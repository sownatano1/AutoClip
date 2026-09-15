from __future__ import annotations

import html
import json
import os
import re
from http.cookiejar import MozillaCookieJar

import requests

import autoclip
import quality_v2 as q2
import quality_v6 as q6
import quality_v9_5 as q95
import quality_v9_7 as q97


_BASE_FINAL_SELECT = q95._ORIGINAL_SELECT
SOURCE_INTELLIGENCE: dict = {}


def _clean(value, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit and len(text) > limit else text


def _source_metadata() -> dict:
    info = q97.SOURCE_INFO or {}
    chapters = []
    for chapter in (info.get("chapters") or [])[:40]:
        try:
            start = float(chapter.get("start_time", 0) or 0)
            end = float(chapter.get("end_time", start) or start)
        except Exception:
            continue
        title = _clean(chapter.get("title"), 180)
        if title:
            chapters.append({"start": round(start, 2), "end": round(end, 2), "title": title})

    return {
        "title": _clean(info.get("title") or q6.CURRENT_TITLE, 500),
        "description": _clean(info.get("description"), 5000),
        "channel": _clean(info.get("channel") or info.get("uploader"), 200),
        "uploader": _clean(info.get("uploader"), 200),
        "tags": [_clean(x, 100) for x in (info.get("tags") or []) if _clean(x)][:40],
        "categories": [_clean(x, 100) for x in (info.get("categories") or []) if _clean(x)][:12],
        "language": _clean(info.get("language"), 30),
        "duration": float(info.get("duration") or 0),
        "chapters": chapters,
    }


def _language_order(tracks: dict, info: dict) -> list[str]:
    keys = list(tracks.keys())
    if not keys:
        return []
    preferred = []
    source = _clean(info.get("language")).lower()
    for value in (source, "en", "en-us", "en-gb", "pt-br", "pt"):
        if not value:
            continue
        for key in keys:
            low = key.lower()
            if key not in preferred and (low == value or low.startswith(value + "-") or value.startswith(low + "-")):
                preferred.append(key)
    preferred.extend(k for k in keys if k not in preferred)
    return preferred


def _track_candidate(info: dict):
    for kind, tracks in (("manual", info.get("subtitles") or {}), ("automatic", info.get("automatic_captions") or {})):
        if not isinstance(tracks, dict):
            continue
        for language in _language_order(tracks, info):
            entries = tracks.get(language) or []
            if not isinstance(entries, list):
                continue
            for wanted in ("json3", "vtt"):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    if str(entry.get("ext") or "").lower() == wanted and str(entry.get("url") or "").strip():
                        return kind, language, wanted, str(entry["url"])
    return None


def _requests_cookies():
    path = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if not path or not os.path.isfile(path):
        return None
    try:
        jar = MozillaCookieJar(path)
        jar.load(ignore_discard=True, ignore_expires=True)
        return jar
    except Exception:
        return None


def _dedupe_caption_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        line = _clean(raw)
        if not line:
            continue
        if out and line == out[-1]:
            continue
        # YouTube auto captions can roll the same sentence forward word by word.
        if out and out[-1] in line and len(line) <= len(out[-1]) + 90:
            out[-1] = line
            continue
        if out and line in out[-1]:
            continue
        out.append(line)
    return out


def _parse_json3(text: str) -> str:
    data = json.loads(text)
    lines = []
    for event in data.get("events", []) if isinstance(data, dict) else []:
        if not isinstance(event, dict):
            continue
        parts = []
        for seg in event.get("segs") or []:
            if isinstance(seg, dict):
                parts.append(str(seg.get("utf8") or ""))
        line = "".join(parts).replace("\n", " ")
        if _clean(line):
            lines.append(line)
    return "\n".join(_dedupe_caption_lines(lines))


def _parse_vtt(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "Kind:", "Language:")):
            continue
        if "-->" in line or re.fullmatch(r"\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = html.unescape(line)
        if _clean(line):
            lines.append(line)
    return "\n".join(_dedupe_caption_lines(lines))


def _native_youtube_captions() -> dict:
    info = q97.SOURCE_INFO or {}
    candidate = _track_candidate(info)
    if not candidate:
        return {"available": False, "used": False, "text": "", "language": "", "kind": ""}

    kind, language, ext, url = candidate
    headers = {"User-Agent": "Mozilla/5.0"}
    for key, value in (info.get("http_headers") or {}).items():
        if isinstance(key, str) and isinstance(value, str) and key.lower() != "cookie":
            headers[key] = value
    try:
        response = requests.get(url, headers=headers, cookies=_requests_cookies(), timeout=18)
        response.raise_for_status()
        parsed = _parse_json3(response.text) if ext == "json3" else _parse_vtt(response.text)
        parsed = parsed[:12000].strip()
        if parsed:
            autoclip.log(f"Source Intelligence: legenda nativa do YouTube carregada ({kind}, {language}, {ext})")
            return {
                "available": True,
                "used": True,
                "text": parsed,
                "language": language,
                "kind": kind,
            }
    except Exception as exc:
        autoclip.log(f"Source Intelligence: legenda nativa indisponível; usando Whisper: {exc}")
    return {"available": True, "used": False, "text": "", "language": language, "kind": kind}


def _compact_timeline(segments: list[dict], max_lines: int = 80) -> str:
    raw = q2._editor_transcript(segments, bucket_seconds=45)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) > max_lines:
        chosen = []
        for i in range(max_lines):
            pos = round(i * (len(lines) - 1) / max(1, max_lines - 1))
            chosen.append(lines[pos])
        lines = chosen
    compact = "\n".join(line[:340] for line in lines)
    return compact[:22000]


