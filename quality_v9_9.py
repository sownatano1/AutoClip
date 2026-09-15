from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

import autoclip
import quality_v5 as q5
import quality_v6 as q6
import quality_v9_7 as q97
import quality_v9_8 as q98


SOCIAL_GUARD: list[dict] = []

_BANNED_TAGS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "to", "of", "for", "from", "with",
    "this", "that", "these", "those", "its", "it's", "thats", "that's", "im", "i'm", "ive", "i've",
    "you", "your", "youre", "you're", "we", "were", "we're", "they", "theyre", "they're", "he", "she",
    "it", "is", "are", "was", "be", "been", "being", "have", "has", "had", "do", "does", "did", "say",
    "says", "said", "think", "thinks", "thought", "one", "two", "what", "why", "how", "when", "where",
    "who", "which", "yeah", "yes", "no", "okay", "ok", "like", "really", "just", "secret", "cap",
    "um", "uma", "o", "a", "os", "as", "de", "da", "do", "das", "dos", "e", "ou", "que", "isso",
    "esse", "essa", "ele", "ela", "eles", "elas", "eu", "voce", "você", "meu", "minha", "para", "por",
    "como", "quando", "onde", "quem", "qual", "sim", "nao", "não", "tipo", "muito", "mais", "fala",
}


