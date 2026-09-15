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


_BASE_ANALYZER = q6.analyze_static_v6
_BASE_RENDERER = q4.render_portrait_clip


@dataclass
class VisualShot:
    start: float
    end: float
    center_x: float
    kind: str
    secondary_x: float | None = None
    zoom: float = 1.0
    intensity: float = 0.0


@dataclass
class VisualPlan:
    width: int
    height: int
    shots: list[VisualShot]
    reason: str


def _flag(name: str, default: bool = True) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


def _visual_attention_enabled() -> bool:
    return _flag("VISUAL_ATTENTION_ENGINE", True)


def _split_enabled() -> bool:
    return _flag("SMART_SPLIT_SCREEN", True)


def _reaction_enabled() -> bool:
    return _flag("REACTION_EMPHASIS", True)


def _reaction_words(segments: list[dict], absolute_time: float) -> bool:
    text = " ".join(
        str(s.get("text") or "").lower()
        for s in segments
        if float(s.get("end", 0)) >= absolute_time - 1.5
        and float(s.get("start", 0)) <= absolute_time + 1.5
    )
    cues = (
        "haha", "hahaha", "laugh", "laughing", "wow", "whoa", "oh my god", "no way", "really?",
        "ahah", "kkkk", "risos", "rindo", "uau", "meu deus", "não acredito", "nao acredito", "sério?", "serio?",
    )
    return any(cue in text for cue in cues)


def _face_motion(frame_a, frame_b, face) -> float:
    import cv2

    x, y, w, h = [int(v) for v in face]
    gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
    fh, fw = gray_a.shape[:2]
    x1, x2 = max(0, x), min(fw, x + w)
    y1, y2 = max(0, y), min(fh, y + h)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    a = gray_a[y1:y2, x1:x2]
    b = gray_b[y1:y2, x1:x2]
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return 0.0
    return float(np.mean(cv2.absdiff(a, b)))


def _reaction_signal(
    video: Path,
    abs_start: float,
    abs_end: float,
    primary_x: float,
    crop_w: int,
) -> tuple[float | None, float, float | None, int]:
    """Find a persistent second face and estimate whether it is visibly reacting."""
    import cv2

    if abs_end - abs_start < 1.2:
        return None, 0.0, None, 0
    frontal, profile = q6._cascades()
    cap = cv2.VideoCapture(str(video))
    candidates: list[tuple[float, float, float]] = []
    try:
        for frac in (0.18, 0.34, 0.50, 0.66, 0.82):
            t = abs_start + (abs_end - abs_start) * frac
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_a, frame_a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.16) * 1000)
            ok_b, frame_b = cap.read()
            if not ok_a or not ok_b:
                continue
            faces = q4._detect_faces(frame_a, frontal, profile)
            if len(faces) < 2:
                continue
            scored = q4._activity_scores(frame_a, frame_b, frontal, profile)
            secondary = []
            for face in faces:
                x, y, w, h = face
                center = float(x + w / 2)
                if abs(center - primary_x) <= crop_w * 0.22:
                    continue
                general = _face_motion(frame_a, frame_b, face)
                mouth = 0.0
                if scored:
                    nearest = min(scored, key=lambda item: abs(float(item[1]) - center))
                    if abs(float(nearest[1]) - center) <= max(35.0, w * 0.65):
                        mouth = float(nearest[0])
                area_ratio = float(w * h) / max(1.0, frame_a.shape[0] * frame_a.shape[1])
                reaction = general * 0.72 + mouth * 0.72 + min(4.0, area_ratio * 90.0)
                secondary.append((reaction, center))
            if secondary:
                reaction, center = max(secondary, key=lambda item: item[0])
                candidates.append((reaction, center, t))
    finally:
        cap.release()

    if not candidates:
        return None, 0.0, None, 0
    centers = [item[1] for item in candidates]
    stable_center = float(np.median(centers))
    stable = [item for item in candidates if abs(item[1] - stable_center) <= crop_w * 0.20]
    if not stable:
        stable = candidates
    strongest = max(stable, key=lambda item: item[0])
    score = float(np.median([item[0] for item in stable]) * 0.55 + strongest[0] * 0.45)
    return stable_center, score, strongest[2], len(stable)


def _subject_motion_score(video: Path, abs_start: float, abs_end: float, center_x: float, crop_w: int) -> float:
    import cv2

    frontal, profile = q6._cascades()
    cap = cv2.VideoCapture(str(video))
    values: list[float] = []
    try:
        for frac in (0.25, 0.5, 0.75):
            t = abs_start + max(0.0, abs_end - abs_start) * frac
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_a, a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.16) * 1000)
            ok_b, b = cap.read()
            if not ok_a or not ok_b:
                continue
            faces = q4._detect_faces(a, frontal, profile)
            if not faces:
                continue
            face = min(faces, key=lambda f: abs((f[0] + f[2] / 2) - center_x))
            if abs((face[0] + face[2] / 2) - center_x) <= crop_w * 0.30:
                values.append(_face_motion(a, b, face))
    finally:
        cap.release()
    return float(np.median(values)) if values else 0.0


