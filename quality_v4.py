from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import autoclip
import quality_v2 as q2


@dataclass
class Shot:
    start: float
    end: float
    center_x: float
    kind: str


@dataclass
class ShotPlan:
    width: int
    height: int
    shots: list[Shot]
    reason: str


def _caption_chunks(text: str, max_words: int = 4, max_chars: int = 24) -> list[str]:
    tokens = re.sub(r"\s+", " ", text).strip().split()
    chunks: list[str] = []
    current: list[str] = []
    for token in tokens:
        proposed = " ".join(current + [token])
        if current and (len(current) >= max_words or len(proposed) > max_chars):
            chunks.append(" ".join(current))
            current = [token]
        else:
            current.append(token)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    centis = int(round((seconds - whole) * 100))
    if centis >= 100:
        whole += 1
        centis = 0
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    return f"{h}:{m:02d}:{s:02d}.{centis:02d}"


def write_compact_ass(segments: list[dict], start: float, end: float, target: Path) -> None:
    # Explicit portrait script resolution gives predictable size and position.
    # v3 used 38; 48 is intentionally only a moderate increase.
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,DejaVu Sans,48,&H00FFFFFF,&H000000FF,&H00000000,&H50000000,0,0,0,0,100,100,0,0,1,2.4,0,2,90,90,155,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events: list[str] = []
    for seg in segments:
        seg_start = max(start, float(seg["start"]))
        seg_end = min(end, float(seg["end"]))
        text = re.sub(r"\s+", " ", str(seg.get("text") or "")).strip()
        if seg_end <= seg_start or not text:
            continue
        chunks = _caption_chunks(text)
        if not chunks:
            continue
        weights = [max(1, len(c.split())) for c in chunks]
        total_weight = sum(weights)
        consumed = 0
        for pos, chunk in enumerate(chunks):
            cue_start = seg_start + (seg_end - seg_start) * (consumed / total_weight)
            consumed += weights[pos]
            cue_end = seg_end if pos == len(chunks) - 1 else seg_start + (seg_end - seg_start) * (consumed / total_weight)
            if cue_end - cue_start < 0.16:
                cue_end = min(seg_end, cue_start + 0.16)
            safe = chunk.replace("{", "(").replace("}", ")").replace("\n", " ")
            events.append(f"Dialogue: 0,{_ass_timestamp(cue_start-start)},{_ass_timestamp(cue_end-start)},Default,,0,0,0,,{safe}")
    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _iou(a, b) -> float:
    ax, ay, aw, ah = [float(x) for x in a]
    bx, by, bw, bh = [float(x) for x in b]
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _detect_faces(frame, frontal, profile) -> list[tuple[int, int, int, int]]:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    min_face = max(30, int(min(w, h) * 0.04))
    found: list[tuple[int, int, int, int]] = []

    for f in frontal.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(min_face, min_face)):
        found.append(tuple(int(v) for v in f))
    for f in profile.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(min_face, min_face)):
        found.append(tuple(int(v) for v in f))

    flipped = cv2.flip(gray, 1)
    for x, y, fw, fh in profile.detectMultiScale(flipped, scaleFactor=1.08, minNeighbors=4, minSize=(min_face, min_face)):
        found.append((w - int(x) - int(fw), int(y), int(fw), int(fh)))

    # Remove overlapping duplicates from frontal/profile detectors.
    found.sort(key=lambda f: f[2] * f[3], reverse=True)
    unique: list[tuple[int, int, int, int]] = []
    for face in found:
        if all(_iou(face, other) < 0.45 for other in unique):
            unique.append(face)
    return unique[:5]


def _activity_scores(frame_a, frame_b, frontal, profile) -> list[tuple[float, float, int]]:
    """Estimate who is talking by lower-face motion, with face size as a stability bonus."""
    import cv2
    import numpy as np

    faces = _detect_faces(frame_a, frontal, profile)
    if not faces:
        return []
    gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
    fh, fw = gray_a.shape[:2]
    scores: list[tuple[float, float, int]] = []

    for x, y, w, h in faces:
        x1 = max(0, x)
        x2 = min(fw, x + w)
        mouth_y1 = max(0, y + int(h * 0.52))
        mouth_y2 = min(fh, y + int(h * 0.94))
        upper_y1 = max(0, y + int(h * 0.12))
        upper_y2 = min(fh, y + int(h * 0.45))
        if x2 <= x1 or mouth_y2 <= mouth_y1:
            continue

        mouth_a = gray_a[mouth_y1:mouth_y2, x1:x2]
        mouth_b = gray_b[mouth_y1:mouth_y2, x1:x2]
        mouth_motion = float(np.mean(cv2.absdiff(mouth_a, mouth_b))) if mouth_a.size else 0.0

        upper_a = gray_a[upper_y1:upper_y2, x1:x2]
        upper_b = gray_b[upper_y1:upper_y2, x1:x2]
        upper_motion = float(np.mean(cv2.absdiff(upper_a, upper_b))) if upper_a.size else 0.0

        # Discount general head/camera motion; reward mouth-specific movement and a clear face.
        area_bonus = 8.0 * math.sqrt(max(1.0, (w * h) / max(1.0, fw * fh)))
        score = max(0.0, mouth_motion - 0.45 * upper_motion) + area_bonus
        scores.append((score, float(x + w / 2), w * h))

    scores.sort(key=lambda item: (item[0], item[2]), reverse=True)
    return scores


