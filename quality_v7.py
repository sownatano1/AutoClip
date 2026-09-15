from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

import autoclip
import quality_v4 as q4
import quality_v5 as q5
import quality_v6 as q6
import quality_v6_1 as q61


COVERS: dict[int, dict] = {}


def _enabled() -> bool:
    return os.getenv("CREATE_COVER", "true").strip().lower() in {"1", "true", "yes"}


def _cover_duration() -> float:
    raw = os.getenv("COVER_DURATION_SECONDS", "0.8").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.8
    return max(0.3, min(2.0, value))


def _style() -> str:
    raw = os.getenv("COVER_STYLE", "Auto").strip().lower()
    if raw in {"clean", "bold", "cinematic"}:
        return raw
    profile = q6.CURRENT_PROFILE
    if profile == "film":
        return "cinematic"
    if profile == "talking":
        return "clean"
    return "bold"


def _cover_title(plan, segments: list[dict]) -> str:
    hook = q6.HOOK_BY_BOUNDS.get((round(plan.start, 2), round(plan.end, 2)), "")
    if hook:
        return re.sub(r"\s+", " ", hook).strip()
    text = " ".join(str(s.get("text") or "").strip() for s in segments).strip()
    words = text.split()
    if not words:
        return "HIGHLIGHT"
    title = " ".join(words[:7]).strip(" .,:;!?-")
    return title[:72] or "HIGHLIGHT"


def _sample_frame(video: Path, absolute_time: float):
    import cv2
    cap = cv2.VideoCapture(str(video))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, absolute_time * 1000)
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def _best_frame(video: Path, plan) -> tuple[np.ndarray, float, float]:
    """Choose a sharp, well-exposed, visually useful frame from inside the selected clip."""
    import cv2

    frontal, profile = q6._cascades()
    duration = max(1.0, plan.end - plan.start)
    candidates: list[tuple[float, float, np.ndarray, float]] = []
    previous_gray = None

    for frac in np.linspace(0.12, 0.88, 11):
        rel = float(duration * frac)
        absolute = plan.start + rel
        frame = _sample_frame(video, absolute)
        if frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharpness = min(900.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        brightness = float(gray.mean())
        contrast = float(gray.std())
        exposure = max(0.0, 1.0 - abs(brightness - 128.0) / 128.0)
        faces = q4._detect_faces(frame, frontal, profile)
        h, w = gray.shape[:2]
        frame_area = max(1.0, float(w * h))
        face_area = sum(float(fw * fh) for _, _, fw, fh in faces[:3]) / frame_area
        largest_face = max((float(fw * fh) for _, _, fw, fh in faces), default=0.0) / frame_area
        motion = 0.0
        if previous_gray is not None and previous_gray.shape == gray.shape:
            motion = float(np.mean(cv2.absdiff(previous_gray, gray)))
        previous_gray = gray

        base = sharpness * 0.07 + contrast * 0.8 + exposure * 35.0
        if q6.CURRENT_PROFILE in {"podcast", "talking"}:
            score = base + face_area * 1000.0 + largest_face * 1700.0
        elif q6.CURRENT_PROFILE == "gameplay":
            score = base + motion * 1.8 + contrast * 0.7
        else:
            score = base + motion * 1.1 + face_area * 450.0

        center_x = w / 2.0
        if faces:
            x, y, fw, fh = max(faces, key=lambda f: int(f[2]) * int(f[3]))
            center_x = float(x + fw / 2)
        elif q6.CURRENT_PROFILE == "gameplay":
            center_x = q6._motion_center(video, max(plan.start, absolute - 0.35), min(plan.end, absolute + 0.35), w)

        candidates.append((score, absolute, frame, center_x))

    if not candidates:
        absolute = plan.start + duration * 0.35
        frame = _sample_frame(video, absolute)
        if frame is None:
            raise RuntimeError("Não foi possível extrair um frame para a capa.")
        return frame, absolute, frame.shape[1] / 2

    _, absolute, frame, center_x = max(candidates, key=lambda item: item[0])
    return frame, absolute, center_x


def _portrait_frame(frame: np.ndarray, center_x: float) -> np.ndarray:
    import cv2

    h, w = frame.shape[:2]
    target_ratio = 9 / 16
    if w / h >= target_ratio:
        crop_w = max(2, int(h * target_ratio))
        crop_w -= crop_w % 2
        x = int(max(0, min(w - crop_w, center_x - crop_w / 2)))
        frame = frame[:, x:x + crop_w]
    else:
        target_h = max(2, int(w / target_ratio))
        if target_h <= h:
            y = max(0, int((h - target_h) / 2))
            frame = frame[y:y + target_h, :]
    return cv2.resize(frame, (1080, 1920), interpolation=cv2.INTER_LANCZOS4)


def _font(size: int) -> ImageFont.FreeTypeFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _wrap_title(draw: ImageDraw.ImageDraw, text: str, font, max_width: int = 900, max_lines: int = 3) -> list[str]:
    words = re.sub(r"\s+", " ", text).strip().split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        proposed = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), proposed, font=font, stroke_width=0)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) >= max_lines - 1:
                break
        else:
            current.append(word)
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    consumed = sum(len(line.split()) for line in lines)
    if consumed < len(words) and lines:
        lines[-1] = (lines[-1].rstrip(" .") + "…")
    return lines