def _fallback_intelligence(meta: dict, native: dict) -> dict:
    chapters = [
        {"start": c["start"], "end": c["end"], "topic": c["title"], "transition_after": True}
        for c in meta.get("chapters", [])
    ]
    premise = meta.get("description", "")[:600] or meta.get("title", "")
    return {
        "analysis_mode": "metadata_fallback",
        "content_type": q6.PROFILE_LABELS.get(q6.CURRENT_PROFILE, q6.CURRENT_PROFILE),
        "premise": premise,
        "participants": [],
        "topic_map": chapters,
        "editorial_guidance": {
            "context_needed": "Inclua setup suficiente para um espectador novo entender pessoas, assunto e motivo da fala.",
            "strong_endings": ["conclusão da ideia", "resposta", "punchline", "decisão", "afirmação final"],
            "avoid_starting_with": ["frase incompleta", "pronome sem referente", "reação sem contexto"],
            "avoid_ending_on": ["fala incompleta", "transição", "começo de assunto novo depois do payoff"],
            "topic_change_signals": ["mudança clara de pergunta", "novo assunto", "nova história"],
        },
        "native_captions_used": bool(native.get("used")),
        "native_caption_language": native.get("language", ""),
    }


def _source_intelligence(segments: list[dict]) -> dict:
    meta = _source_metadata()
    native = _native_youtube_captions()
    timeline = _compact_timeline(segments)
    fallback = _fallback_intelligence(meta, native)

    if not autoclip.gemini_client():
        return fallback

    prompt = f"""Você é o editor-chefe de um sistema que transforma vídeos longos do YouTube em cortes curtos.
ANTES de escolher qualquer corte, entenda o vídeo inteiro.

METADADOS DO YOUTUBE:
{json.dumps(meta, ensure_ascii=False)}

LEGENDA NATIVA DO YOUTUBE (quando disponível):
{native.get('text') or '(indisponível; use a transcrição Whisper abaixo)'}

MAPA TEMPORAL DA TRANSCRIÇÃO WHISPER DO VÍDEO INTEIRO:
{timeline}

Crie um mapa editorial compacto. Regras:
- diferencie o assunto geral do vídeo dos assuntos locais em cada trecho;
- identifique participantes/nome/papel SOMENTE quando título, descrição, legenda ou transcrição sustentarem isso; nunca deduza identidade pelo rosto;
- marque mudanças de assunto e blocos que parecem histórias/perguntas/ideias completas;
- pense especialmente em onde uma ideia começa com contexto suficiente e onde ela REALMENTE termina;
- um corte não deve continuar alguns segundos dentro de um novo assunto apenas para ficar mais longo;
- um final bom encerra a mesma pergunta/história/ideia com conclusão, resposta, punchline, surpresa, decisão ou afirmação forte;
- não invente fatos.

Responda SOMENTE JSON válido:
{{
  "content_type":"podcast/interview/talking head/gameplay/film/etc",
  "premise":"resumo do propósito e contexto geral",
  "participants":[{{"name":"...","role":"...","confidence":0.0,"evidence":"metadata/transcript"}}],
  "topic_map":[{{"start":0,"end":120,"topic":"...","transition_after":true}}],
  "editorial_guidance":{{
    "context_needed":"...",
    "strong_endings":["..."],
    "avoid_starting_with":["..."],
    "avoid_ending_on":["..."],
    "topic_change_signals":["..."]
  }}
}}
"""
    try:
        data = q2._gemini_json(prompt, attempts=1)
    except Exception:
        data = None
    if not isinstance(data, dict):
        return fallback

    result = fallback.copy()
    result["analysis_mode"] = "source_intelligence_ai"
    result["content_type"] = _clean(data.get("content_type"), 120) or fallback["content_type"]
    result["premise"] = _clean(data.get("premise"), 1200) or fallback["premise"]
    if isinstance(data.get("participants"), list):
        result["participants"] = [x for x in data["participants"][:12] if isinstance(x, dict)]
    if isinstance(data.get("topic_map"), list):
        result["topic_map"] = [x for x in data["topic_map"][:30] if isinstance(x, dict)]
    if isinstance(data.get("editorial_guidance"), dict):
        result["editorial_guidance"] = data["editorial_guidance"]
    return result


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [_clean(x, 180) for x in value if _clean(x)]
    if isinstance(value, str) and _clean(value):
        return [_clean(value, 180)]
    return []