def _clean(text, limit: int = 0) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit] if limit else value


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ0-9]+(?:[-'][A-Za-zÀ-ÿ0-9]+)?", str(text or ""))


def _target_language() -> str:
    return q5._normalize_language(__import__("os").getenv("SUBTITLE_LANGUAGE", "English"))


def _metadata() -> dict:
    try:
        return q98._source_metadata()
    except Exception:
        return q97._source_context()


def _clip_topics(plan) -> list[str]:
    topics: list[str] = []
    intel = q98.SOURCE_INTELLIGENCE or {}
    for item in intel.get("topic_map", []) if isinstance(intel.get("topic_map"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", 0) or 0)
            end = float(item.get("end", start) or start)
        except Exception:
            continue
        if end > plan.start and start < plan.end:
            topic = _clean(item.get("topic"), 220)
            if topic:
                topics.append(topic)
    return topics[:6]


def _context_blob(plan, source: dict) -> str:
    intel = q98.SOURCE_INTELLIGENCE or {}
    parts = [
        source.get("title", ""),
        source.get("description", ""),
        source.get("channel", ""),
        " ".join(source.get("tags") or []),
        _clean(intel.get("premise"), 1200),
        " ".join(_clip_topics(plan)),
        plan.text,
    ]
    return _clean(" ".join(str(x) for x in parts if x), 16000)


def _franchise_subject(title: str) -> str:
    title = _clean(title)
    quoted = re.search(r"[‘'\"]([^’'\"]{2,90})[’'\"]", title)
    if quoted:
        subject = quoted.group(1).strip()
        if ":" in subject:
            subject = subject.split(":", 1)[0].strip()
        if 1 <= len(_words(subject)) <= 5:
            return subject

    match = re.search(r"(.{2,80}?)\s+Cast\s+(?:Test|Tests|Try|Tries)\b", title, re.IGNORECASE)
    if match:
        subject = re.sub(r"^[\s'\"‘’]+|[\s'\"‘’]+$", "", match.group(1)).strip()
        if ":" in subject:
            subject = subject.split(":", 1)[0].strip()
        words = _words(subject)
        if words:
            return " ".join(words[-4:])

    match = re.search(r"Their\s+(.{2,50}?)\s+Knowledge", title, re.IGNORECASE)
    if match:
        return _clean(match.group(1), 60)
    return ""


def _knowledge_topic(title: str) -> str:
    match = re.search(r"Their\s+(.{2,60}?)\s+Knowledge", title, re.IGNORECASE)
    return _clean(match.group(1), 70) if match else ""


def _format_signals(source: dict, plan) -> dict[str, bool]:
    title = source.get("title", "")
    desc = source.get("description", "")
    clip = plan.text
    all_source = f"{title} {desc}".casefold()
    clip_low = clip.casefold()
    return {
        "quiz": any(x in all_source for x in ("pop quiz", "quiz", "trivia", "test their", "tests their", "knowledge challenge")),
        "quote": any(x in clip_low for x in ("finish the line", "finish this line", "quote", "who said", "said this", "secret, cap", "secret cap")),
        "interview": any(x in all_source for x in ("interview", "conversation", "talks with", "sits down")),
        "cast": " cast " in f" {all_source} ",
        "movie": any(x in all_source for x in ("movie", "film", "actor", "actors", "cast", "cinema")),
    }


def _local_caption(plan, source: dict, language: str) -> str:
    title = source.get("title", "")
    signals = _format_signals(source, plan)
    subject = _franchise_subject(title)
    knowledge = _knowledge_topic(title) or subject

    if language == "pt-BR":
        if signals["quiz"] and signals["quote"]:
            if subject:
                return f"O elenco de {subject} consegue completar esta frase icônica?"
            return "Eles conseguem completar esta frase icônica?"
        if signals["quiz"] and subject:
            return f"Quanto o elenco de {subject} realmente sabe?"
        if signals["quiz"] and knowledge:
            return f"Este desafio coloca o conhecimento sobre {knowledge} à prova"
    else:
        if signals["quiz"] and signals["quote"]:
            if subject:
                return f"Can the {subject} Cast Finish This Iconic Quote?"
            return "Can They Finish This Iconic Quote?"
        if signals["quiz"] and subject:
            return f"How Well Does the {subject} Cast Know Their World?"
        if signals["quiz"] and knowledge:
            return f"This {knowledge} Challenge Puts Their Knowledge to the Test"

    hook = _clean(q97._hook_for_plan(plan), 140)
    if hook and not _looks_like_transcript(hook, plan.text):
        return hook

    core = re.split(r"\s*[|•]\s*", title)[0].strip(" ‘'\"")
    if core:
        words = core.split()
        if len(words) > 13:
            core = " ".join(words[:13]).rstrip(" ,;:-")
        return core
    return "A Moment Worth Watching" if language != "pt-BR" else "Um momento que vale assistir"


def _looks_like_transcript(caption: str, transcript: str) -> bool:
    cap_words = [w.casefold() for w in _words(caption)]
    if not cap_words:
        return True
    cap_norm = " ".join(cap_words)
    tr_words = [w.casefold() for w in _words(transcript)]
    tr_norm = " ".join(tr_words)
    if len(cap_words) >= 4 and cap_norm in tr_norm:
        return True
    if len(cap_words) >= 5:
        cap_set = set(cap_words)
        overlap = len(cap_set & set(tr_words)) / max(1, len(cap_set))
        if overlap >= 0.88:
            return True
    first = " ".join(tr_words[: max(6, len(cap_words))])
    if SequenceMatcher(None, cap_norm, first).ratio() >= 0.82:
        return True
    return False


def _valid_caption(caption: str, plan) -> bool:
    caption = _clean(caption)
    words = _words(caption)
    if not (4 <= len(words) <= 16):
        return False
    if caption.count("?") + caption.count("!") > 2:
        return False
    low = caption.casefold()
    if any(x in low for x in ("watch until the end", "you won't believe", "you wont believe", "this is crazy")):
        return False
    return not _looks_like_transcript(caption, plan.text)


def _tag_from_phrase(value: str) -> str:
    words = [w for w in _words(value) if _norm(w) and _norm(w) not in {_norm(x) for x in _BANNED_TAGS}]
    if not words or len(words) > 5:
        return ""
    joined = "".join(w if w.isupper() and len(w) <= 6 else w[:1].upper() + w[1:] for w in words)
    joined = re.sub(r"[^A-Za-zÀ-ÿ0-9]", "", joined)
    if len(joined) < 3 or len(joined) > 48:
        return ""
    return "#" + joined


def _safe_tag(tag: str) -> str:
    raw = str(tag or "").strip().lstrip("#")
    if not raw:
        return ""
    low = _norm(raw)
    banned_norm = {_norm(x) for x in _BANNED_TAGS}
    if not low or low in banned_norm or low.isdigit() or len(low) < 3:
        return ""
    # Keep already useful CamelCase/acronym tags; otherwise convert phrases safely.
    if " " not in raw and re.fullmatch(r"[A-Za-zÀ-ÿ0-9_-]+", raw):
        cleaned = re.sub(r"[_-]+", "", raw)
        if _norm(cleaned) in banned_norm:
            return ""
        return "#" + cleaned[:48]
    return _tag_from_phrase(raw)


def _title_tags(source: dict) -> list[str]:
    title = source.get("title", "")
    out: list[str] = []
    subject = _franchise_subject(title)
    if subject:
        out.append(_tag_from_phrase(subject))

    quoted = re.search(r"[‘'\"]([^’'\"]{2,90})[’'\"]", title)
    if quoted and ":" in quoted.group(1):
        sequel = quoted.group(1).split(":", 1)[1].strip()
        if sequel:
            out.append(_tag_from_phrase(sequel))

    for part in re.split(r"\s*[|•]\s*", title)[1:]:
        part = _clean(part, 40)
        if 1 <= len(_words(part)) <= 3:
            out.append(_tag_from_phrase(part))
    return [x for x in out if x]


def _metadata_tags(source: dict, plan) -> list[str]:
    context = _norm(source.get("title", "") + " " + plan.text)
    out: list[str] = []
    for raw in source.get("tags") or []:
        tag = _safe_tag(raw)
        if not tag:
            continue
        tag_norm = _norm(tag)
        raw_norm = _norm(raw)
        # Metadata tags must either match the selected clip/title or be short named concepts.
        if raw_norm and (raw_norm in context or (len(_words(raw)) <= 3 and len(raw_norm) >= 5)):
            out.append(tag)
        if len(out) >= 5:
            break
    return out


def _local_tags(plan, source: dict) -> list[str]:
    signals = _format_signals(source, plan)
    candidates: list[str] = []
    candidates.extend(_title_tags(source))
    candidates.extend(_metadata_tags(source, plan))

    clip = plan.text
    for acronym in re.findall(r"\b[A-Z]{2,6}\b", clip):
        if _norm(acronym) not in {_norm(x) for x in _BANNED_TAGS}:
            candidates.append("#" + acronym)

    full_context = (source.get("title", "") + " " + source.get("description", "") + " " + clip).casefold()
    for concept in ("Marvel", "Hulk", "Spider-Man", "Avengers"):
        if concept.casefold() in full_context:
            candidates.append(_tag_from_phrase(concept))

    if signals["quiz"]:
        candidates.extend(["#PopQuiz", "#Trivia"])
    if signals["interview"]:
        candidates.append("#Interview")
    if signals["cast"]:
        candidates.append("#Cast")
    if signals["movie"]:
        candidates.append("#Film")

    channel = _clean(source.get("channel"), 30)
    if channel and len(_words(channel)) <= 2:
        candidates.append(_tag_from_phrase(channel))

    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        tag = _safe_tag(candidate)
        key = _norm(tag)
        if not tag or not key or key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= 10:
            break
    return out


def _validate_model_tags(tags, plan, source: dict) -> list[str]:
    local = _local_tags(plan, source)
    local_norm = {_norm(x) for x in local}
    context_norm = _norm(_context_blob(plan, source))
    out: list[str] = []
    seen: set[str] = set()

    for raw in list(tags or []) + local:
        tag = _safe_tag(raw)
        key = _norm(tag)
        if not tag or not key or key in seen:
            continue
        # AI tags need explicit textual support; deterministic local tags are already vetted.
        if raw not in local and key not in local_norm and key not in context_norm:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= 10:
            break
    return out


def _batch_social_v9_9(plans, raw_by_clip: list[list[dict]], source_language: str | None, language: str):
    client = autoclip.gemini_client()
    if not client:
        return {}

    source = _metadata()
    intel = q98.SOURCE_INTELLIGENCE or {}
    target = {"en": "English", "pt-BR": "Brazilian Portuguese", "original": "the source language"}.get(language, "English")
    clips = []
    for idx, (plan, raw) in enumerate(zip(plans, raw_by_clip), 1):
        clips.append({
            "clip": idx,
            "start": round(plan.start, 2),
            "end": round(plan.end, 2),
            "source_topics": _clip_topics(plan),
            "dialogue": [{"i": i, "text": _clean(s.get("text"), 500)} for i, s in enumerate(raw)],
        })

    prompt = f'''You are the packaging editor for short-form video posts.

SOURCE METADATA:
{json.dumps(source, ensure_ascii=False)}

SOURCE INTELLIGENCE:
{json.dumps({"content_type": intel.get("content_type"), "premise": intel.get("premise"), "participants": intel.get("participants"), "topic_map": intel.get("topic_map")}, ensure_ascii=False)}

OUTPUT LANGUAGE: {target}
SOURCE LANGUAGE: {source_language or 'unknown'}

For EACH selected clip:

1) CAPTION = A TITLE/HOOK FOR WHAT THE VIEWER IS ABOUT TO WATCH.
- 5-12 words is ideal; maximum 16.
- Describe the premise, challenge, reveal, story, opinion, mistake, explanation or payoff of the CLIP.
- It should read like a strong human-written video title, not like subtitles.
- DO NOT copy the opening dialogue or return a raw quote as the whole caption.
- A short quote may appear only as a small anchor inside a broader title when useful.
- Example of BAD caption: "That's my secret Cap. What? That's my secret"
- Example of GOOD packaging for a supported quiz context: "Can the Spider-Man Cast Finish This Iconic Quote?"
- Avoid generic clickbait such as "You won't believe this" or "Watch until the end".
- Use a public person's name only when metadata/dialogue/context supports that the person is relevant to this exact clip.

2) HASHTAGS = ONLY MEANINGFUL DISCOVERY TERMS.
- Return 6-10 unique hashtags, not 12-18 filler tags.
- Every hashtag must be an actual entity, franchise, person, show/movie/game, character/topic, or format that is directly supported by metadata/context/dialogue.
- NEVER turn ordinary transcript words, pronouns, contractions or verbs into hashtags.
- Forbidden examples: #Its #Thats #Im #Say #Think #One #What #Really.
- Prefer #SpiderMan #Marvel #MCU #Hulk #PopQuiz style tags when and only when those concepts are genuinely supported.
- Do not add #viral, #fyp, #tiktok, #clips merely to fill space.

3) SUBTITLES
- If output language is Brazilian Portuguese, faithfully translate each dialogue segment.
- If output language is English and source is not English, faithfully translate each dialogue segment.
- Otherwise preserve the original segment text.
- Preserve every segment index i exactly.

Return ONLY valid JSON:
{{"clips":[{{"clip":1,"caption":"...","speaker_names":[],"speaker_roles":[],"hashtags":["#Tag"],"translations":[{{"i":0,"text":"..."}}]}}]}}

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
        autoclip.log(f"Social Caption Guard: IA indisponível; usando fallback semântico local: {exc}")
    return {}


def prepare_clip_content_v9_9(plans, segments: list[dict], source_language: str | None) -> list[dict]:
    language = _target_language()
    source = _metadata()
    raw_by_clip = [q5._raw_segments_for_plan(plan, segments) for plan in plans]
    generated = _batch_social_v9_9(plans, raw_by_clip, source_language, language)

    q97.SOCIAL_CAPTIONS.clear()
    SOCIAL_GUARD.clear()
    prepared: list[dict] = []

    for idx, plan in enumerate(plans, 1):
        raw = [dict(s) for s in raw_by_clip[idx - 1]]
        item = generated.get(idx) if isinstance(generated.get(idx), dict) else {}
        translations = item.get("translations") if isinstance(item, dict) else None
        if isinstance(translations, list):
            for tr in translations:
                try:
                    pos = int(tr.get("i", -1))
                    text = _clean(tr.get("text"), 700)
                    if 0 <= pos < len(raw) and text:
                        raw[pos]["text"] = text
                except Exception:
                    pass
        elif language == "en" and (source_language or "").lower() and not (source_language or "").lower().startswith("en"):
            raw = q5._translate_batch_to_english(raw)

        model_caption = _clean(item.get("caption"), 220) if isinstance(item, dict) else ""
        caption = model_caption if _valid_caption(model_caption, plan) else _local_caption(plan, source, language)
        caption = q97._clean_caption(caption)

        model_tags = item.get("hashtags") if isinstance(item, dict) and isinstance(item.get("hashtags"), list) else []
        tags = _validate_model_tags(model_tags, plan, source)
        if len(tags) < 4:
            tags = _local_tags(plan, source)

        names = [_clean(x, 100) for x in (item.get("speaker_names") or []) if _clean(x)] if isinstance(item, dict) else []
        roles = [_clean(x, 80) for x in (item.get("speaker_roles") or []) if _clean(x)] if isinstance(item, dict) else []

        prepared.append({"segments": raw, "title": caption[:80], "caption": caption, "hashtags": tags})
        q97.SOCIAL_CAPTIONS.append({
            "clip": idx,
            "caption": caption,
            "hashtags": tags,
            "speaker_names": names,
            "speaker_roles": roles,
        })
        source_mode = "AI validada" if model_caption and caption == q97._clean_caption(model_caption) else "fallback semântico local"
        SOCIAL_GUARD.append({"clip": idx, "caption": caption, "hashtags": tags, "mode": source_mode})
        autoclip.log(f"Social Caption Guard corte {idx}: {caption} · {len(tags)} hashtags · {source_mode}")

    return prepared


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_prepare = q97.prepare_clip_content_v9_7
    try:
        q97.prepare_clip_content_v9_7 = prepare_clip_content_v9_9
        autoclip.log(
            "Quality v9.9: Social Caption Guard · caption como título do clipe · hashtags sem palavras vazias · fallback semântico"
        )
        q98.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Social Caption Guard v9.9\n")
        for item in SOCIAL_GUARD:
            autoclip.summary(f"- **Corte {item['clip']}** — **{item['caption']}**")
            autoclip.summary(f"  - Modo: `{item['mode']}`")
            autoclip.summary("  - Hashtags: " + " ".join(item["hashtags"]))
        autoclip.summary(
            "\nA legenda social agora funciona como **título/hook do conteúdo do clipe**, não como cópia da primeira fala. "
            "Hashtags passam por filtro semântico e palavras comuns/contrações como `Its`, `Thats`, `Im`, `say`, "
            "`think` e `one` são rejeitadas mesmo quando o Gemini está sem quota.\n"
        )
    finally:
        q97.prepare_clip_content_v9_7 = original_prepare
