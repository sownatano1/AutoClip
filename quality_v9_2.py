from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import autoclip
import quality_v2 as q2
import quality_v4 as q4
import quality_v6 as q6
import quality_v8 as q8
import quality_v9 as q9
import quality_v9_1 as q91


_BASE_VISUAL_ANALYZER = q9.analyze_visual_v9


@dataclass
class FocusBox:
    x: float
    y: float
    w: float
    h: float
    confidence: float = 0.0


@dataclass
class Shot92:
    start: float
    end: float
    kind: str
    primary: FocusBox
    secondary: FocusBox | None = None
    zoom: float = 1.0
    intensity: float = 0.0


@dataclass
class Plan92:
    width: int
    height: int
    shots: list[Shot92]
    reason: str


@dataclass
class SplitCandidate:
    start: float
    end: float
    primary: FocusBox
    secondary: FocusBox
    score: float


def _iou(a, b) -> float:
    ax, ay, aw, ah = [float(v) for v in a]
    bx, by, bw, bh = [float(v) for v in b]
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _activity_for_face(scored, face) -> float:
    if not scored:
        return 0.0
    x, y, w, h = face
    cx = float(x + w / 2)
    nearest = min(scored, key=lambda item: abs(float(item[1]) - cx))
    if abs(float(nearest[1]) - cx) <= max(36.0, float(w) * 0.72):
        return float(nearest[0])
    return 0.0


def _distinct_faces(primary, candidate, frame_w: int) -> bool:
    px, py, pw, ph = primary
    cx, cy, cw, ch = candidate
    pc = float(px + pw / 2)
    cc = float(cx + cw / 2)
    min_sep = max(frame_w * 0.10, (float(pw) + float(cw)) * 0.58)
    return abs(pc - cc) >= min_sep and _iou(primary, candidate) <= 0.12


def _choose_primary(faces, scored, intended_x: float, frame_w: int, frame_h: int, mode: str):
    if not faces:
        return None
    area_total = max(1.0, float(frame_w * frame_h))
    ranked = []
    for face in faces:
        x, y, w, h = face
        cx = float(x + w / 2)
        area = float(w * h) / area_total
        dist = abs(cx - intended_x) / max(1.0, frame_w)
        activity = _activity_for_face(scored, face)
        if mode in {"speaker", "fallback", "scene", "attention_punch"}:
            score = activity * 0.72 + area * 115.0 - dist * 16.0
        else:
            score = activity * 0.18 + area * 85.0 - dist * 24.0
        ranked.append((score, face))
    return max(ranked, key=lambda item: item[0])[1]


def _box_from_faces(faces: list[tuple[int, int, int, int]], fallback_x: float, fallback_y: float) -> FocusBox:
    if not faces:
        return FocusBox(fallback_x, fallback_y, 0.0, 0.0, 0.0)
    xs = np.array([f[0] + f[2] / 2 for f in faces], dtype=np.float32)
    ys = np.array([f[1] + f[3] / 2 for f in faces], dtype=np.float32)
    ws = np.array([f[2] for f in faces], dtype=np.float32)
    hs = np.array([f[3] for f in faces], dtype=np.float32)
    mx, my = float(np.median(xs)), float(np.median(ys))
    mw, mh = float(np.median(ws)), float(np.median(hs))
    keep = [f for f in faces if abs((f[0] + f[2] / 2) - mx) <= max(45.0, mw * 0.8)]
    if keep:
        mx = float(np.mean([f[0] + f[2] / 2 for f in keep]))
        my = float(np.mean([f[1] + f[3] / 2 for f in keep]))
        mw = float(np.median([f[2] for f in keep]))
        mh = float(np.median([f[3] for f in keep]))
    return FocusBox(mx, my, mw, mh, min(1.0, len(keep or faces) / 5.0))


def _track_focus(video: Path, abs_start: float, abs_end: float, intended_x: float, mode: str) -> FocusBox:
    import cv2

    frontal, profile = q6._cascades()
    cap = cv2.VideoCapture(str(video))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    picked = []
    try:
        for frac in np.linspace(0.08, 0.92, 9):
            t = abs_start + max(0.0, abs_end - abs_start) * float(frac)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_a, a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.13) * 1000)
            ok_b, b = cap.read()
            if not ok_a:
                continue
            faces = q4._detect_faces(a, frontal, profile)
            scored = q4._activity_scores(a, b, frontal, profile) if ok_b else []
            face = _choose_primary(faces, scored, intended_x, width, height, mode)
            if face is not None:
                picked.append(face)
    finally:
        cap.release()
    return _box_from_faces(picked, intended_x, height * 0.38)