def _brief_for_editor(meta: dict, intel: dict) -> str:
    lines = [
        f"Título real: {meta.get('title') or q6.CURRENT_TITLE}",
        f"Canal: {meta.get('channel') or 'desconhecido'}",
        f"Tipo de conteúdo: {intel.get('content_type') or q6.CURRENT_PROFILE}",
        f"Contexto global: {_clean(intel.get('premise'), 1200)}",
    ]

    participants = []
    for item in intel.get("participants", []) if isinstance(intel.get("participants"), list) else []:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"), 100)
        role = _clean(item.get("role"), 80)
        try:
            confidence = float(item.get("confidence", 0) or 0)
        except Exception:
            confidence = 0.0
        if name and confidence >= 0.55:
            participants.append(f"{name}{f' ({role})' if role else ''}")
    if participants:
        lines.append("Participantes sustentados pelo contexto: " + ", ".join(participants[:8]))

    topics = []
    for item in intel.get("topic_map", []) if isinstance(intel.get("topic_map"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", 0) or 0)
            end = float(item.get("end", start) or start)
        except Exception:
            continue
        topic = _clean(item.get("topic"), 220)
        if topic:
            topics.append(f"- {autoclip.fmt_time(start)}–{autoclip.fmt_time(end)}: {topic}")
    if topics:
        lines.append("Mapa de assuntos:\n" + "\n".join(topics[:18]))

    guidance = intel.get("editorial_guidance") if isinstance(intel.get("editorial_guidance"), dict) else {}
    context = _clean(guidance.get("context_needed"), 500)
    if context:
        lines.append("Contexto necessário nos cortes: " + context)
    strong = _as_list(guidance.get("strong_endings"))
    if strong:
        lines.append("Finais fortes deste vídeo: " + "; ".join(strong[:6]))
    avoid = _as_list(guidance.get("avoid_ending_on"))
    if avoid:
        lines.append("Não terminar em: " + "; ".join(avoid[:6]))
    signals = _as_list(guidance.get("topic_change_signals"))
    if signals:
        lines.append("Sinais de mudança de assunto: " + "; ".join(signals[:6]))

    lines.append(
        "Regra editorial principal: use o menor trecho que entregue contexto + desenvolvimento + payoff; "
        "pare no fechamento da mesma ideia e não invada o próximo assunto."
    )
    return "\n".join(lines)[:6000]


def select_v9_8(segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str):
    meta = _source_metadata()
    intel = _source_intelligence(segments)
    SOURCE_INTELLIGENCE.clear()
    SOURCE_INTELLIGENCE.update(intel)
    brief = _brief_for_editor(meta, intel)

    native_state = "usada" if intel.get("native_captions_used") else "Whisper como fallback"
    autoclip.progress("Source Intelligence", 62, f"Contexto do vídeo entendido · legenda nativa: {native_state}")
    autoclip.log("Source Intelligence: seleção e Final Guard receberão o mesmo mapa editorial")

    enriched_source = f"{source_title}\n\nSOURCE INTELLIGENCE (use isto como contexto editorial):\n{brief}"
    return _BASE_FINAL_SELECT(segments, min_seconds, max_seconds, count, enriched_source)


def _summary_source_intelligence() -> None:
    intel = SOURCE_INTELLIGENCE
    meta = _source_metadata()
    autoclip.summary("\n### Source Intelligence v9.8\n")
    autoclip.summary(f"- **Título:** {meta.get('title') or q6.CURRENT_TITLE}")
    if meta.get("channel"):
        autoclip.summary(f"- **Canal:** {meta['channel']}")
    autoclip.summary(f"- **Tipo/contexto:** {intel.get('content_type') or q6.CURRENT_PROFILE}")
    if intel.get("premise"):
        autoclip.summary(f"- **Leitura global:** {_clean(intel['premise'], 700)}")

    participants = []
    for item in intel.get("participants", []) if isinstance(intel.get("participants"), list) else []:
        if isinstance(item, dict) and _clean(item.get("name")):
            try:
                confidence = float(item.get("confidence", 0) or 0)
            except Exception:
                confidence = 0
            if confidence >= 0.55:
                name = _clean(item.get("name"), 100)
                role = _clean(item.get("role"), 80)
                participants.append(f"{name}{f' ({role})' if role else ''}")
    if participants:
        autoclip.summary("- **Participantes sustentados pelo contexto:** " + ", ".join(participants[:8]))

    topics = intel.get("topic_map") if isinstance(intel.get("topic_map"), list) else []
    autoclip.summary(f"- **Blocos/assuntos mapeados:** {len(topics)}")
    if intel.get("native_captions_used"):
        lang = intel.get("native_caption_language") or "idioma detectado"
        autoclip.summary(f"- **Legenda nativa do YouTube:** usada ({lang})")
    else:
        autoclip.summary("- **Legenda nativa do YouTube:** indisponível/não legível; transcrição Whisper usada como fallback")
    autoclip.summary(
        "\nEsse mapa é criado **antes da escolha dos cortes** e é reutilizado pelo seletor editorial e pelo Final Guard, "
        "para reconhecer melhor contexto, mudanças de assunto e o fechamento real de cada ideia.\n"
    )


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_selector = q95._ORIGINAL_SELECT
    SOURCE_INTELLIGENCE.clear()
    try:
        q95._ORIGINAL_SELECT = select_v9_8
        autoclip.log(
            "Quality v9.8: Source Intelligence · título + descrição + tags + legenda YouTube + Whisper antes dos cortes"
        )
        q97.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        _summary_source_intelligence()
    finally:
        q95._ORIGINAL_SELECT = original_selector
