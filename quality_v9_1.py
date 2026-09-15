from __future__ import annotations

import os
from pathlib import Path

import numpy as np

import autoclip
import quality_v4 as q4
import quality_v6 as q6
import quality_v6_1 as q61
import quality_v8 as q8
import quality_v9 as q9


_ORIGINAL_ANALYZE = q9.analyze_visual_v9
_ORIGINAL_PORTRAIT_TRANSFORM = q9._portrait_transform
_ORIGINAL_PANEL_TRANSFORM = q9._panel_transform
_ORIGINAL_V8_WRITER = q8.write_ass_v8


def _stable_face_center(
    video: Path,
    abs_start: float,
    abs_end: float,
    intended_x: float,
    crop_w: int,
    mode: str,
) -> float:
    """Track the intended person across the shot and return a stable face center."""
    import cv2

    frontal, profile = q6._cascades()
    cap = cv2.VideoCapture(str(video))
    centers: list[float] = []
    weights: list[float] = []
    try:
        for frac in (0.10, 0.24, 0.38, 0.52, 0.66, 0.80, 0.92):
            t = abs_start + max(0.0, abs_end - abs_start) * frac
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_a, frame_a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.13) * 1000)
            ok_b, frame_b = cap.read()
            if not ok_a:
                continue
            faces = q4._detect_faces(frame_a, frontal, profile)
            if not faces:
                continue

            scored = q4._activity_scores(frame_a, frame_b, frontal, profile) if ok_b else []
            candidates: list[tuple[float, float, float]] = []
            frame_area = max(1.0, float(frame_a.shape[0] * frame_a.shape[1]))
            for x, y, w, h in faces:
                center = float(x + w / 2)
                distance = abs(center - intended_x) / max(1.0, crop_w)
                area = float(w * h) / frame_area
                activity = 0.0
                if scored:
                    nearest = min(scored, key=lambda item: abs(float(item[1]) - center))
                    if abs(float(nearest[1]) - center) <= max(35.0, w * 0.70):
                        activity = float(nearest[0])

                if mode in {"speaker", "attention_punch", "fallback", "scene"}:
                    score = activity * 0.65 + area * 120.0 - distance * 8.0
                else:
                    # Reaction/split subjects are already selected by the visual director;
                    # stay close to that identity rather than jumping to the current speaker.
                    score = activity * 0.20 + area * 70.0 - distance * 14.0
                candidates.append((score, center, area))

            if not candidates:
                continue
            score, center, area = max(candidates, key=lambda item: item[0])
            if abs(center - intended_x) <= crop_w * 0.78 or not centers:
                centers.append(center)
                weights.append(max(0.25, 1.0 + area * 18.0 + max(0.0, score) * 0.05))
    finally:
        cap.release()

    if not centers:
        return intended_x

    # Reject occasional jumps to another person and use a weighted center of the stable track.
    median = float(np.median(centers))
    keep = [(c, w) for c, w in zip(centers, weights) if abs(c - median) <= crop_w * 0.23]
    if not keep:
        keep = list(zip(centers, weights))
    numerator = sum(c * w for c, w in keep)
    denominator = sum(w for _, w in keep)
    refined = numerator / max(1e-6, denominator)

    # Blend lightly with the old editorial target to avoid sudden identity switches.
    return 0.88 * refined + 0.12 * intended_x


def _zoom_for_true_center(width: int, height: int, center_x: float, base_zoom: float) -> float:
    """Slightly tighten the crop when a person is near a source edge so they can remain centered."""
    nominal_crop_w = max(2.0, height * 9 / 16)
    edge_room = max(1.0, 2.0 * min(center_x, width - center_x))
    centerable = edge_room / nominal_crop_w
    target = min(base_zoom, centerable * 0.97)
    return max(0.74, min(base_zoom, target))


def _portrait_transform_v9_1(width: int, height: int, center_x: float, zoom: float = 1.0) -> str:
    target_ratio = 9 / 16
    source_ratio = width / height
    if source_ratio >= target_ratio:
        zoom = max(0.72, min(1.0, zoom))
        crop_h = max(2, int(height * zoom))
        crop_h -= crop_h % 2
        crop_w = max(2, int(crop_h * target_ratio))
        crop_w -= crop_w % 2
        if crop_w > width:
            crop_w = width - (width % 2)
            crop_h = max(2, int(crop_w / target_ratio))
            crop_h -= crop_h % 2
        x = int(max(0, min(width - crop_w, center_x - crop_w / 2)))
        y = int(max(0, min(height - crop_h, (height - crop_h) / 2)))
        return f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:1920,setsar=1"
    return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"