def _dual_samples(video: Path, abs_start: float, abs_end: float, intended_x: float):
    """Sample primary+secondary from the SAME frames, preventing duplicate-face split screens."""
    import cv2

    frontal, profile = q6._cascades()
    cap = cv2.VideoCapture(str(video))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = max(0.1, abs_end - abs_start)
    step = 0.52
    samples = []
    t = abs_start + min(0.35, duration * 0.12)
    try:
        while t <= abs_end - 0.18:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_a, a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.14) * 1000)
            ok_b, b = cap.read()
            if not ok_a:
                t += step
                continue
            faces = q4._detect_faces(a, frontal, profile)
            scored = q4._activity_scores(a, b, frontal, profile) if ok_b else []
            primary = _choose_primary(faces, scored, intended_x, width, height, "speaker")
            if primary is None:
                samples.append((t, None, None, 0.0))
                t += step
                continue

            alternatives = []
            for face in faces:
                if face == primary or not _distinct_faces(primary, face, width):
                    continue
                activity = _activity_for_face(scored, face)
                x, y, w, h = face
                area = float(w * h) / max(1.0, width * height)
                alternatives.append((activity * 0.55 + area * 70.0, face))
            if not alternatives:
                samples.append((t, primary, None, 0.0))
            else:
                score, secondary = max(alternatives, key=lambda item: item[0])
                samples.append((t, primary, secondary, float(score)))
            t += step
    finally:
        cap.release()
    return samples, width, height, step


def _adaptive_split_runs(video: Path, plan: q2.ClipPlan, shot, transcript_segments: list[dict]) -> list[SplitCandidate]:
    abs_start, abs_end = plan.start + shot.start, plan.start + shot.end
    samples, width, height, step = _dual_samples(video, abs_start, abs_end, float(shot.center_x))
    valid = []
    for t, primary, secondary, score in samples:
        cue = q9._reaction_words(transcript_segments, t)
        valid.append((t, primary, secondary, score, bool(secondary is not None and (score >= 1.5 or cue))))

    groups = []
    current = []
    misses = 0
    for row in valid:
        if row[4]:
            current.append(row)
            misses = 0
        elif current:
            misses += 1
            if misses > 1:
                if len(current) >= 3:
                    groups.append(current)
                current = []
                misses = 0
    if len(current) >= 3:
        groups.append(current)

    out = []
    for group in groups:
        pfaces = [r[1] for r in group if r[1] is not None]
        sfaces = [r[2] for r in group if r[2] is not None]
        if len(pfaces) < 3 or len(sfaces) < 3:
            continue
        pbox = _box_from_faces(pfaces, float(shot.center_x), height * 0.38)
        sbox = _box_from_faces(sfaces, width - pbox.x, height * 0.38)
        if abs(pbox.x - sbox.x) < max(width * 0.11, (pbox.w + sbox.w) * 0.62):
            continue
        start = max(float(shot.start), group[0][0] - plan.start - step * 0.65)
        end = min(float(shot.end), group[-1][0] - plan.start + step * 0.90)
        if end - start < 1.35:
            continue
        score = float(np.median([r[3] for r in group]) + 0.4 * len(group))
        out.append(SplitCandidate(start, end, pbox, sbox, score))
    return out


def _select_splits(candidates: list[SplitCandidate], duration: float) -> list[SplitCandidate]:
    """No fixed per-split duration: start/end follow dual-person relevance. Only total usage is budgeted."""
    if not candidates:
        return []
    budget = duration * 0.46
    chosen = []
    used = 0.0
    for cand in sorted(candidates, key=lambda c: c.score, reverse=True):
        if len(chosen) >= 5:
            break
        if any(max(0.0, min(cand.end, c.end) - max(cand.start, c.start)) > 0.15 for c in chosen):
            continue
        length = cand.end - cand.start
        if used + length > budget and chosen:
            continue
        chosen.append(cand)
        used += length
    return sorted(chosen, key=lambda c: c.start)


