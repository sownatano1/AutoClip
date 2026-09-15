from __future__ import annotations

import json
import os
import re
from collections import Counter

import autoclip
import quality_v2 as q2
import quality_v5 as q5
import quality_v6 as q6
import quality_v9_6 as q96


_BASE_CAPTURE = q6._capture_download
_BASE_PREPARE = q5.prepare_clip_content
SOURCE_INFO: dict = {}
SOCIAL_CAPTIONS: list[dict] = []


def _capture_download_v9_7(url, target_dir):
    video, info = _BASE_CAPTURE(url, target_dir)
    SOURCE_INFO.clear()
    SOURCE_INFO.update(info or {})
    return video, info


def _target_language() -> str:
    return q5._normalize_language(os.getenv("SUBTITLE_LANGUAGE", "English"))


def _source_context() -> dict:
    info = SOURCE_INFO
    tags = info.get("tags") or []
    categories = info.get("categories") or []
    return {
        "title": str(info.get("title") or q6.CURRENT_TITLE or "").strip()[:400],
        "description": re.sub(r"\s+", " ", str(info.get("description") or "")).strip()[:2200],
        "channel": str(info.get("channel") or info.get("uploader") or "").strip()[:160],
        "uploader": str(info.get("uploader") or "").strip()[:160],
        "tags": [str(x).strip() for x in tags if str(x).strip()][:30],
        "categories": [str(x).strip() for x in categories if str(x).strip()][:10],
    }


def _clean_caption(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"\s*#\w+.*$", "", text).strip()
    return text[:220].rstrip(" ,;:-")


def _clean_hashtag(tag: str) -> str:
    text = str(tag or "").strip()
    if not text:
        return ""
    text = text.lstrip("#")
    text = re.sub(r"[^\wÀ-ÿ]", "", text, flags=re.UNICODE)
    if len(text) < 2:
        return ""
    return "#" + text[:48]


def _normalize_tags(tags: list[str], fallback: list[str]) -> list[str]:
    combined = list(tags or []) + list(fallback or [])
    out: list[str] = []
    seen: set[str] = set()
    for tag in combined:
        cleaned = _clean_hashtag(tag)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= 18:
            break
    return out


def _profile_tags() -> list[str]:
    profile = q6.CURRENT_PROFILE
    if profile == "podcast":
        return ["#podcast", "#interview", "#conversation", "#clips", "#viralclips", "#tiktok"]
    if profile == "talking":
        return ["#creator", "#storytime", "#clips", "#shortvideo", "#tiktok"]
    if profile == "gameplay":
        return ["#gaming", "#gameplay", "#gamer", "#clips", "#tiktok"]
    if profile == "film":
        return ["#movie", "#film", "#cinema", "#actors", "#scene", "#clips", "#tiktok"]
    return ["#clips", "#shortvideo", "#tiktok"]