def _shot_boundaries(segments: list[dict], clip_start: float, clip_end: float) -> list[tuple[float, float]]:
    """Create editorial cuts around 5 s, preferring Whisper segment boundaries."""
    duration = max(0.1, clip_end - clip_start)
    relative_ends = sorted({max(0.0, min(duration, float(s["end"]) - clip_start)) for s in segments if clip_start < float(s["end"]) < clip_end})
    boundaries = [0.0]
    cursor = 0.0
    while duration - cursor > 7.0:
        target = cursor + 5.2
        lower = cursor + 3.8
        upper = min(duration, cursor + 7.0)
        choices = [t for t in relative_ends if lower <= t <= upper]
        cut = min(choices, key=lambda t: abs(t - target)) if choices else min(duration, target)
        if cut <= cursor + 2.5:
            cut = min(duration, cursor + 5.0)
        boundaries.append(cut)
        cursor = cut
    if duration - boundaries[-1] < 2.2 and len(boundaries) > 1:
        boundaries[-1] = duration
    else:
        boundaries.append(duration)

    pairs: list[tuple[float, float]] = []
    for a, b in zip(boundaries, boundaries[1:]):
        if b - a >= 0.6:
            pairs.append((a, b))
    return pairs


def analyze_static_shots(video: Path, plan: q2.ClipPlan, transcript_segments: list[dict]) -> ShotPlan:
    import cv2

    cap = cv2.VideoCapture(str(video))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError("Não foi possível detectar a resolução do vídeo.")

    frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    crop_w = min(width, max(2, int(height * 9 / 16)))
    half = crop_w / 2
    base_intervals = _shot_boundaries(transcript_segments, plan.start, plan.end)
    shots: list[Shot] = []

    try:
        for shot_index, (rel_start, rel_end) in enumerate(base_intervals):
            duration = rel_end - rel_start
            active_centers: list[float] = []
            alternate_centers: list[float] = []

            for frac in (0.28, 0.52, 0.76):
                rel_t = rel_start + duration * frac
                abs_t = plan.start + min(rel_t, max(0.0, plan.end - plan.start - 0.2))
                cap.set(cv2.CAP_PROP_POS_MSEC, abs_t * 1000)
                ok_a, frame_a = cap.read()
                cap.set(cv2.CAP_PROP_POS_MSEC, (abs_t + 0.14) * 1000)
                ok_b, frame_b = cap.read()
                if not ok_a or not ok_b:
                    continue
                scored = _activity_scores(frame_a, frame_b, frontal, profile)
                if scored:
                    active_centers.append(scored[0][1])
                if len(scored) >= 2:
                    alternate_centers.append(scored[1][1])

            if active_centers:
                active = _median(active_centers)
            else:
                # Fallback: center on a visible face at the middle of this shot.
                mid = plan.start + (rel_start + rel_end) / 2
                cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000)
                ok, frame = cap.read()
                faces = _detect_faces(frame, frontal, profile) if ok else []
                active = float(faces[0][0] + faces[0][2] / 2) if faces else width / 2

            active = max(half, min(width - half, active))
            alternate = None
            if len(alternate_centers) >= 2:
                candidate = _median(alternate_centers)
                if abs(candidate - active) > crop_w * 0.28:
                    alternate = max(half, min(width - half, candidate))

            # Every fourth suitable shot may briefly show a listener reaction.
            # Both frames remain static; this is a cut, never a pan.
            if shot_index % 4 == 3 and alternate is not None and duration >= 4.2:
                reaction_end = min(rel_start + 1.5, rel_end - 2.3)
                if reaction_end > rel_start + 0.8:
                    shots.append(Shot(rel_start, reaction_end, alternate, "reaction"))
                    shots.append(Shot(reaction_end, rel_end, active, "speaker"))
                    continue

            shots.append(Shot(rel_start, rel_end, active, "speaker"))
    finally:
        cap.release()

    reaction_count = sum(1 for s in shots if s.kind == "reaction")
    reason = f"{len(shots)} planos estáticos; foco por atividade facial; {reaction_count} corte(s) de reação"
    return ShotPlan(width, height, shots, reason)