def _visual_motion_score(video: Path, abs_start: float, abs_end: float) -> float:
    import cv2

    cap = cv2.VideoCapture(str(video))
    values: list[float] = []
    try:
        for frac in (0.18, 0.38, 0.58, 0.78):
            t = abs_start + max(0.0, abs_end - abs_start) * frac
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_a, a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.20) * 1000)
            ok_b, b = cap.read()
            if not ok_a or not ok_b:
                continue
            ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (160, 90))
            gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (160, 90))
            values.append(float(np.mean(cv2.absdiff(ga, gb))))
    finally:
        cap.release()
    return float(np.median(values)) if values else 99.0


def _narrative_boundary(
    segments: list[dict], clip_start: float, low: float, target: float, high: float
) -> float:
    options = [
        float(s.get("end", 0)) - clip_start
        for s in segments
        if low <= float(s.get("end", 0)) - clip_start <= high
    ]
    return min(options, key=lambda x: abs(x - target)) if options else target


def _append_piece(parts: list[VisualShot], start: float, end: float, center: float, kind: str,
                  secondary: float | None = None, zoom: float = 1.0, intensity: float = 0.0) -> None:
    if end - start >= 0.28:
        parts.append(VisualShot(start, end, center, kind, secondary, zoom, intensity))


def analyze_visual_v9(video: Path, plan: q2.ClipPlan, transcript_segments: list[dict]) -> VisualPlan:
    base = _BASE_ANALYZER(video, plan, transcript_segments)
    width, height = base.width, base.height
    crop_w = min(width, max(2, int(height * 9 / 16)))
    duration = max(0.1, plan.end - plan.start)
    shots: list[VisualShot] = []
    split_count = 0
    reaction_count = 0
    attention_count = 0
    last_intervention_end = -99.0
    max_split = 2 if duration >= 45 else 1
    max_reaction = 3 if duration >= 70 else 2
    min_gap = 5.0

    for base_shot in base.shots:
        a, b = float(base_shot.start), float(base_shot.end)
        center = float(base_shot.center_x)
        shot_duration = b - a
        abs_a, abs_b = plan.start + a, plan.start + b

        # Existing reaction shots from v6 become a stronger close-up only when the
        # listener is actually moving enough to justify it.
        if base_shot.kind == "reaction" and _reaction_enabled():
            motion = _subject_motion_score(video, abs_a, abs_b, center, crop_w)
            if motion >= 5.8 or _reaction_words(transcript_segments, (abs_a + abs_b) / 2):
                shots.append(VisualShot(a, b, center, "reaction_emphasis", None, 0.84, motion))
                reaction_count += 1
                last_intervention_end = b
                continue

        intervention_done = False
        if (
            q6.CURRENT_PROFILE == "podcast"
            and base_shot.kind in {"speaker", "fallback"}
            and shot_duration >= 3.8
            and a - last_intervention_end >= min_gap
            and (reaction_count < max_reaction or split_count < max_split)
        ):
            secondary, reaction_score, event_abs, persistence = _reaction_signal(
                video, abs_a, abs_b, center, crop_w
            )
            cue = bool(event_abs is not None and _reaction_words(transcript_segments, event_abs))
            if secondary is not None and event_abs is not None and persistence >= 2:
                event = max(a + 0.7, min(b - 0.7, event_abs - plan.start))

                # Strong listener reactions deserve a short close-up, then we return to the speaker.
                if _reaction_enabled() and reaction_count < max_reaction and (reaction_score >= 10.5 or cue):
                    length = 1.15
                    r_start = max(a, min(event - 0.35, b - length))
                    r_end = min(b, r_start + length)
                    _append_piece(shots, a, r_start, center, "speaker")
                    _append_piece(shots, r_start, r_end, secondary, "reaction_emphasis", None, 0.82, reaction_score)
                    _append_piece(shots, r_end, b, center, "speaker")
                    reaction_count += 1
                    last_intervention_end = r_end
                    intervention_done = True

                # Moderate, persistent reactions work better as a temporary speaker/listener split-screen.
                elif _split_enabled() and split_count < max_split and reaction_score >= 5.2:
                    length = min(2.35, max(1.55, shot_duration * 0.34))
                    s_start = max(a, min(event - length * 0.42, b - length))
                    s_end = min(b, s_start + length)
                    _append_piece(shots, a, s_start, center, "speaker")
                    _append_piece(shots, s_start, s_end, center, "split", secondary, 1.0, reaction_score)
                    _append_piece(shots, s_end, b, center, "speaker")
                    split_count += 1
                    last_intervention_end = s_end
                    intervention_done = True

        if intervention_done:
            continue

        # Visual Attention Engine: if a shot stays visually static for too long,
        # introduce one static punch-in aligned near a speech boundary. No sliding camera.
        stale_threshold = {"podcast": 6.4, "talking": 7.2, "film": 10.0}.get(q6.CURRENT_PROFILE, 99.0)
        if (
            _visual_attention_enabled()
            and q6.CURRENT_PROFILE != "gameplay"
            and base_shot.kind in {"speaker", "fallback", "scene"}
            and shot_duration >= stale_threshold
            and a - last_intervention_end >= min_gap
        ):
            motion = _visual_motion_score(video, abs_a, abs_b)
            if motion <= 5.6:
                desired = a + shot_duration * 0.62
                low = a + max(2.0, shot_duration * 0.48)
                high = min(b - 1.4, a + shot_duration * 0.78)
                cut = _narrative_boundary(transcript_segments, plan.start, low, desired, high) if high > low else desired
                cut = max(a + 1.6, min(b - 1.2, cut))
                _append_piece(shots, a, cut, center, base_shot.kind)
                _append_piece(shots, cut, b, center, "attention_punch", None, 0.90, max(0.0, 5.6 - motion))
                attention_count += 1
                last_intervention_end = b
                continue

        shots.append(VisualShot(a, b, center, base_shot.kind))

    if not shots:
        shots = [VisualShot(0.0, duration, width / 2, "fallback")]

    reason = (
        f"Visual Attention v9 · {attention_count} mudança(s) por estagnação · "
        f"{split_count} split-screen · {reaction_count} reação(ões) enfatizada(s) · {base.reason}"
    )
    return VisualPlan(width, height, shots, reason)


