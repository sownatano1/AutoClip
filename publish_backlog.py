from __future__ import annotations

import argparse
import base64
import json
import hashlib
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet, InvalidToken


BACKLOG_PATH = os.getenv("AUTOCLIP_BACKLOG_PATH", "data/publish_backlog.enc").strip() or "data/publish_backlog.enc"
API_ROOT = "https://api.github.com"


class BacklogConflict(RuntimeError):
    pass

def _fernet() -> Fernet:
    # Prefer a dedicated encryption secret when configured; otherwise derive a
    # high-entropy key from the Buffer API key that is already required to publish.
    # The repository stores only ciphertext, so making it public does not expose
    # pending Cloudinary URLs/captions.
    material = (
        os.getenv("AUTOCLIP_BACKLOG_ENCRYPTION_KEY", "").strip()
        or os.getenv("BUFFER_API_KEY", "").strip()
    )
    if not material:
        raise RuntimeError(
            "Secret para criptografar o backlog ausente. Configure "
            "AUTOCLIP_BACKLOG_ENCRYPTION_KEY ou BUFFER_API_KEY."
        )
    digest = hashlib.sha256(("AutoClip::publish-backlog::v1::" + material).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))



def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _github_context() -> tuple[str, str, str]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = (
        os.getenv("AUTOCLIP_BACKLOG_BRANCH", "").strip()
        or os.getenv("GITHUB_REF_NAME", "").strip()
        or "main"
    )
    if not token:
        raise RuntimeError("GITHUB_TOKEN ausente; não é possível salvar o backlog.")
    if not repo or "/" not in repo:
        raise RuntimeError("GITHUB_REPOSITORY ausente ou inválido; não é possível salvar o backlog.")
    return token, repo, branch


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _contents_url(repo: str) -> str:
    safe_path = "/".join(quote(part, safe="") for part in BACKLOG_PATH.split("/"))
    return f"{API_ROOT}/repos/{repo}/contents/{safe_path}"


def _empty() -> dict[str, Any]:
    return {"version": 1, "items": []}


def _load() -> tuple[dict[str, Any], str | None]:
    token, repo, branch = _github_context()
    response = requests.get(
        _contents_url(repo),
        headers=_headers(token),
        params={"ref": branch},
        timeout=30,
    )
    if response.status_code == 404:
        return _empty(), None
    response.raise_for_status()
    payload = response.json()
    encrypted = base64.b64decode(payload.get("content", ""))
    try:
        raw = _fernet().decrypt(encrypted).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Não foi possível descriptografar o backlog. A chave de criptografia "
            "pode ter sido alterada enquanto ainda havia itens pendentes."
        ) from exc
    data = json.loads(raw) if raw.strip() else _empty()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise RuntimeError(f"Backlog inválido em {BACKLOG_PATH}.")
    data.setdefault("version", 1)
    return data, str(payload.get("sha") or "") or None


def _save(data: dict[str, Any], sha: str | None, message: str) -> None:
    token, repo, branch = _github_context()
    plaintext = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")
    encrypted = _fernet().encrypt(plaintext)
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(encrypted).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    response = requests.put(
        _contents_url(repo),
        headers=_headers(token),
        json=body,
        timeout=30,
    )
    if response.status_code in {409, 422}:
        raise BacklogConflict("O backlog mudou enquanto estava sendo atualizado.")
    response.raise_for_status()


def pending_items() -> list[dict[str, Any]]:
    data, _ = _load()
    return [dict(item) for item in data.get("items", []) if isinstance(item, dict)]


def pending_count() -> int:
    return len(pending_items())


def enqueue(video_url: str, text: str, *, source: str = "AutoClip") -> dict[str, Any]:
    item = {
        "id": uuid.uuid4().hex[:16],
        "created_at": _now_iso(),
        "video_url": str(video_url).strip(),
        "text": str(text),
        "source": source,
    }
    if not item["video_url"]:
        raise RuntimeError("Não é possível adicionar ao backlog sem URL do vídeo.")

    for attempt in range(1, 6):
        data, sha = _load()
        items = data.setdefault("items", [])
        if any(existing.get("id") == item["id"] for existing in items if isinstance(existing, dict)):
            return item
        items.append(item)
        try:
            _save(data, sha, f"AutoClip: adicionar item {item['id']} ao backlog")
            return item
        except BacklogConflict:
            if attempt == 5:
                raise
            time.sleep(0.5 * attempt)

    raise RuntimeError("Não foi possível adicionar item ao backlog.")


def remove_item(item_id: str) -> bool:
    for attempt in range(1, 6):
        data, sha = _load()
        old_items = data.get("items", [])
        new_items = [item for item in old_items if not (isinstance(item, dict) and item.get("id") == item_id)]
        if len(new_items) == len(old_items):
            return False
        data["items"] = new_items
        try:
            _save(data, sha, f"AutoClip: publicar item {item_id} do backlog")
            return True
        except BacklogConflict:
            if attempt == 5:
                raise
            time.sleep(0.5 * attempt)
    return False


def is_buffer_capacity_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "scheduled posts limit reached" in message
        or ("scheduled" in message and "limit" in message and "reached" in message)
        or "10/10" in message
    )


def drain() -> int:
    import autoclip

    items = pending_items()
    if not items:
        print("Backlog vazio: nada para publicar.", flush=True)
        return 0

    client = autoclip.BufferClient()
    published = 0
    print(f"Backlog: {len(items)} item(ns) pendente(s).", flush=True)

    for item in items:
        item_id = str(item.get("id") or "")
        video_url = str(item.get("video_url") or "").strip()
        text = str(item.get("text") or "")
        if not item_id or not video_url:
            raise RuntimeError("Item inválido encontrado no backlog; publicação interrompida para evitar perda de dados.")

        try:
            post_id = client.add_video_to_queue(video_url, text)
        except Exception as exc:
            if is_buffer_capacity_error(exc):
                print(
                    f"Buffer cheio; {len(items) - published} item(ns) continuam aguardando no backlog.",
                    flush=True,
                )
                return 0
            raise RuntimeError(f"Falha ao publicar item {item_id} no Buffer: {exc}") from exc

        remove_item(item_id)
        published += 1
        print(f"Publicado {item_id} -> Buffer {post_id}", flush=True)

    print(f"Backlog drenado: {published} item(ns) enviado(s) ao Buffer.", flush=True)
    return published


def main() -> None:
    parser = argparse.ArgumentParser(description="Backlog persistente de publicação do AutoClip")
    parser.add_argument("--drain", action="store_true", help="Enviar itens pendentes ao Buffer até a fila ficar cheia")
    parser.add_argument("--count", action="store_true", help="Mostrar quantidade de itens pendentes")
    args = parser.parse_args()

    try:
        if args.drain:
            drain()
            return
        if args.count:
            print(pending_count())
            return
        parser.error("use --drain ou --count")
    except Exception as exc:
        print(f"ERRO: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise


if __name__ == "__main__":
    main()