def _keyword_tags(text: str, limit: int = 8) -> list[str]:
    stop = {
        "this", "that", "with", "from", "have", "your", "they", "what", "when", "where", "which",
        "about", "there", "would", "could", "should", "because", "just", "like", "really", "video",
        "the", "and", "for", "you", "are", "was", "were", "has", "had", "not", "but",
        "isso", "essa", "esse", "para", "como", "quando", "onde", "porque", "muito", "mais", "sobre",
        "uma", "que", "com", "por", "dos", "das", "ele", "ela", "eles", "elas", "não", "nao",
    }
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9][\wÀ-ÿ'-]{2,}", text)
    freq = Counter(t for t in tokens if t.casefold() not in stop)
    return ["#" + re.sub(r"[^\wÀ-ÿ]", "", token) for token, _ in freq.most_common(limit)]


def _hook_for_plan(plan) -> str:
    return re.sub(r"\s+", " ", q6.HOOK_BY_BOUNDS.get((round(plan.start, 2), round(plan.end, 2)), "")).strip()


def _fallback_social(plan, source: dict) -> tuple[str, str, list[str], list[str]]:
    hook = _hook_for_plan(plan)
    caption = hook or " ".join(re.sub(r"\s+", " ", plan.text).split()[:12]).strip(" .,:;!?")
    caption = _clean_caption(caption) or "A moment worth hearing"
    title = caption[:80]
    keyword_source = " ".join([
        source.get("title", ""),
        source.get("channel", ""),
        plan.text[:900],
    ])
    tags = _normalize_tags(_keyword_tags(keyword_source, 10), _profile_tags())
    return title, caption, tags, []


def _prepare_subtitles(plan, segments: list[dict], source_language: str | None, language: str) -> list[dict]:
    raw = q5._raw_segments_for_plan(plan, segments)
    source = (source_language or "").lower()
    if language == "en" and source and not source.startswith("en"):
        return q5._translate_batch_to_english(raw)
    # PT-BR translation is handled in the batch social request below to avoid another model call.
    return raw


def _batch_social_and_translation(plans, raw_segments_by_clip: list[list[dict]], source_language: str | None, language: str):
    client = autoclip.gemini_client()
    if not client:
        return {}

    source = _source_context()
    target_text = {
        "en": "English",
        "pt-BR": "Portuguese (Brazil)",
        "original": "the same language as the source dialogue",
    }.get(language, "English")

    clips = []
    for idx, (plan, raw) in enumerate(zip(plans, raw_segments_by_clip), 1):
        clips.append({
            "clip": idx,
            "start": round(plan.start, 2),
            "end": round(plan.end, 2),
            "hook": _hook_for_plan(plan),
            "dialogue": [{"i": i, "text": str(s.get("text") or "").strip()} for i, s in enumerate(raw)],
        })

    prompt = f'''You are the social-copy editor for short-form videos that will be posted through Buffer to TikTok.

SOURCE VIDEO METADATA:
{json.dumps(source, ensure_ascii=False)}

SOURCE LANGUAGE: {source_language or 'unknown'}
OUTPUT LANGUAGE FOR CAPTION: {target_text}
CONTENT PROFILE: {q6.PROFILE_LABELS.get(q6.CURRENT_PROFILE, q6.CURRENT_PROFILE)}

For EACH clip, do all of the following:

1) UNDERSTAND CONTEXT
- Read the source title/description/channel/tags AND the clip dialogue.
- Determine the actual subject of this clip, not just the overall video's topic.

2) IDENTIFY PUBLIC SPEAKERS CONSERVATIVELY
- speaker_names may contain a famous/public person's name ONLY when that name is supported by the source metadata or the dialogue/context strongly enough to attribute the clip.
- Do NOT identify anyone from face appearance. Do NOT guess a celebrity from visual identity.
- If multiple named people are present but it is unclear which one is speaking in this clip, leave speaker_names empty rather than guessing.
- speaker_roles may contain a short widely-known role such as actor, YouTuber, athlete, musician, director, creator, etc. Omit uncertain roles.

3) WRITE A SHORT SOCIAL CAPTION
- Make caption feel like a strong hook, normally 5-14 words and one short sentence.
- If a confidently identified famous speaker is relevant, naturally include their name when that improves discovery/context.
- Keep it factual and grounded in this clip.
- Do not write generic clickbait such as "You won't believe this" or "Watch until the end".
- Do not add hashtags inside caption.
- Do not write a long explanation.

4) HASHTAGS FOR DISCOVERY
- Return 12-18 UNIQUE hashtags when enough relevant concepts exist.
- Prefer highly relevant discoverability tags over random spam.
- Mix: public-person names, movie/show/franchise/game/topic, interview/podcast/content format, niche/category, and a few broad discovery tags.
- Example pattern for a Spider-Man cast interview, ONLY if supported: #TomHolland #SpiderMan #Marvel #Zendaya #Movie #Interview #Actors #MCU #Film #Cinema #MovieClips #Entertainment #ViralClips #TikTok
- Never add a celebrity/franchise/topic not supported by the metadata/dialogue.
- Hashtags must contain no spaces.

5) SUBTITLE TRANSLATION
- If output language is Portuguese (Brazil), translate each dialogue segment faithfully into natural Brazilian Portuguese.
- If output language is English and the source is not English, translate faithfully to English.
- Otherwise return the original segment text.
- Preserve each segment i exactly.

Return ONLY valid JSON:
{{"clips":[{{"clip":1,"title":"short internal title","caption":"short hook-like caption","speaker_names":["Name"],"speaker_roles":["actor"],"hashtags":["#Tag"],"translations":[{{"i":0,"text":"..."}}]}}]}}

CLIPS:
{json.dumps(clips, ensure_ascii=False)}'''

    try:
        model = autoclip.env("GEMINI_MODEL", "gemini-3.8-flash")
        response = client.models.generate_content(model=model, contents=prompt)
        data = autoclip.extract_json(response.text or "")
        if isinstance(data, dict) and isinstance(data.get("clips"), list):
            out = {}
            for item in data["clips"]:
                try:
                    out[int(item.get("clip"))] = item
                except Exception:
                    pass
            return out
    except Exception as exc:
        autoclip.log(f"Social Caption v9.7: Gemini indisponível; usando fallback factual: {exc}")
    return {}


def prepare_clip_content_v9_7(plans, segments: list[dict], source_language: str | None) -> list[dict]:
    language = _target_language()
    source = _source_context()
    raw_by_clip = [q5._raw_segments_for_plan(plan, segments) for plan in plans]
    generated = _batch_social_and_translation(plans, raw_by_clip, source_language, language)

    SOCIAL_CAPTIONS.clear()
    prepared: list[dict] = []
    for idx, plan in enumerate(plans, 1):
        raw = [dict(s) for s in raw_by_clip[idx - 1]]
        item = generated.get(idx) or {}
        translations = item.get("translations") if isinstance(item, dict) else None
        if isinstance(translations, list):
            for tr in translations:
                try:
                    pos = int(tr.get("i", -1))
                    text = re.sub(r"\s+", " ", str(tr.get("text") or "")).strip()
                    if 0 <= pos < len(raw) and text:
                        raw[pos]["text"] = text
                except Exception:
                    pass
        elif language == "en" and (source_language or "").lower() and not (source_language or "").lower().startswith("en"):
            raw = q5._translate_batch_to_english(raw)

        fallback_title, fallback_caption, fallback_tags, _ = _fallback_social(plan, source)
        title = str(item.get("title") or fallback_title).strip()[:80] if isinstance(item, dict) else fallback_title
        caption = _clean_caption(item.get("caption") if isinstance(item, dict) else "") or fallback_caption
        names = [re.sub(r"\s+", " ", str(x)).strip() for x in (item.get("speaker_names") or []) if str(x).strip()] if isinstance(item, dict) else []
        roles = [re.sub(r"\s+", " ", str(x)).strip() for x in (item.get("speaker_roles") or []) if str(x).strip()] if isinstance(item, dict) else []
        model_tags = [str(x) for x in (item.get("hashtags") or [])] if isinstance(item, dict) else []
        tags = _normalize_tags(model_tags, fallback_tags + _profile_tags())

        # Keep the Buffer text short up top, with discovery tags below it.
        prepared.append({
            "segments": raw,
            "title": title,
            "caption": caption,
            "hashtags": tags,
        })
        SOCIAL_CAPTIONS.append({
            "clip": idx,
            "caption": caption,
            "hashtags": tags,
            "speaker_names": names,
            "speaker_roles": roles,
        })
        who = ", ".join(names) if names else "não atribuído com segurança"
        autoclip.log(f"Social Caption corte {idx}: {caption} · pessoa(s): {who} · {len(tags)} hashtags")

    return prepared


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_capture = q6._capture_download
    original_prepare = q5.prepare_clip_content
    try:
        q6._capture_download = _capture_download_v9_7
        q5.prepare_clip_content = prepare_clip_content_v9_7
        autoclip.log("Quality v9.7: Social Caption contextual para Buffer · pessoas públicas conservadoras · 12-18 hashtags relevantes")
        q96.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Social Caption v9.7 — Buffer/TikTok\n")
        for item in SOCIAL_CAPTIONS:
            people = ", ".join(item["speaker_names"]) if item["speaker_names"] else "não atribuído com segurança"
            roles = ", ".join(item["speaker_roles"]) if item["speaker_roles"] else "—"
            autoclip.summary(f"- **Corte {item['clip']}** — `{item['caption']}`")
            autoclip.summary(f"  - Pessoa(s): **{people}** · papel: {roles}")
            autoclip.summary("  - Hashtags: " + " ".join(item["hashtags"]))
        autoclip.summary(
            "\nA legenda enviada ao Buffer usa uma frase curta estilo hook e hashtags específicas do contexto. "
            "Nomes de pessoas públicas só entram quando título/descrição/transcrição dão suporte suficiente; "
            "o sistema não identifica celebridades pelo rosto.\n"
        )
    finally:
        q6._capture_download = original_capture
        q5.prepare_clip_content = original_prepare
