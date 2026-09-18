from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

import requests

import autoclip
import quality_v2 as q2
import quality_v6 as q6
import quality_v8 as q8
import quality_v9_8 as q98
import quality_v9_11 as q11
import quality_v9_12 as q12


_BASE_LOCAL_INTELLIGENCE = q11._source_intelligence_local
_BASE_LOCAL_SELECTOR = q11.select_local_editorial
SOURCE_VIDEO: Path | None = None
TWELVELABS_RESULT: dict = {}
GROQ_RESULT: dict = {}
HYBRID_REPORT: list[dict] = []


def _enabled() -> bool:
    return os.getenv("HYBRID_AI_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _clean(value, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _safe_json(value) -> dict:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _capture_source_download(url: str, target_dir: Path):
    global SOURCE_VIDEO
    video, info = _ORIGINAL_DOWNLOAD(url, target_dir)
    SOURCE_VIDEO = Path(video)
    return video, info


_ORIGINAL_DOWNLOAD = q6._ORIGINAL_DOWNLOAD


def _twelvelabs_key() -> str:
    return os.getenv("TWELVELABS_API_KEY", "").strip()


def _groq_key() -> str:
    return os.getenv("GROQ_API_KEY", "").strip()


def _delete_twelvelabs_asset(asset_id: str) -> None:
    key = _twelvelabs_key()
    if not key or not asset_id:
        return
    try:
        requests.delete(
            f"https://api.twelvelabs.io/v1.3/assets/{asset_id}",
            headers={"x-api-key": key},
            timeout=10,
        )
    except Exception:
        pass


def _twelvelabs_analyze() -> dict:
    """Best-effort full-video understanding. Every failure returns {}."""
    key = _twelvelabs_key()
    path = SOURCE_VIDEO
    if not _enabled() or not key or path is None or not path.is_file():
        return {}

    try:
        size_mb = path.stat().st_size / 1024 / 1024
    except Exception:
        return {}
    if size_mb > 195:
        autoclip.log(
            f"Hybrid Intelligence: TwelveLabs pulado porque a fonte tem {size_mb:.0f} MB (>195 MB para upload direto)."
        )
        return {}

    asset_id = ""
    try:
        autoclip.log("Hybrid Intelligence: TwelveLabs analisando o vídeo como camada opcional")
        mime = "video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream"
        with path.open("rb") as handle:
            response = requests.post(
                "https://api.twelvelabs.io/v1.3/assets",
                headers={"x-api-key": key},
                data={"method": "direct"},
                files={"file": (path.name, handle, mime)},
                timeout=(10, 55),
            )
        if response.status_code >= 400:
            raise RuntimeError(f"upload HTTP {response.status_code}: {response.text[:240]}")
        uploaded = response.json()
        asset_id = str(uploaded.get("_id") or uploaded.get("id") or "").strip()
        if not asset_id:
            raise RuntimeError("upload não devolveu asset_id")

        wait_seconds = max(10, min(55, int(os.getenv("TWELVELABS_READY_TIMEOUT", "40"))))
        deadline = time.monotonic() + wait_seconds
        ready = False
        while time.monotonic() < deadline:
            state = requests.get(
                f"https://api.twelvelabs.io/v1.3/assets/{asset_id}",
                headers={"x-api-key": key},
                timeout=12,
            )
            if state.status_code >= 400:
                raise RuntimeError(f"asset HTTP {state.status_code}")
            data = state.json()
            status = str(data.get("status") or "").lower()
            if status == "ready":
                ready = True
                break
            if status == "failed":
                raise RuntimeError("asset marcado como failed")
            time.sleep(4)

        if not ready:
            raise RuntimeError(f"asset não ficou pronto em {wait_seconds}s; seguindo localmente")

        prompt = (
            "Analyze this video as an editor of short-form clips. Do not identify people from faces. "
            "Return ONLY valid JSON with: premise (string), visual_context (string), "
            "strong_moments (array of objects with start, end, score 0-100, reason), "
            "topic_changes (array with start and topic), and ending_notes (array of strings). "
            "Strong moments must be understandable, visually engaging, and likely to contain a complete thought. "
            "Use timestamps in seconds. Keep at most 8 strong moments."
        )
        analysis = requests.post(
            "https://api.twelvelabs.io/v1.3/analyze",
            headers={"x-api-key": key, "Content-Type": "application/json"},
            json={
                "model_name": "pegasus1.5",
                "video": {"type": "asset_id", "asset_id": asset_id},
                "prompt": prompt,
                "stream": False,
                "temperature": 0.1,
                "max_tokens": 1200,
            },
            timeout=(10, 40),
        )
        if analysis.status_code >= 400:
            raise RuntimeError(f"analyze HTTP {analysis.status_code}: {analysis.text[:240]}")
        body = analysis.json()
        parsed = _safe_json(body.get("data") or body.get("text") or body)
        if not parsed:
            raise RuntimeError("resposta sem JSON editorial utilizável")
        autoclip.log(
            f"Hybrid Intelligence: TwelveLabs OK · {len(parsed.get('strong_moments') or [])} momento(s) sugeridos"
        )
        return parsed
    except Exception as exc:
        autoclip.log(f"Hybrid Intelligence: TwelveLabs indisponível/ignorado · {exc}")
        return {}
    finally:
        if asset_id:
            _delete_twelvelabs_asset(asset_id)


def _hybrid_source_intelligence(segments: list[dict]) -> dict:
    local = _BASE_LOCAL_INTELLIGENCE(segments)
    TWELVELABS_RESULT.clear()
    if not _enabled():
        return local

    extra = _twelvelabs_analyze()
    if not extra:
        return local

    TWELVELABS_RESULT.update(extra)
    result = dict(local)
    result["analysis_mode"] = "local_first+twelvelabs_optional"
    result["twelvelabs_visual_context"] = _clean(extra.get("visual_context"), 900)
    result["twelvelabs_strong_moments"] = [
        x for x in (extra.get("strong_moments") or [])[:8] if isinstance(x, dict)
    ]
    result["twelvelabs_topic_changes"] = [
        x for x in (extra.get("topic_changes") or [])[:12] if isinstance(x, dict)
    ]
    # Local metadata remains canonical; provider notes are additive only.
    return result


def _frame_data_url(video: Path, seconds: float) -> str:
    import cv2

    cap = cv2.VideoCapture(str(video))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            return ""
    finally:
        cap.release()

    h, w = frame.shape[:2]
    if w > 640:
        scale = 640.0 / w
        frame = cv2.resize(frame, (640, max(1, int(h * scale))))
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 68])
    if not ok:
        return ""
    b64 = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _groq_review(candidates: list[q2.ClipPlan]) -> dict:
    """One low-cost multimodal call. Failure means no reranking."""
    key = _groq_key()
    video = SOURCE_VIDEO
    if not _enabled() or not key or video is None or not video.is_file() or not candidates:
        return {}

    meta = q98._source_metadata()
    content: list[dict] = []
    instructions = (
        "You are a short-form video editor reviewing candidates that were already cut safely by a local editor. "
        "Do NOT change timestamps and do NOT identify people from faces. Use names only when the supplied text/metadata says them. "
        "Rank candidates for retention, understandable context, complete idea/payoff, and visual interest. "
        "Return ONLY JSON: {\"ranking\":[{\"candidate\":1,\"score\":0-100,"
        "\"visual_interest\":0-100,\"complete_idea\":true,\"reason\":\"short reason\"}]}. "
        "Score every candidate exactly once.\n"
        f"Source title: {_clean(meta.get('title'), 350)}\n"
        f"Content profile: {q6.CURRENT_PROFILE}\n"
    )
    if TWELVELABS_RESULT:
        instructions += (
            "Optional video-understanding hints from another service (treat as hints, not facts): "
            + _clean(json.dumps(TWELVELABS_RESULT, ensure_ascii=False), 1800)
            + "\n"
        )
    content.append({"type": "text", "text": instructions})

    usable = candidates[:5]
    for idx, plan in enumerate(usable, 1):
        mid = plan.start + (plan.end - plan.start) * 0.52
        content.append(
            {
                "type": "text",
                "text": (
                    f"Candidate {idx}: {plan.start:.2f}-{plan.end:.2f}s; "
                    f"local_score={plan.score}; transcript={_clean(plan.text, 1500)}"
                ),
            }
        )
        image = _frame_data_url(video, mid)
        if image:
            content.append({"type": "image_url", "image_url": {"url": image}})

    try:
        autoclip.log(f"Hybrid Intelligence: Groq revisando {len(usable)} candidato(s) com texto + frames")
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b"),
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.1,
                "max_completion_tokens": 900,
                "response_format": {"type": "json_object"},
            },
            timeout=(8, 28),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:260]}")
        body = response.json()
        choices = body.get("choices") or []
        message = (choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
        parsed = _safe_json(message.get("content"))
        ranking = parsed.get("ranking") if isinstance(parsed, dict) else None
        if not isinstance(ranking, list):
            raise RuntimeError("resposta sem ranking JSON")
        autoclip.log("Hybrid Intelligence: Groq OK · reranking opcional recebido")
        return parsed
    except Exception as exc:
        autoclip.log(f"Hybrid Intelligence: Groq indisponível/ignorado · {exc}")
        return {}


def _twelve_bonus(plan: q2.ClipPlan) -> float:
    moments = TWELVELABS_RESULT.get("strong_moments") if isinstance(TWELVELABS_RESULT, dict) else []
    if not isinstance(moments, list):
        return 0.0
    center = (plan.start + plan.end) / 2
    best = 0.0
    for item in moments:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", 0) or 0)
            end = float(item.get("end", start) or start)
            score = float(item.get("score", 70) or 70)
        except Exception:
            continue
        if start <= center <= max(start, end):
            best = max(best, 2.0 + min(6.0, max(0.0, score - 50.0) * 0.12))
    return best


def _hybrid_selector(
    segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str
) -> list[q2.ClipPlan]:
    # Ask the deterministic editor for extra alternatives. With no provider response,
    # the first N candidates are exactly the normal local-first result.
    candidate_count = max(count, min(5, max(3, count * 2)))
    candidates = _BASE_LOCAL_SELECTOR(
        segments, min_seconds, max_seconds, candidate_count, source_title
    )
    if not candidates:
        return candidates

    GROQ_RESULT.clear()
    review = _groq_review(candidates)
    if review:
        GROQ_RESULT.update(review)

    groq_by_idx: dict[int, dict] = {}
    for item in (review.get("ranking") or []) if isinstance(review, dict) else []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("candidate"))
        except Exception:
            continue
        if 1 <= idx <= len(candidates):
            groq_by_idx[idx] = item

    ranked = []
    for idx, plan in enumerate(candidates, 1):
        groq = groq_by_idx.get(idx) or {}
        try:
            g_score = max(0.0, min(100.0, float(groq.get("score", 50) or 50)))
            visual = max(0.0, min(100.0, float(groq.get("visual_interest", 50) or 50)))
        except Exception:
            g_score, visual = 50.0, 50.0

        if groq:
            provider = g_score * 0.78 + visual * 0.22
            hybrid = plan.score * 0.78 + provider * 0.22
            if groq.get("complete_idea") is False:
                hybrid -= 5.0
        else:
            hybrid = plan.score

        twelve = _twelve_bonus(plan)
        hybrid += twelve
        ranked.append((hybrid, idx, plan, g_score if groq else None, visual if groq else None, twelve, groq))

    # Providers only choose among already safe local candidates.
    ranked.sort(key=lambda x: (-x[0], x[1]))
    selected = [row[2] for row in ranked[:count]]

    # Restore the local report so it reflects only clips that will actually render.
    q11.LOCAL_EDITORIAL_REPORT.clear()
    HYBRID_REPORT.clear()
    for pos, plan in enumerate(selected, 1):
        row = next(r for r in ranked if r[2] is plan)
        _, original_idx, _, g_score, visual, twelve, groq = row
        q11.LOCAL_EDITORIAL_REPORT.append(
            {
                "clip": pos,
                "start": plan.start,
                "end": plan.end,
                "score": plan.score,
                "ending": "speech-safe local boundary",
            }
        )
        HYBRID_REPORT.append(
            {
                "clip": pos,
                "candidate": original_idx,
                "start": plan.start,
                "end": plan.end,
                "local_score": plan.score,
                "hybrid_score": round(row[0], 1),
                "groq_score": None if g_score is None else round(g_score, 1),
                "visual_interest": None if visual is None else round(visual, 1),
                "twelvelabs_bonus": round(twelve, 1),
                "reason": _clean(groq.get("reason"), 220) if isinstance(groq, dict) else "",
            }
        )

    return selected


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    global _ORIGINAL_DOWNLOAD, SOURCE_VIDEO

    original_download = q6._ORIGINAL_DOWNLOAD
    original_intelligence = q11._source_intelligence_local
    original_selector = q11.select_local_editorial

    SOURCE_VIDEO = None
    TWELVELABS_RESULT.clear()
    GROQ_RESULT.clear()
    HYBRID_REPORT.clear()
    _ORIGINAL_DOWNLOAD = original_download

    try:
        q6._ORIGINAL_DOWNLOAD = _capture_source_download
        q11._source_intelligence_local = _hybrid_source_intelligence
        q11.select_local_editorial = _hybrid_selector

        groq_state = "configurado" if _groq_key() else "sem chave → fallback local"
        twelve_state = "configurado" if _twelvelabs_key() else "sem chave → fallback local"
        autoclip.log(
            "Quality v9.13: Hybrid Intelligence opcional · "
            f"Groq={groq_state} · TwelveLabs={twelve_state} · local-first sempre"
        )

        q12.run(url, clips_count, min_seconds, max_seconds, whisper_model)

        autoclip.summary("\n### Quality v9.13 — Hybrid Intelligence\n")
        autoclip.summary(
            "- **Local-first:** continua obrigatório e suficiente. Groq/TwelveLabs nunca são necessários para concluir um vídeo."
        )
        autoclip.summary(
            f"- **TwelveLabs:** {'usado como dica visual/temporal' if TWELVELABS_RESULT else 'não usado/indisponível — sem impacto no processamento'}."
        )
        autoclip.summary(
            f"- **Groq:** {'usado para reranquear candidatos locais' if GROQ_RESULT else 'não usado/indisponível — ordem local preservada'}."
        )
        for item in HYBRID_REPORT:
            providers = []
            if item["groq_score"] is not None:
                providers.append(
                    f"Groq {item['groq_score']:g}/100 · visual {item['visual_interest']:g}/100"
                )
            if item["twelvelabs_bonus"] > 0:
                providers.append(f"TwelveLabs +{item['twelvelabs_bonus']:g}")
            extra = " · " + " · ".join(providers) if providers else " · somente local"
            autoclip.summary(
                f"- **Corte {item['clip']}** — candidato local {item['candidate']} · "
                f"score local {item['local_score']:g} · score híbrido {item['hybrid_score']:g}{extra}"
            )
            if item["reason"]:
                autoclip.summary(f"  - Groq: {item['reason']}")
        autoclip.summary(
            "\nTimeout, quota, 401/429/5xx, resposta inválida ou ausência de chave fazem a camada externa ser simplesmente ignorada. "
            "Os limites de início/fim continuam sendo definidos pelo Speech Guard local.\n"
        )
    finally:
        q6._ORIGINAL_DOWNLOAD = original_download
        q11._source_intelligence_local = original_intelligence
        q11.select_local_editorial = original_selector