def _vignette(image: Image.Image, strength: float = 0.42) -> Image.Image:
    w, h = image.size
    yy, xx = np.mgrid[0:h, 0:w]
    dx = (xx - w / 2) / (w / 2)
    dy = (yy - h / 2) / (h / 2)
    dist = np.sqrt(dx * dx + dy * dy)
    alpha = np.clip((dist - 0.25) / 0.9, 0, 1) * (255 * strength)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.putalpha(Image.fromarray(alpha.astype(np.uint8), mode="L"))
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    return Image.alpha_composite(image.convert("RGBA"), Image.composite(black, overlay, overlay.getchannel("A")))


def _make_cover(frame: np.ndarray, center_x: float, title: str, target: Path) -> str:
    import cv2

    portrait = _portrait_frame(frame, center_x)
    image = Image.fromarray(cv2.cvtColor(portrait, cv2.COLOR_BGR2RGB)).convert("RGBA")
    style = _style()

    if style == "cinematic":
        image = ImageEnhance.Color(image).enhance(0.86)
        image = ImageEnhance.Contrast(image).enhance(1.22)
        image = ImageEnhance.Sharpness(image).enhance(1.15)
        image = _vignette(image, 0.52)
    elif style == "clean":
        image = ImageEnhance.Contrast(image).enhance(1.08)
        image = ImageEnhance.Sharpness(image).enhance(1.12)
    else:
        image = ImageEnhance.Color(image).enhance(1.10)
        image = ImageEnhance.Contrast(image).enhance(1.18)
        image = ImageEnhance.Sharpness(image).enhance(1.20)
        image = _vignette(image, 0.34)

    # Dark top gradient keeps the title readable without covering the whole image.
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(0, 760, 8):
        alpha = int(max(0, 180 * (1 - y / 760)))
        od.rectangle((0, y, 1080, y + 8), fill=(0, 0, 0, alpha))
    image = Image.alpha_composite(image, overlay)

    draw = ImageDraw.Draw(image)
    text = title.upper() if style == "bold" else title
    font_size = 92 if style == "bold" else 82
    font = _font(font_size)
    lines = _wrap_title(draw, text, font, max_width=900, max_lines=3)
    line_gap = 14
    heights = []
    widths = []
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font, stroke_width=5)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    total_h = sum(heights) + max(0, len(lines) - 1) * line_gap
    y = 110

    if style == "clean":
        max_w = max(widths, default=0)
        draw.rounded_rectangle((70, y - 34, 70 + max_w + 70, y + total_h + 40), radius=28, fill=(0, 0, 0, 125))
    elif style == "bold":
        draw.rounded_rectangle((66, y - 28, 82, y + total_h + 26), radius=8, fill=(255, 210, 40, 255))

    for line, line_h in zip(lines, heights):
        draw.text((105, y), line, font=font, fill=(255, 255, 255, 255), stroke_width=5, stroke_fill=(0, 0, 0, 220))
        y += line_h + line_gap

    # Small visual tag so covers from AutoClip have a consistent hierarchy, not a watermark.
    if style == "cinematic":
        draw.rounded_rectangle((105, 88, 260, 126), radius=12, fill=(0, 0, 0, 150))

    image.convert("RGB").save(target, quality=95)
    return style


def _has_audio(path: Path) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(probe.stdout.strip())