def _panel_transform_v9_1(width: int, height: int, center_x: float) -> str:
    """Center the selected person inside a split-screen panel, even near the source edge."""
    panel_ratio = 1080 / 960
    if width / height >= panel_ratio:
        nominal_w = height * panel_ratio
        edge_room = max(1.0, 2.0 * min(center_x, width - center_x))
        zoom = max(0.68, min(1.0, (edge_room / max(1.0, nominal_w)) * 0.97))
        crop_h = max(2, int(height * zoom))
        crop_h -= crop_h % 2
        crop_w = max(2, int(crop_h * panel_ratio))
        crop_w -= crop_w % 2
        if crop_w > width:
            crop_w = width - (width % 2)
            crop_h = max(2, int(crop_w / panel_ratio))
            crop_h -= crop_h % 2
        x = int(max(0, min(width - crop_w, center_x - crop_w / 2)))
        y = int(max(0, min(height - crop_h, (height - crop_h) / 2)))
        return f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:960,setsar=1"
    return "scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1"


def _expand_existing_splits(shots: list[q9.VisualShot]) -> list[q9.VisualShot]:
    """Let useful split-screens breathe longer by borrowing a little time from adjacent speaker shots."""
    out = [q9.VisualShot(s.start, s.end, s.center_x, s.kind, s.secondary_x, s.zoom, s.intensity) for s in shots]
    for i, shot in enumerate(out):
        if shot.kind != "split":
            continue
        current = shot.end - shot.start
        target = min(4.2, max(2.8, current * 1.55))
        need = max(0.0, target - current)
        if need <= 0.05:
            continue

        if i > 0 and out[i - 1].kind in {"speaker", "fallback", "attention_punch"}:
            prev = out[i - 1]
            available = max(0.0, (prev.end - prev.start) - 0.85)
            take = min(available, need * 0.55)
            if take > 0:
                prev.end -= take
                shot.start -= take
                need -= take

        if i + 1 < len(out) and out[i + 1].kind in {"speaker", "fallback", "attention_punch"}:
            nxt = out[i + 1]
            available = max(0.0, (nxt.end - nxt.start) - 0.85)
            take = min(available, need)
            if take > 0:
                nxt.start += take
                shot.end += take

    return [s for s in out if s.end - s.start >= 0.28]


def _extra_split_screens(
    video: Path,
    plan: q2.ClipPlan,
    segments: list[dict],
    shots: list[q9.VisualShot],
    width: int,
    height: int,
) -> tuple[list[q9.VisualShot], int]:
    if not q9._split_enabled() or q6.CURRENT_PROFILE != "podcast":
        return shots, 0

    duration = max(0.1, plan.end - plan.start)
    if duration < 35:
        max_split = 2
    elif duration < 70:
        max_split = 3
    elif duration < 110:
        max_split = 4
    else:
        max_split = 5

    existing = sum(1 for s in shots if s.kind == "split")
    if existing >= max_split:
        return shots, 0

    crop_w = min(width, max(2, int(height * 9 / 16)))
    interventions = [(s.start, s.end) for s in shots if s.kind in {"split", "reaction_emphasis"}]
    result: list[q9.VisualShot] = []
    added = 0

    for shot in shots:
        if existing + added >= max_split:
            result.append(shot)
            continue
        if shot.kind not in {"speaker", "fallback"} or shot.end - shot.start < 4.4:
            result.append(shot)
            continue
        if any(max(0.0, min(shot.end, e) - max(shot.start, a)) > 0.15 for a, e in interventions):
            result.append(shot)
            continue
        if any(abs(shot.start - e) < 3.8 or abs(shot.end - a) < 3.8 for a, e in interventions):
            result.append(shot)
            continue

        abs_a, abs_b = plan.start + shot.start, plan.start + shot.end
        secondary, reaction_score, event_abs, persistence = q9._reaction_signal(
            video, abs_a, abs_b, shot.center_x, crop_w
        )
        if secondary is None or event_abs is None or persistence < 2 or reaction_score < 4.4:
            result.append(shot)
            continue

        length = min(4.1, max(2.7, (shot.end - shot.start) * 0.54))
        event = max(shot.start + 0.8, min(shot.end - 0.8, event_abs - plan.start))
        s_start = max(shot.start, min(event - length * 0.45, shot.end - length))
        s_end = min(shot.end, s_start + length)
        if s_end - s_start < 2.4:
            result.append(shot)
            continue

        q9._append_piece(result, shot.start, s_start, shot.center_x, shot.kind, None, shot.zoom, shot.intensity)
        q9._append_piece(result, s_start, s_end, shot.center_x, "split", secondary, 1.0, reaction_score)
        q9._append_piece(result, s_end, shot.end, shot.center_x, shot.kind, None, shot.zoom, shot.intensity)
        interventions.append((s_start, s_end))
        added += 1

    return result, added