def render_portrait_clip(video: Path, plan: q2.ClipPlan, segments: list[dict], ass: Path, output: Path, idx: int, total: int) -> ShotPlan:
    framing = analyze_static_shots(video, plan, segments)
    width, height = framing.width, framing.height
    escaped = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    source_ratio = width / height
    target_ratio = 9 / 16

    chains: list[str] = []
    labels = "".join(f"[src{i}]" for i in range(len(framing.shots)))
    chains.append(f"[0:v]split={len(framing.shots)}{labels}")

    for i, shot in enumerate(framing.shots):
        if source_ratio >= target_ratio:
            crop_w = max(2, int(height * target_ratio))
            crop_w -= crop_w % 2
            x = int(max(0, min(width - crop_w, shot.center_x - crop_w / 2)))
            transform = f"crop={crop_w}:{height}:{x}:0,scale=1080:1920,setsar=1"
        else:
            transform = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
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
        raise RuntimeError(f"FFmpeg falhou: {stderr[-2500:]}")

    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(output)
    ], text=True).strip()
    if probe != "1080x1920":
        raise RuntimeError(f"Saída deveria ser 1080x1920, mas FFprobe encontrou {probe!r}.")
    return framing


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    autoclip.check_configuration()
    shutil.rmtree(autoclip.WORK, ignore_errors=True)
    shutil.rmtree(autoclip.OUTPUT, ignore_errors=True)
    autoclip.WORK.mkdir(exist_ok=True)
    autoclip.OUTPUT.mkdir(exist_ok=True)

    autoclip.progress("Início", 2, "Baixando fonte")
    video, info = autoclip.download_youtube(url, autoclip.WORK)
    source_title = str(info.get("title") or "Vídeo do YouTube")
    autoclip.summary(f"# AutoClip Quality v4\n\n**Fonte:** {source_title}\n")

    transcript = autoclip.transcribe(video, whisper_model, autoclip.WORK)
    segments = transcript.get("segments") or []
    if not segments:
        raise RuntimeError("Whisper não encontrou fala suficiente.")

    autoclip.progress("Editor", 61, "Analisando a história inteira e procurando cortes com contexto")
    plans = q2.select_contextual_clips(segments, min_seconds, max_seconds, clips_count, source_title)
    if not plans:
        raise RuntimeError("Nenhum trecho contextual adequado foi encontrado.")

    autoclip.progress("Editor", 64, "Preparando legendas, falas e metadados")
    content = q2.prepare_clip_content(plans, segments, transcript.get("language"))

    buffer = autoclip.BufferClient()
    results = []
    total = len(plans)
    for idx, plan in enumerate(plans, start=1):
        item = content[idx - 1]
        autoclip.progress("Corte", 66 + int((idx - 1) / total * 20), f"Preparando {idx}/{total} · score {plan.score}")
        ass = autoclip.WORK / f"clip_{idx}.ass"
        output = autoclip.OUTPUT / f"clip_{idx}.mp4"
        write_compact_ass(item["segments"], plan.start, plan.end, ass)
        framing = render_portrait_clip(video, plan, item["segments"], ass, output, idx, total)

        autoclip.progress("Cloudinary", 88 + int((idx - 1) / total * 6), f"Enviando corte {idx}/{total}")
        cloud_url, cloud_id = autoclip.cloudinary_upload(output, idx)
        text = f"{item['caption']}\n\n{' '.join(item['hashtags'])}".strip()
        autoclip.progress("Buffer", 94 + int((idx - 1) / total * 5), f"Preparando corte {idx}/{total}")
        buffer_id = buffer.add_video_to_queue(cloud_url, text)
        results.append({
            "clip": idx,
            "score": plan.score,
            "cloudinary_url": cloud_url,
            "cloudinary_id": cloud_id,
            "buffer_post_id": buffer_id,
            "reason": plan.reason,
            "framing": framing.reason,
            "start": plan.start,
            "end": plan.end,
        })
        output.unlink(missing_ok=True)

    autoclip.progress("Concluído", 100, f"{len(results)} corte(s) processados")
    autoclip.summary("## Resultado Quality v4\n")
    public_safe = autoclip.env("PUBLIC_REPO_SAFE_LOGS", "false").lower() in {"1", "true", "yes", "on"}
    for r in results:
        asset = "Cloudinary: URL ocultada" if public_safe else r["cloudinary_url"]
        autoclip.summary(
            f"- **Corte {r['clip']}** — {autoclip.fmt_time(r['start'])}–{autoclip.fmt_time(r['end'])} "
            f"— score {r['score']} — edição: {r['framing']} — Buffer `{r['buffer_post_id']}` — {asset}"
        )
        if r["reason"]:
            autoclip.summary(f"  - Contexto editorial: {r['reason']}")
    autoclip.summary(
        "\nQuality v4: 1080x1920 preenchido, legenda inferior maior, planos estáticos de aproximadamente 4–7 s, "
        "foco em rosto com maior atividade facial e cortes curtos de reação quando outro participante está visível.\n"
    )