def _prepend_cover(video: Path, cover: Path, duration: float) -> None:
    temp = video.with_name(video.stem + "_with_cover.mp4")
    ms = int(round(duration * 1000))
    base = [
        "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-t", f"{duration:.3f}", "-i", str(cover),
        "-i", str(video),
    ]
    video_filters = (
        "[0:v]scale=1080:1920,setsar=1,fps=30,format=yuv420p,setpts=PTS-STARTPTS[c];"
        "[1:v]scale=1080:1920,setsar=1,fps=30,format=yuv420p,setpts=PTS-STARTPTS[m];"
        "[c][m]concat=n=2:v=1:a=0[v]"
    )
    if _has_audio(video):
        filters = video_filters + f";[1:a]adelay={ms}:all=1[a]"
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        filters = video_filters
        maps = ["-map", "[v]"]
    cmd = base + ["-filter_complex", filters] + maps + [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "160k", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg não conseguiu inserir a capa: {result.stderr[-1800:]}")
    temp.replace(video)


def _upload_cover(path: Path, clip_number: int) -> tuple[str, str]:
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=autoclip.require("CLOUDINARY_CLOUD_NAME"),
        api_key=autoclip.require("CLOUDINARY_API_KEY"),
        api_secret=autoclip.require("CLOUDINARY_API_SECRET"),
        secure=True,
    )
    folder = (autoclip.env("CLOUDINARY_FOLDER", "autoclips").strip("/") or "autoclips") + "/covers"
    public_id = f"{int(time.time())}_cover_{clip_number}"
    result = cloudinary.uploader.upload(
        str(path), resource_type="image", folder=folder, public_id=public_id,
        overwrite=False, unique_filename=False, use_filename=False,
    )
    url = str(result.get("secure_url") or "").strip()
    pid = str(result.get("public_id") or "").strip()
    if not url:
        raise RuntimeError("Cloudinary não devolveu secure_url para a capa.")
    return url, pid


def _buffer_add_with_cover(self, video_url: str, text: str) -> str:
    q = """mutation CreateVideo($input: CreatePostInput!) { createPost(input: $input) { ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""
    offset = int(float(os.getenv("THUMBNAIL_OFFSET_MS", "200")))
    variables = {
        "input": {
            "text": text,
            "channelId": self.channel_id(),
            "schedulingType": "automatic",
            "mode": "addToQueue",
            "assets": [{"video": {"url": video_url, "metadata": {"thumbnailOffset": offset}}}],
        }
    }
    result = self.call(q, variables).get("createPost") or {}
    if result.get("message"):
        raise RuntimeError(result["message"])
    post = result.get("post") or {}
    if not post.get("id"):
        raise RuntimeError("Buffer não devolveu ID da publicação.")
    return str(post["id"])


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    COVERS.clear()
    original_render = q4.render_portrait_clip
    original_cloud_upload = autoclip.cloudinary_upload
    original_buffer_add = getattr(autoclip.BufferClient, "add_video_to_queue", None)

    def render_with_cover(video: Path, plan, segments: list[dict], ass: Path, output: Path, idx: int, total: int):
        framing = original_render(video, plan, segments, ass, output, idx, total)
        if not _enabled():
            return framing
        autoclip.progress("Capa", 87, f"Escolhendo melhor frame do corte {idx}/{total}")
        frame, frame_time, center_x = _best_frame(video, plan)
        cover_path = autoclip.WORK / f"cover_{idx}.jpg"
        title = _cover_title(plan, segments)
        style = _make_cover(frame, center_x, title, cover_path)
        _prepend_cover(output, cover_path, _cover_duration())
        COVERS[idx] = {
            "path": cover_path,
            "title": title,
            "style": style,
            "frame_time": frame_time,
            "url": "",
        }
        return framing

    def upload_with_cover(file: Path, clip_number: int):
        video_url, video_id = original_cloud_upload(file, clip_number)
        cover = COVERS.get(clip_number)
        if cover and Path(cover["path"]).is_file():
            autoclip.progress("Capa", 93, f"Enviando capa {clip_number} ao Cloudinary")
            cover_url, cover_id = _upload_cover(Path(cover["path"]), clip_number)
            cover["url"] = cover_url
            cover["cloudinary_id"] = cover_id
        return video_url, video_id

    try:
        q4.render_portrait_clip = render_with_cover
        autoclip.cloudinary_upload = upload_with_cover
        if _enabled() and os.getenv("AUTO_PUBLISH", "false").strip().lower() in {"1", "true", "yes"} and original_buffer_add:
            autoclip.BufferClient.add_video_to_queue = _buffer_add_with_cover
            os.environ["THUMBNAIL_OFFSET_MS"] = "200"
        autoclip.log(
            f"Quality v7: capa={'on' if _enabled() else 'off'} · estilo={_style()} · "
            f"intro={_cover_duration():g}s"
        )
        q61.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        if _enabled() and COVERS:
            autoclip.summary("\n### Capas v7\n")
            for idx in sorted(COVERS):
                info = COVERS[idx]
                autoclip.summary(
                    f"- **Corte {idx}** — estilo **{info['style']}** — título: **{info['title']}** "
                    f"— frame {autoclip.fmt_time(float(info['frame_time']))} — {info.get('url') or 'capa local'}"
                )
            autoclip.summary(
                f"\nA capa fica por **{_cover_duration():g}s** no início do MP4 e o Buffer aponta para o começo do vídeo como thumbnail.\n"
            )
    finally:
        q4.render_portrait_clip = original_render
        autoclip.cloudinary_upload = original_cloud_upload
        if original_buffer_add:
            autoclip.BufferClient.add_video_to_queue = original_buffer_add