def _split_shot_by_events(shot, focus: FocusBox, events: list[SplitCandidate]) -> list[Shot92]:
    if not events:
        return [Shot92(float(shot.start), float(shot.end), str(shot.kind), focus, None, float(shot.zoom), float(shot.intensity))]
    out = []
    cursor = float(shot.start)
    base_kind = "speaker" if shot.kind == "split" else str(shot.kind)
    for event in events:
        if event.start > cursor + 0.25:
            out.append(Shot92(cursor, event.start, base_kind, focus, None, float(shot.zoom), float(shot.intensity)))
        out.append(Shot92(event.start, event.end, "split", event.primary, event.secondary, 1.0, event.score))
        cursor = event.end
    if float(shot.end) > cursor + 0.25:
        out.append(Shot92(cursor, float(shot.end), base_kind, focus, None, float(shot.zoom), float(shot.intensity)))
    return out


def analyze_v9_2(video: Path, plan: q2.ClipPlan, transcript_segments: list[dict]) -> Plan92:
    base = _BASE_VISUAL_ANALYZER(video, plan, transcript_segments)
    width, height = base.width, base.height
    duration = max(0.1, plan.end - plan.start)

    prepared = []
    split_candidates = []
    for shot in base.shots:
        base_kind = "speaker" if shot.kind == "split" else str(shot.kind)
        focus = _track_focus(
            video,
            plan.start + float(shot.start),
            plan.start + float(shot.end),
            float(shot.center_x),
            base_kind,
        )
        prepared.append((shot, focus, base_kind))
        if (
            q6.CURRENT_PROFILE == "podcast"
            and q9._split_enabled()
            and base_kind in {"speaker", "fallback", "attention_punch"}
            and float(shot.end) - float(shot.start) >= 2.0
        ):
            split_candidates.extend(_adaptive_split_runs(video, plan, shot, transcript_segments))

    selected = _select_splits(split_candidates, duration)
    shots: list[Shot92] = []
    for shot, focus, base_kind in prepared:
        local = [e for e in selected if e.start >= float(shot.start) - 0.02 and e.end <= float(shot.end) + 0.02]
        proxy = type("Proxy", (), {
            "start": float(shot.start), "end": float(shot.end), "kind": base_kind,
            "zoom": float(shot.zoom), "intensity": float(shot.intensity)
        })()
        shots.extend(_split_shot_by_events(proxy, focus, local))

    # Merge consecutive adaptive split segments when the same two distinct people remain on screen.
    merged: list[Shot92] = []
    for shot in shots:
        if (
            merged and shot.kind == "split" and merged[-1].kind == "split"
            and abs(shot.start - merged[-1].end) < 0.08
            and shot.secondary is not None and merged[-1].secondary is not None
            and abs(shot.primary.x - merged[-1].primary.x) < width * 0.09
            and abs(shot.secondary.x - merged[-1].secondary.x) < width * 0.09
        ):
            merged[-1].end = shot.end
        else:
            merged.append(shot)

    split_count = sum(1 for s in merged if s.kind == "split")
    split_seconds = sum(s.end - s.start for s in merged if s.kind == "split")
    reason = (
        base.reason
        + f" · enquadramento por caixa facial/eye-line · split adaptativo {split_count}x/{split_seconds:.1f}s"
        + " · identidades do split validadas no mesmo frame"
    )
    return Plan92(width, height, merged, reason)


def _portrait_transform(width: int, height: int, focus: FocusBox, zoom_hint: float, kind: str) -> str:
    target_ratio = 9 / 16
    if width / height < target_ratio:
        return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"

    if focus.h > 1:
        desired_face_fraction = 0.29 if kind in {"reaction_emphasis", "attention_punch"} else 0.25
        crop_h = focus.h / desired_face_fraction
        crop_h = max(height * 0.70, min(float(height), crop_h))
        crop_h = min(crop_h, height * max(0.72, min(1.0, zoom_hint)))
    else:
        crop_h = height * max(0.72, min(1.0, zoom_hint))
    crop_h = max(2, int(crop_h))
    crop_h -= crop_h % 2
    crop_w = max(2, int(crop_h * target_ratio))
    crop_w -= crop_w % 2
    if crop_w > width:
        crop_w = width - (width % 2)
        crop_h = max(2, int(crop_w / target_ratio))
        crop_h -= crop_h % 2

    x = int(max(0, min(width - crop_w, focus.x - crop_w / 2)))
    # Place face center around the upper third rather than blindly centering the source frame vertically.
    target_face_y = crop_h * 0.36
    y = int(max(0, min(height - crop_h, focus.y - target_face_y))) if focus.h > 1 else int(max(0, (height - crop_h) / 2))
    return f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:1920,setsar=1"


