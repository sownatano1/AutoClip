from __future__ import annotations

import os
import subprocess
from pathlib import Path

import autoclip
import quality_v9_11_1 as q111


ENHANCE_RESULTS: list[dict] = []


def _enabled() -> bool:
    return os.getenv("FINAL_VISUAL_ENHANCE", "true").strip().lower() in {"1", "true", "yes", "on"}


def _filter_chain() -> str:
    # Conservative finishing pass: light compression-noise cleanup, tiny tonal lift
    # and restrained sharpening. It improves perceived facial detail without
    # creating the brittle/halo-heavy look of aggressive sharpening.
    return (
        "hqdn3d=0.45:0.35:2.5:2.0,"
        "eq=contrast=1.018:brightness=0.003:saturation=1.02:gamma=1.005,"
        "unsharp=5:5:0.38:3:3:0.0"
    )


def _duration(path: Path) -> float:
    try:
        value = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            text=True,
        ).strip()
        return max(0.0, float(value))
    except Exception:
        return 0.0


def enhance_final_clip(path: Path, clip_number: int) -> bool:
    """Apply a lightweight finishing pass only to the already-rendered short clip.

    This intentionally runs after edit/crop/subtitles/Auto Review/cover and directly
    before Cloudinary upload, so the long YouTube source is never filtered.
    Enhancement is non-critical: if FFmpeg cannot finish, the original clip is kept.
    """
    if not _enabled() or not path.is_file():
        return False

    before_size = path.stat().st_size
    duration = _duration(path)
    temp = path.with_name(f"{path.stem}.enhanced{path.suffix}")

    autoclip.progress(
        "Enhance Final",
        88,
        f"Corte {clip_number}: melhorando nitidez, rostos e compressão no MP4 final",
    )

    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", _filter_chain(),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", "-loglevel", "error",
        str(temp),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    last = -1
    if proc.stdout:
        for raw in proc.stdout:
            line = raw.strip()
            if duration > 0 and line.startswith(("out_time_ms=", "out_time_us=")):
                try:
                    micros = float(line.split("=", 1)[1])
                    pct = int(max(0, min(99, micros / (duration * 1_000_000) * 100)))
                    if pct >= last + 20:
                        last = pct
                        autoclip.log(f"Enhance Final corte {clip_number}: {pct}%")
                except Exception:
                    pass

    stderr = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()

    if code != 0 or not temp.is_file() or temp.stat().st_size < 100_000:
        temp.unlink(missing_ok=True)
        autoclip.log(
            f"Aviso: Enhance Final não pôde ser aplicado ao corte {clip_number}; "
            "o MP4 original será mantido. "
            + (stderr[-700:] if stderr else "")
        )
        ENHANCE_RESULTS.append(
            {"clip": clip_number, "applied": False, "reason": "ffmpeg_fallback"}
        )
        return False

    temp.replace(path)
    after_size = path.stat().st_size
    ENHANCE_RESULTS.append(
        {
            "clip": clip_number,
            "applied": True,
            "before_mb": round(before_size / 1024 / 1024, 1),
            "after_mb": round(after_size / 1024 / 1024, 1),
        }
    )
    autoclip.log(
        f"Enhance Final corte {clip_number}: aplicado · "
        f"{before_size / 1024 / 1024:.1f} MB → {after_size / 1024 / 1024:.1f} MB"
    )
    return True


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_upload = autoclip.cloudinary_upload
    ENHANCE_RESULTS.clear()

    def upload_enhanced(file: Path, clip_number: int):
        enhance_final_clip(Path(file), clip_number)
        return original_upload(file, clip_number)

    try:
        autoclip.cloudinary_upload = upload_enhanced
        autoclip.log(
            "Quality v9.12: Final Visual Enhance={} · pós-processamento somente no clipe pronto"
            .format("on" if _enabled() else "off")
        )
        q111.run(url, clips_count, min_seconds, max_seconds, whisper_model)

        autoclip.summary("\n### Quality v9.12 — Final Visual Enhance\n")
        if not _enabled():
            autoclip.summary("Enhance final: **desativado**.\n")
        elif not ENHANCE_RESULTS:
            autoclip.summary("Enhance final: **nenhum clipe processado**.\n")
        else:
            for item in ENHANCE_RESULTS:
                if item.get("applied"):
                    autoclip.summary(
                        f"- **Corte {item['clip']}** — enhance aplicado no MP4 final "
                        f"({item['before_mb']} MB → {item['after_mb']} MB)."
                    )
                else:
                    autoclip.summary(
                        f"- **Corte {item['clip']}** — enhance pulado por segurança; MP4 original preservado."
                    )
            autoclip.summary(
                "\nO filtro roda somente depois que o corte vertical está pronto, antes do upload: "
                "redução leve de ruído/compressão + ajuste tonal sutil + nitidez moderada. "
                "O vídeo-fonte inteiro não é reprocessado.\n"
            )
    finally:
        autoclip.cloudinary_upload = original_upload
