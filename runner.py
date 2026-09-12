from __future__ import annotations

import os
import shutil
from pathlib import Path

import autoclip


def cookie_aware_download(url: str, target_dir: Path):
    import yt_dlp

    autoclip.validate_youtube_url(url)
    target_dir.mkdir(parents=True, exist_ok=True)
    template = str(target_dir / "source.%(ext)s")
    last_pct = -1

    def hook(d: dict) -> None:
        nonlocal last_pct
        if d.get("status") == "downloading":
            downloaded = float(d.get("downloaded_bytes") or 0)
            total = float(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            if total > 0:
                pct = max(0, min(99, int(downloaded * 100 / total)))
                if pct != last_pct:
                    last_pct = pct
                    eta = d.get("eta")
                    eta_text = f" · ~{int(eta)}s" if isinstance(eta, (int, float)) and eta >= 0 else ""
                    autoclip.progress(
                        "Download",
                        5 + int(pct * 0.20),
                        f"{pct}%{autoclip.human_speed(d.get('speed'))}{eta_text}",
                    )
        elif d.get("status") == "finished":
            autoclip.progress("Download", 25, "Concluído; preparando arquivo")

    node_path = shutil.which("node")
    if not node_path:
        raise RuntimeError("Node.js não foi encontrado no runner; ele é necessário para resolver os desafios do YouTube.")

    opts = {
        "format": "bv*[height<=720]+ba/b[height<=720]/best[height<=720]/best",
        "merge_output_format": "mp4",
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "restrictfilenames": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 1,
        # YouTube now requires an external JS challenge solver for many formats.
        # GitHub's Ubuntu runner ships Node 24, which is supported by yt-dlp.
        "js_runtimes": {"node": {"path": node_path}},
        # Keep the GitHub EJS component fallback enabled even though the
        # yt-dlp[default] dependency group installs yt-dlp-ejs locally.
        "remote_components": {"ejs:github"},
        # Datacenter/IP bot checks: use mweb together with the automatic PO token provider.
        "extractor_args": {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilhttp": {
                "base_url": [os.getenv("YOUTUBE_POT_PROVIDER_URL", "http://127.0.0.1:4416")]
            },
        },
    }

    cookie_file = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if cookie_file and Path(cookie_file).is_file():
        opts["cookiefile"] = cookie_file
        autoclip.progress("Download", 3, "Sessão do YouTube carregada pelo Secret")

    autoclip.progress("Download", 4, "PO Token + EJS/Node habilitados para o YouTube")

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    files = [
        p
        for p in target_dir.glob("source.*")
        if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
    ]
    if not files:
        raise RuntimeError("Download terminou, mas nenhum vídeo foi encontrado.")
    return max(files, key=lambda p: p.stat().st_size), info


def main() -> None:
    autoclip.download_youtube = cookie_aware_download
    url = os.environ["YOUTUBE_URL"]
    clips = max(1, min(3, int(os.getenv("CLIPS_PER_SOURCE", "3"))))
    min_seconds = max(20, int(os.getenv("MIN_CLIP_SECONDS", "60")))
    max_seconds = max(min_seconds, int(os.getenv("MAX_CLIP_SECONDS", "180")))
    whisper_model = os.getenv("WHISPER_MODEL", "tiny")
    autoclip.run(url, clips, min_seconds, max_seconds, whisper_model)


if __name__ == "__main__":
    main()