def _panel_transform(width: int, height: int, focus: FocusBox) -> str:
    ratio = 1080 / 960
    if width / height < ratio:
        return "scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1"

    if focus.h > 1:
        crop_h = focus.h / 0.34
        crop_h = max(height * 0.54, min(float(height), crop_h))
    else:
        crop_h = float(height)
    crop_h = max(2, int(crop_h))
    crop_h -= crop_h % 2
    crop_w = max(2, int(crop_h * ratio))
    crop_w -= crop_w % 2
    if crop_w > width:
        crop_w = width - (width % 2)
        crop_h = max(2, int(crop_w / ratio))
        crop_h -= crop_h % 2
    x = int(max(0, min(width - crop_w, focus.x - crop_w / 2)))
    y = int(max(0, min(height - crop_h, focus.y - crop_h * 0.40))) if focus.h > 1 else int(max(0, (height - crop_h) / 2))
    return f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:960,setsar=1"


def render_v9_2(video: Path, plan: q2.ClipPlan, segments: list[dict], ass: Path, output: Path, idx: int, total: int) -> Plan92:
    framing = analyze_v9_2(video, plan, segments)
    width, height = framing.width, framing.height
    escaped = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    chains = []
    labels = "".join(f"[src{i}]" for i in range(len(framing.shots)))
    chains.append(f"[0:v]split={len(framing.shots)}{labels}")

    for i, shot in enumerate(framing.shots):
        if shot.kind == "split" and shot.secondary is not None:
            top = _panel_transform(width, height, shot.primary)
            bottom = _panel_transform(width, height, shot.secondary)
            chains.append(f"[src{i}]split=2[s{i}a][s{i}b]")
            chains.append(f"[s{i}a]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{top}[top{i}]")
            chains.append(f"[s{i}b]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{bottom}[bot{i}]")
            chains.append(f"[top{i}][bot{i}]vstack=inputs=2,drawbox=x=0:y=956:w=1080:h=8:color=white@0.28:t=fill[v{i}]")
        else:
            transform = _portrait_transform(width, height, shot.primary, shot.zoom, shot.kind)
            chains.append(f"[src{i}]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{transform}[v{i}]")

    concat_inputs = "".join(f"[v{i}]" for i in range(len(framing.shots)))
    chains.append(f"{concat_inputs}concat=n={len(framing.shots)}:v=1:a=0[cutv]")
    chains.append(f"[cutv]subtitles='{escaped}'[outv]")

    duration = max(0.1, plan.end - plan.start)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{plan.start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
        "-filter_complex", ";".join(chains), "-map", "[outv]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        "-pix_fmt", "yuv420p", "-aspect", "9:16",
        "-progress", "pipe:1", "-nostats", "-loglevel", "error", str(output),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    last = -1
    if proc.stdout:
        for raw in proc.stdout:
            line = raw.strip()
            if line.startswith(("out_time_ms=", "out_time_us=")):
                try:
                    micros = float(line.split("=", 1)[1])
                    pct = int(max(0, min(99, micros / (duration * 1_000_000) * 100)))
                    if pct >= last + 5:
                        last = pct
                        overall = 68 + int(((idx - 1) + pct / 100) / max(1, total) * 18)
                        autoclip.progress("Render", overall, f"Corte {idx}/{total}: {pct}% · {framing.reason}")
                except Exception:
                    pass
    stderr = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"FFmpeg v9.2 falhou: {stderr[-3000:]}")

    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(output),
    ], text=True).strip()
    if probe != "1080x1920":
        raise RuntimeError(f"Saída v9.2 deveria ser 1080x1920, mas encontrou {probe!r}.")
    return framing


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_analyze_91 = q91.analyze_visual_v9_1
    original_render_9 = q9.render_visual_v9
    try:
        q91.analyze_visual_v9_1 = analyze_v9_2
        q9.render_visual_v9 = render_v9_2
        autoclip.log(
            "Quality v9.2: split-screen adaptativo por relevância · identidades distintas · crop por face/eye-line"
        )
        q91.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Ajustes v9.2\n")
        autoclip.summary(
            "Split-screen: **duração adaptativa**, encerrando quando duas pessoas distintas deixam de ser relevantes; "
            "duplicação da mesma pessoa: **bloqueada por validação no mesmo frame**; "
            "enquadramento: **caixa facial + posição vertical do rosto (eye-line)**.\n"
        )
    finally:
        q91.analyze_visual_v9_1 = original_analyze_91
        q9.render_visual_v9 = original_render_9
