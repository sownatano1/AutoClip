from __future__ import annotations

import os
import shutil
from pathlib import Path

import autoclip
import publish_backlog
import quality_v9_11_1
import quality_v9_12


_REAL_BUFFER_CLIENT = autoclip.BufferClient


def _youtube_common_options() -> dict:
    node_path = shutil.which("node")
    if not node_path:
        raise RuntimeError("Node.js não foi encontrado no runner; ele é necessário para resolver os desafios do YouTube.")

    opts = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "js_runtimes": {"node": {"path": node_path}},
        "remote_components": {"ejs:github"},
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
    return opts


def validate_youtube_session(url: str) -> None:
    import yt_dlp

    autoclip.validate_youtube_url(url)
    cookie_file = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if not cookie_file or not Path(cookie_file).is_file():
        raise RuntimeError(
            "Sessão do YouTube ausente. Atualize o Secret YOUTUBE_COOKIES_B64 antes de processar."
        )

    opts = _youtube_common_options()
    opts.update({"skip_download": True})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise RuntimeError(
            "Sessão do YouTube inválida ou rotacionada. Exporte cookies novos de uma sessão "
            "privada/incógnita dedicada e atualize YOUTUBE_COOKIES_B64. "
            f"Detalhe: {exc}"
        ) from exc

    if not info:
        raise RuntimeError("YouTube não retornou dados do vídeo durante a validação da sessão.")
    print("YouTube: sessão válida", flush=True)


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

    opts = _youtube_common_options()
    opts.update(
        {
            "format": "bv*[height<=720]+ba/b[height<=720]/best[height<=720]/best",
            "merge_output_format": "mp4",
            "outtmpl": template,
            "restrictfilenames": True,
            "progress_hooks": [hook],
            "concurrent_fragment_downloads": 1,
        }
    )

    if opts.get("cookiefile"):
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


class PreviewBufferClient:
    """Keep the full pipeline intact while preventing test clips from entering TikTok queue."""
    def __init__(self):
        pass

    def add_video_to_queue(self, video_url: str, text: str) -> str:
        print(f"PREVIEW: vídeo disponível no Cloudinary: {video_url}", flush=True)
        print(f"PREVIEW: legenda Buffer/TikTok:\n{text}\n", flush=True)
        return "PREVIEW_ONLY"


class BacklogAwareBufferClient:
    """Publish immediately while there is room; persist overflow safely in GitHub.

    This wrapper also exposes BufferClient.call() because Quality v7 temporarily
    replaces add_video_to_queue() to control the video thumbnail/cover offset.
    """

    def __init__(self):
        self._client = _REAL_BUFFER_CLIENT()

    def channel_id(self) -> str:
        return self._client.channel_id()

    def _enqueue(self, video_url: str, text: str, reason: str) -> str:
        item = publish_backlog.enqueue(video_url, text, source="AutoClip process-video")
        print(
            f"BACKLOG: corte {item['id']} salvo para publicação posterior ({reason}).",
            flush=True,
        )
        return f"BACKLOG:{item['id']}"

    @staticmethod
    def _create_post_payload(variables: dict | None) -> tuple[str, str]:
        payload = (variables or {}).get("input") or {}
        text = str(payload.get("text") or "")
        video_url = ""
        assets = payload.get("assets") or []
        if isinstance(assets, list) and assets:
            first = assets[0] if isinstance(assets[0], dict) else {}
            video = first.get("video") if isinstance(first, dict) else {}
            if isinstance(video, dict):
                video_url = str(video.get("url") or "").strip()
        return video_url, text

    def _backlog_graphql_success(self, video_url: str, text: str, reason: str) -> dict:
        backlog_id = self._enqueue(video_url, text, reason)
        # Quality v7 expects the same shape returned by autoclip.BufferClient.call().
        return {"createPost": {"post": {"id": backlog_id, "dueAt": None}}}

    def call(self, query: str, variables: dict | None = None) -> dict:
        """Delegate GraphQL calls while intercepting createPost overflow.

        Quality v7 calls this method directly when covers are enabled, so backlog
        handling must live here as well as in add_video_to_queue().
        """
        is_create_post = "createPost" in str(query)
        video_url, text = self._create_post_payload(variables) if is_create_post else ("", "")

        if is_create_post and video_url:
            try:
                count = publish_backlog.pending_count()
            except Exception as exc:
                print(f"Aviso: não foi possível consultar o backlog antes do Buffer: {exc}", flush=True)
                count = 0

            if count > 0:
                return self._backlog_graphql_success(
                    video_url,
                    text,
                    f"{count} item(ns) já aguardando",
                )

        try:
            data = self._client.call(query, variables)
        except Exception as exc:
            if is_create_post and video_url and publish_backlog.is_buffer_capacity_error(exc):
                return self._backlog_graphql_success(
                    video_url,
                    text,
                    "limite de posts agendados do Buffer atingido",
                )
            raise

        if is_create_post and video_url:
            create_post = data.get("createPost") if isinstance(data, dict) else None
            message = str((create_post or {}).get("message") or "") if isinstance(create_post, dict) else ""
            if message and publish_backlog.is_buffer_capacity_error(RuntimeError(message)):
                return self._backlog_graphql_success(
                    video_url,
                    text,
                    "limite de posts agendados do Buffer atingido",
                )

        return data

    def add_video_to_queue(self, video_url: str, text: str) -> str:
        # Preserve FIFO: once there are older pending clips, new clips join the
        # backlog instead of jumping ahead of them in Buffer.
        try:
            count = publish_backlog.pending_count()
        except Exception as exc:
            print(f"Aviso: não foi possível consultar o backlog antes do Buffer: {exc}", flush=True)
            count = 0

        if count > 0:
            return self._enqueue(video_url, text, f"{count} item(ns) já aguardando")

        try:
            return self._client.add_video_to_queue(video_url, text)
        except Exception as exc:
            if publish_backlog.is_buffer_capacity_error(exc):
                return self._enqueue(video_url, text, "limite de posts agendados do Buffer atingido")
            raise


def main() -> None:
    url = os.environ["YOUTUBE_URL"]
    if os.getenv("YOUTUBE_PREFLIGHT_ONLY", "").strip().lower() in {"1", "true", "yes"}:
        validate_youtube_session(url)
        return

    autoclip.download_youtube = cookie_aware_download
    # Preserve the authenticated YouTube downloader through every quality wrapper.
    quality_v9_11_1.q11.q101.q10.q99.q97.q6._ORIGINAL_DOWNLOAD = cookie_aware_download
    auto_publish = os.getenv("AUTO_PUBLISH", "false").strip().lower() in {"1", "true", "yes"}
    if not auto_publish:
        autoclip.BufferClient = PreviewBufferClient
        print("Modo PREVIEW ativo: os cortes não serão enviados ao Buffer/TikTok.", flush=True)
    else:
        autoclip.BufferClient = BacklogAwareBufferClient
        print(
            "Modo PUBLICAÇÃO ativo: Buffer será usado até o limite; excedentes irão para o backlog persistente.",
            flush=True,
        )

    clips = max(1, min(3, int(os.getenv("CLIPS_PER_SOURCE", "3"))))
    min_seconds = max(20, int(os.getenv("MIN_CLIP_SECONDS", "25")))
    max_seconds = max(min_seconds, int(os.getenv("MAX_CLIP_SECONDS", "150")))
    whisper_model = os.getenv("WHISPER_MODEL", "base")
    quality_v9_12.run(url, clips, min_seconds, max_seconds, whisper_model)


if __name__ == "__main__":
    main()
