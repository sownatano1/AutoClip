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
class DynamicFraming:
    width: int
    height: int
    keyframes: list[tuple[float, float]]
    reason: str


def _caption_chunks(text: str, max_words: int = 4, max_chars: int = 24) -> list[str]:
    """Keep captions short enough to stay on one compact line whenever possible."""
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
    """Create subtitles with an explicit 1080x1920 coordinate system.

    This avoids libass scaling SRT style values against its internal script resolution,
    which made previous captions unexpectedly huge and too high on screen.
    """
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,DejaVu Sans,38,&H00FFFFFF,&H000000FF,&H00000000,&H50000000,0,0,0,0,100,100,0,0,1,2,0,2,95,95,155,1

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
            events.append(
                f"Dialogue: 0,{_ass_timestamp(cue_start-start)},{_ass_timestamp(cue_end-start)},Default,,0,0,0,,{safe}"
            )
    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _median(values: list[float]) -> float:
    values = sorted(values)
    return values[len(values) // 2]


def analyze_dynamic_framing(video: Path, start: float, end: float) -> DynamicFraming:
    """Find the dominant face repeatedly so a vertical crop can pan with the subject."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError("Não foi possível detectar a resolução do vídeo.")

    duration = max(0.5, end - start)
    target_crop_w = min(width, max(2, int(height * 9 / 16)))
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    min_face = max(32, int(min(width, height) * 0.04))

    # About one framing decision every 2.5 s. This is enough to follow a speaker
    # without making the crop jitter on every frame.
    step = 2.5
    sample_times = [min(duration, i * step) for i in range(int(math.ceil(duration / step)) + 1)]
    if sample_times[-1] < duration:
        sample_times.append(duration)

    raw_centers: list[tuple[float, float | None]] = []
    try:
        for rel in sample_times:
            t = start + min(rel, max(0.0, duration - 0.05))
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                raw_centers.append((rel, None))
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = list(cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(min_face, min_face)))
            if not faces:
                raw_centers.append((rel, None))
                continue

            # Use the largest visible face as the primary subject at that moment.
            x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
            raw_centers.append((rel, float(x + w / 2)))
    finally:
        cap.release()

    known = [c for _, c in raw_centers if c is not None]
    fallback = _median(known) if known else width / 2

    # Fill missing detections from the nearest known framing point.
    filled: list[tuple[float, float]] = []
    for idx, (rel, center) in enumerate(raw_centers):
        if center is None:
            nearest = None
            nearest_dist = float("inf")
            for other_rel, other_center in raw_centers:
                if other_center is not None and abs(other_rel - rel) < nearest_dist:
                    nearest = other_center
                    nearest_dist = abs(other_rel - rel)
            center = float(nearest if nearest is not None else fallback)
        filled.append((rel, float(center)))

    # Median smooth adjacent detections to avoid jerky left/right movements.
    smoothed: list[tuple[float, float]] = []
    for i, (rel, center) in enumerate(filled):
        neighborhood = [filled[j][1] for j in range(max(0, i - 1), min(len(filled), i + 2))]
        smooth_center = _median(neighborhood)
        half = target_crop_w / 2
        smooth_center = max(half, min(width - half, smooth_center))
        smoothed.append((rel, smooth_center))

    spread = (max(c for _, c in smoothed) - min(c for _, c in smoothed)) if smoothed else 0
    reason = "crop 9:16 com acompanhamento suave do assunto" if spread > target_crop_w * 0.12 else "crop 9:16 centralizado no assunto"
    return DynamicFraming(width, height, smoothed, reason)


def _dynamic_x_expression(framing: DynamicFraming, crop_w: int) -> str:
    points = framing.keyframes
    if not points:
        center = framing.width / 2
        return str(int(max(0, min(framing.width - crop_w, center - crop_w / 2))))

    xs = [max(0.0, min(framing.width - crop_w, center - crop_w / 2)) for _, center in points]
    if len(points) == 1:
        return f"{xs[0]:.3f}"

    # Piecewise-linear panning driven by filter time t.
    expr = f"{xs[-1]:.3f}"
    for i in range(len(points) - 2, -1, -1):
        t0 = points[i][0]
        t1 = max(t0 + 0.001, points[i + 1][0])
        x0 = xs[i]
        x1 = xs[i + 1]
        interp = f"({x0:.3f}+({x1 - x0:.3f})*(t-{t0:.3f})/{t1 - t0:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{interp},{expr})"
    return f"max(0,min(iw-ow,{expr}))"


def render_portrait_clip(video: Path, plan: q2.ClipPlan, ass: Path, output: Path, idx: int, total: int) -> DynamicFraming:
    framing = analyze_dynamic_framing(video, plan.start, plan.end)
    width, height = framing.width, framing.height
    escaped = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    subtitle_filter = f"subtitles='{escaped}'"

    source_ratio = width / height
    target_ratio = 9 / 16
    if source_ratio >= target_ratio:
        crop_w = max(2, int(height * target_ratio))
        crop_w -= crop_w % 2
        x_expr = _dynamic_x_expression(framing, crop_w)
        # Fill the entire portrait frame. No landscape inset and no blurred bars.
        vf = f"crop={crop_w}:{height}:x='{x_expr}':y=0,scale=1080:1920,setsar=1,{subtitle_filter}"
    else:
        # Already portrait/narrow: fill 9:16 and crop excess vertically if necessary.
        vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,{subtitle_filter}"

    duration = max(0.1, plan.end - plan.start)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{plan.start:.3f}", "-i", str(video),
        "-t", f"{duration:.3f}", "-vf", vf,
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
        raise RuntimeError(f"FFmpeg falhou: {stderr[-2000:]}")

    # Verify that the final file is truly portrait before uploading it.
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
    autoclip.summary(f"# AutoClip Quality v3\n\n**Fonte:** {source_title}\n")

    transcript = autoclip.transcribe(video, whisper_model, autoclip.WORK)
    segments = transcript.get("segments") or []
    if not segments:
        raise RuntimeError("Whisper não encontrou fala suficiente.")

    autoclip.progress("Editor", 61, "Analisando a história inteira e procurando cortes com contexto")
    plans = q2.select_contextual_clips(segments, min_seconds, max_seconds, clips_count, source_title)
    if not plans:
        raise RuntimeError("Nenhum trecho contextual adequado foi encontrado.")

    autoclip.progress("Editor", 64, "Preparando legendas curtas e metadados dos cortes")
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
        framing = render_portrait_clip(video, plan, ass, output, idx, total)

        autoclip.progress("Cloudinary", 88 + int((idx - 1) / total * 6), f"Enviando corte {idx}/{total}")
        cloud_url, cloud_id = autoclip.cloudinary_upload(output, idx)
        text = f"{item['caption']}\n\n{' '.join(item['hashtags'])}".strip()
        autoclip.progress("Buffer", 94 + int((idx - 1) / total * 5), f"Preparando corte {idx}/{total}")
        buffer_id = buffer.add_video_to_queue(cloud_url, text)
        results.append({
            "clip": idx,
            "score": plan.score,
            "cloudinary_url": cloud_url,
            "buffer_post_id": buffer_id,
            "reason": plan.reason,
            "framing": framing.reason,
            "start": plan.start,
            "end": plan.end,
        })
        output.unlink(missing_ok=True)

    autoclip.progress("Concluído", 100, f"{len(results)} corte(s) em retrato concluídos")
    autoclip.summary("## Resultado Quality v3\n")
    for r in results:
        autoclip.summary(
            f"- **Corte {r['clip']}** — {autoclip.fmt_time(r['start'])}–{autoclip.fmt_time(r['end'])} "
            f"— score {r['score']} — {r['framing']} — Buffer `{r['buffer_post_id']}` — {r['cloudinary_url']}"
        )
        if r["reason"]:
            autoclip.summary(f"  - Contexto editorial: {r['reason']}")
    autoclip.summary(
        "\nQuality v3: saída verificada em 1080x1920; vídeo preenche todo o quadro 9:16; "
        "crop acompanha suavemente o assunto; legendas ASS curtas, menores e fixadas na parte inferior.\n"
    )
