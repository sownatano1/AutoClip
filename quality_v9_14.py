from __future__ import annotations

import json
import os
import re

import requests

import autoclip
import quality_v9_8 as q98
import quality_v9_9 as q99
import quality_v9_10_1 as q101
import quality_v9_11 as q11
import quality_v9_11_1 as q111
import quality_v9_13 as q13


CAPTION_REPORT: list[dict] = []

_GENERIC_CAPTION_PHRASES = (
    "why this role works so differently",
    "why this filming process works so differently",
    "a closer look at",
    "inside this",
    "this moment",
    "a moment worth watching",
    "what happens next",
    "you won't believe",
    "watch until the end",
)

_EXTRA_STOP = {
    "time", "times", "thing", "things", "something", "anything", "everything",
    "it's", "its", "thats", "that's", "theyre", "they're", "we're", "were",
    "youre", "you're", "im", "i'm", "ive", "i've", "really", "actually",
    "kind", "sort", "just", "like", "know", "think", "says", "said",
}


def _clean(value, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _target_language(language: str) -> str:
    return "Brazilian Portuguese" if language == "pt-BR" else "English"


def _participant_hint() -> str:
    intel = q98.SOURCE_INTELLIGENCE or {}
    names = []
    for item in intel.get("participants") or []:
        if not isinstance(item, dict):
            continue
        try:
            confidence = float(item.get("confidence", 0) or 0)
        except Exception:
            confidence = 0
        name = _clean(item.get("name"), 100)
        if name and confidence >= 0.55:
            names.append(name)
    return ", ".join(names[:5])


def _hybrid_reason(plan) -> str:
    for item in q13.HYBRID_REPORT:
        try:
            if abs(float(item.get("start", -999)) - float(plan.start)) < 0.2 and abs(float(item.get("end", -999)) - float(plan.end)) < 0.2:
                return _clean(item.get("reason"), 300)
        except Exception:
            pass
    return ""


def _caption_is_good(caption: str, plan, source: dict) -> bool:
    caption = _clean(caption, 180)
    words = q99._words(caption)
    if not (5 <= len(words) <= 18):
        return False
    low = caption.casefold()
    if any(x in low for x in _GENERIC_CAPTION_PHRASES):
        return False
    if q11._too_close_to_source(caption, source.get("title", "")):
        return False
    if q99._looks_like_transcript(caption, plan.text):
        return False
    last = re.findall(r"[A-Za-zÀ-ÿ']+", low)
    if last and last[-1] in q11._DANGLING:
        return False
    # Reject the exact failure mode that produced captions such as "Time It'S".
    meaningful = [
        w.casefold() for w in words
        if w.casefold() not in q11._STOP and w.casefold() not in _EXTRA_STOP and len(w) >= 3
    ]
    return len(meaningful) >= 2


def _groq_caption(plan, source: dict, language: str) -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        return ""

    model, supports_images = q13.choose_groq_model(prefer_vision=False)
    if not model:
        return ""

    intel = q98.SOURCE_INTELLIGENCE or {}
    prompt = f"""You are writing the visible TikTok post caption/title for ONE short clip.

LANGUAGE: {_target_language(language)}
SOURCE TITLE: {_clean(source.get('title'), 420)}
SOURCE DESCRIPTION: {_clean(source.get('description'), 900)}
KNOWN PARTICIPANTS FROM TEXT/METADATA: {_participant_hint() or 'none confirmed'}
SOURCE PREMISE: {_clean(intel.get('premise'), 700)}
OPTIONAL VIDEO-VISION CONTEXT: {_clean(intel.get('twelvelabs_visual_context'), 500) or 'none'}
EDITORIAL REVIEW HINT: {_hybrid_reason(plan) or 'none'}

EXACT CLIP TRANSCRIPT:
{_clean(plan.text, 2600)}

Write ONE factual title that explains what actually happens or is discussed in THIS exact clip.

Hard rules:
- 7-15 words preferred; never more than 18.
- State the specific action, story, explanation, opinion, reveal, problem, or payoff.
- If a person's name or a work/title is supported by the supplied text/metadata, use it when it makes the caption clearer.
- Do not identify anybody from appearance.
- Do not use vague templates such as "Why This Role Works So Differently", "A Closer Look At...", "This Moment", or "Inside X".
- Do not copy or lightly shorten the YouTube source title.
- Do not quote the first sentence as the whole title.
- No hashtags.
- Avoid vague pronouns when the reader would not know who "he/she/they/it" refers to.
- Make the title understandable to someone who has not seen the original video.

Return ONLY JSON:
{{"caption":"..."}}"""

    content = [{"type": "text", "text": prompt}]
    if supports_images and q13.SOURCE_VIDEO is not None:
        image = q13._frame_data_url(q13.SOURCE_VIDEO, plan.start + (plan.end - plan.start) * 0.52)
        if image:
            content.append({"type": "image_url", "image_url": {"url": image}})

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.2,
                "max_completion_tokens": 220,
                "response_format": {"type": "json_object"},
            },
            timeout=(6, 16),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:220]}")
        body = response.json()
        choices = body.get("choices") or []
        message = (choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
        parsed = q13._safe_json(message.get("content"))
        caption = _clean(parsed.get("caption"), 180)
        if _caption_is_good(caption, plan, source):
            autoclip.log(f"Context Caption: Groq ({model}) → {caption}")
            return caption
        if caption:
            autoclip.log(f"Context Caption: sugestão Groq rejeitada pelo guard → {caption}")
    except Exception as exc:
        autoclip.log(f"Context Caption: Groq indisponível/ignorado · {exc}")
    return ""


def _source_anchor(source: dict) -> str:
    work = _clean(q101._work_hint_v9_10_1(source, []), 70)
    if work and len(q99._words(work)) <= 6:
        return work

    participants = _participant_hint().split(", ") if _participant_hint() else []
    if len(participants) == 1:
        return participants[0]

    title = re.split(r"\s*[|•]\s*", _clean(source.get("title"), 180))[0]
    title = re.sub(r"^[\"'‘’“”]+|[\"'‘’“”]+$", "", title).strip()
    words = title.split()
    if 2 <= len(words) <= 7:
        return title
    return ""


def _clip_keywords(text: str, limit: int = 3) -> list[str]:
    tokens = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]{2,}", text)
    freq: dict[str, int] = {}
    original: dict[str, str] = {}
    for token in tokens:
        key = token.casefold().strip("'’")
        if key in q11._STOP or key in _EXTRA_STOP or len(key) < 4:
            continue
        freq[key] = freq.get(key, 0) + 1
        original.setdefault(key, token)
    ranked = sorted(freq, key=lambda k: (-freq[k], -len(k)))
    return [original[k] for k in ranked[:limit]]