def analyze_visual_v9_1(video: Path, plan: q2.ClipPlan, transcript_segments: list[dict]) -> q9.VisualPlan:
    visual = _ORIGINAL_ANALYZE(video, plan, transcript_segments)
    width, height = visual.width, visual.height
    crop_w = min(width, max(2, int(height * 9 / 16)))

    shots = _expand_existing_splits(visual.shots)
    shots, added_splits = _extra_split_screens(video, plan, transcript_segments, shots, width, height)

    refined: list[q9.VisualShot] = []
    for shot in shots:
        abs_a = plan.start + shot.start
        abs_b = plan.start + shot.end
        mode = shot.kind
        primary = _stable_face_center(video, abs_a, abs_b, shot.center_x, crop_w, mode)
        primary = max(0.0, min(float(width), primary))

        secondary = shot.secondary_x
        if shot.kind == "split" and secondary is not None:
            secondary = _stable_face_center(video, abs_a, abs_b, secondary, crop_w, "split_secondary")
            secondary = max(0.0, min(float(width), secondary))

        zoom = shot.zoom
        if shot.kind != "split":
            zoom = _zoom_for_true_center(width, height, primary, zoom)

        refined.append(
            q9.VisualShot(
                shot.start,
                shot.end,
                primary,
                shot.kind,
                secondary,
                zoom,
                shot.intensity,
            )
        )

    split_count = sum(1 for s in refined if s.kind == "split")
    reason = (
        visual.reason
        + f" · enquadramento facial refinado · {split_count} split-screen(s) com duração ampliada"
        + (f" ({added_splits} adicional(is))" if added_splits else "")
    )
    return q9.VisualPlan(width, height, refined, reason)


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_analyze = q9.analyze_visual_v9
    original_portrait = q9._portrait_transform
    original_panel = q9._panel_transform
    original_v8_writer = q8.write_ass_v8
    old_emphasis_env = os.environ.get("SMART_SUBTITLE_EMPHASIS")
    try:
        # User preference: plain white subtitles only; no automatic highlighted words.
        os.environ["SMART_SUBTITLE_EMPHASIS"] = "false"
        q8.write_ass_v8 = q61.write_ass_v6_1

        q9.analyze_visual_v9 = analyze_visual_v9_1
        q9._portrait_transform = _portrait_transform_v9_1
        q9._panel_transform = _panel_transform_v9_1

        autoclip.log(
            "Quality v9.1: destaque de legenda removido · centralização facial refinada · split-screen ampliado"
        )
        q9.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Ajustes v9.1\n")
        autoclip.summary(
            "Legendas: **sem destaque automático** · enquadramento: **rastreamento facial refinado** · "
            "split-screen: **mais longo e moderadamente mais frequente**.\n"
        )
    finally:
        q9.analyze_visual_v9 = original_analyze
        q9._portrait_transform = original_portrait
        q9._panel_transform = original_panel
        q8.write_ass_v8 = original_v8_writer
        if old_emphasis_env is None:
            os.environ.pop("SMART_SUBTITLE_EMPHASIS", None)
        else:
            os.environ["SMART_SUBTITLE_EMPHASIS"] = old_emphasis_env