def _portrait_transform(width: int, height: int, center_x: float, zoom: float = 1.0) -> str:
    target_ratio = 9 / 16
    source_ratio = width / height
    if source_ratio >= target_ratio:
        zoom = max(0.78, min(1.0, zoom))
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


def _panel_transform(width: int, height: int, center_x: float) -> str:
    panel_ratio = 1080 / 960
    if width / height >= panel_ratio:
        crop_w = max(2, int(height * panel_ratio))
        crop_w -= crop_w % 2
        x = int(max(0, min(width - crop_w, center_x - crop_w / 2)))
        return f"crop={crop_w}:{height}:{x}:0,scale=1080:960,setsar=1"
    return "scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1"


def render_visual_v9(
    video: Path, plan: q2.ClipPlan, segments: list[dict], ass: Path, output: Path, idx: int, total: int
) -> VisualPlan:
    framing = analyze_visual_v9(video, plan, segments)
    width, height = framing.width, framing.height
    escaped = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    chains: list[str] = []
    labels = "".join(f"[src{i}]" for i in range(len(framing.shots)))
    chains.append(f"[0:v]split={len(framing.shots)}{labels}")

    for i, shot in enumerate(framing.shots):
        if shot.kind == "split" and shot.secondary_x is not None:
            top = _panel_transform(width, height, shot.center_x)
            bottom = _panel_transform(width, height, shot.secondary_x)
            chains.append(f"[src{i}]split=2[s{i}a][s{i}b]")
            chains.append(
                f"[s{i}a]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{top}[top{i}]"
            )
            chains.append(
                f"[s{i}b]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{bottom}[bot{i}]"
            )
            chains.append(
                f"[top{i}][bot{i}]vstack=inputs=2,drawbox=x=0:y=956:w=1080:h=8:color=white@0.28:t=fill[v{i}]"
            )
        else:
            transform = _portrait_transform(width, height, shot.center_x, shot.zoom)
            chains.append(
                f"[src{i}]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{transform}[v{i}]"
            )

    concat_inputs = "".join(f"[v{i}]" for i in range(len(framing.shots)))
    chains.append(f"{concat_inputs}concat=n={len(framing.shots)}:v=1:a=0[cutv]")
    chains.append(f"[cutv]subtitles='{escaped}'[outv]")
    filter_complex = ";".join(chains)

    duration = max(0.1, plan.end - plan.start)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{plan.start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
        "-filter_complex", filter_complex, "-map", "[outv]", "-map", "0:a?",
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
        raise RuntimeError(f"FFmpeg v9 falhou: {stderr[-2800:]}")

    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(output),
    ], text=True).strip()
    if probe != "1080x1920":
        raise RuntimeError(f"Saída v9 deveria ser 1080x1920, mas FFprobe encontrou {probe!r}.")
    return framing


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_analyzer = q6.analyze_static_v6
    original_renderer = q4.render_portrait_clip
    try:
        q6.analyze_static_v6 = analyze_visual_v9
        q4.render_portrait_clip = render_visual_v9
        autoclip.log(
            "Quality v9: Visual Attention={} · split-screen={} · reaction emphasis={}".format(
                "on" if _visual_attention_enabled() else "off",
                "on" if _split_enabled() else "off",
                "on" if _reaction_enabled() else "off",
            )
        )
        q8.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Visual Director v9\n")
        autoclip.summary(
            "Visual Attention Engine: **{}** · Split-screen inteligente: **{}** · Reaction emphasis: **{}**.\n".format(
                "ativado" if _visual_attention_enabled() else "desativado",
                "ativado" if _split_enabled() else "desativado",
                "ativado" if _reaction_enabled() else "desativado",
            )
        )
        autoclip.summary(
            "As intervenções têm orçamento visual: o sistema mantém planos estáticos, cria mudanças apenas após intervalos mínimos, "
            "limita split-screens/reactions e nunca usa movimento contínuo de câmera.\n"
        )
    finally:
        q6.analyze_static_v6 = original_analyzer
        q4.render_portrait_clip = original_renderer