def _local_context_caption(plan, source: dict, language: str) -> str:
    # Keep the strong deterministic quiz captions from v9.9 when applicable.
    signals = q99._format_signals(source, plan)
    if signals.get("quiz"):
        candidate = q111._BASE_V99_LOCAL_CAPTION(plan, source, language)
        if candidate and _caption_is_good(candidate, plan, source):
            return candidate

    text = plan.text.casefold()
    topic = q11._semantic_topic(plan.text, source)
    anchor = _source_anchor(source)
    prefix = f"{anchor}: " if anchor else ""

    if language == "pt-BR":
        if any(x in text for x in ("filming", "filmed", "shooting", "camera", "on set", "take ")):
            caption = f"{prefix}como o processo de filmagem realmente aconteceu"
        elif any(x in text for x in ("audition", "casting", "cast for")):
            caption = f"{prefix}o que aconteceu durante o teste de elenco"
        elif any(x in text for x in ("role", "character", "playing", "portray")):
            caption = f"{prefix}o que tornou esse papel importante para a história"
        elif any(x in text for x in ("director", "directing", "directed")):
            caption = f"{prefix}como foi trabalhar com o diretor por trás da cena"
        elif any(x in text for x in ("difficult", "hard", "challenge", "challenging")):
            caption = f"{prefix}o que tornou esse momento tão difícil"
        else:
            keys = _clip_keywords(plan.text, 3)
            subject = " e ".join(keys[:2]) if keys else topic
            caption = f"{prefix}a conversa por trás de {subject}"
    else:
        if any(x in text for x in ("filming", "filmed", "shooting", "camera", "on set", "take ")):
            caption = f"{prefix}How the Filming Process Actually Happened"
        elif any(x in text for x in ("audition", "casting", "cast for")):
            caption = f"{prefix}What Happened During the Audition"
        elif any(x in text for x in ("role", "character", "playing", "portray")):
            caption = f"{prefix}What Made This Role Matter to the Story"
        elif any(x in text for x in ("director", "directing", "directed")):
            caption = f"{prefix}What It Was Like Working With the Director"
        elif any(x in text for x in ("difficult", "hard", "challenge", "challenging")):
            caption = f"{prefix}What Made This Moment So Difficult"
        else:
            keys = _clip_keywords(plan.text, 3)
            subject = " and ".join(keys[:2]) if keys else topic
            caption = f"{prefix}The Conversation Behind {subject}"

    caption = _clean(caption, 180).strip(" :;-")
    if _caption_is_good(caption, plan, source):
        return caption

    # Last-resort title uses a clean semantic topic and never raw frequency junk.
    topic = re.sub(r"\b(Time|Thing|Things|It'?s|Really|Actually)\b", "", topic, flags=re.I)
    topic = _clean(topic) or ("the clip's main story" if language != "pt-BR" else "a história principal do clipe")
    if language == "pt-BR":
        return _clean(f"{prefix}o contexto por trás de {topic.lower()}", 180)
    return _clean(f"{prefix}The Context Behind {topic}", 180)


def contextual_caption(plan, source: dict, language: str) -> str:
    caption = _groq_caption(plan, source, language)
    mode = "groq_context" if caption else "local_context"
    if not caption:
        caption = _local_context_caption(plan, source, language)

    CAPTION_REPORT.append({
        "start": round(float(plan.start), 2),
        "end": round(float(plan.end), 2),
        "caption": caption,
        "mode": mode,
    })
    return caption


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_base = q111._BASE_V911_CAPTION
    CAPTION_REPORT.clear()
    try:
        q111._BASE_V911_CAPTION = contextual_caption
        autoclip.log(
            "Quality v9.14: Context Caption Guard · título explica o conteúdo real do clipe · Groq opcional + fallback local"
        )
        q13.run(url, clips_count, min_seconds, max_seconds, whisper_model)

        autoclip.summary("\n### Quality v9.14 — Context Caption Guard\n")
        for idx, item in enumerate(CAPTION_REPORT, 1):
            autoclip.summary(
                f"- **Corte {idx}** — **{item['caption']}** · modo `{item['mode']}`"
            )
        autoclip.summary(
            "\nAs captions sociais agora recebem transcript completo do corte + contexto da fonte. "
            "Templates vagos e títulos baseados em palavras soltas são rejeitados; Groq é apenas opcional e qualquer falha mantém o fallback local.\n"
        )
    finally:
        q111._BASE_V911_CAPTION = original_base
