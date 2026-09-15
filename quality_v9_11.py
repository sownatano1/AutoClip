from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

import autoclip
import quality_v2 as q2
import quality_v5 as q5
import quality_v6 as q6
import quality_v8 as q8
import quality_v9_5 as q95
import quality_v9_8 as q98
import quality_v9_9 as q99
import quality_v9_10 as q10
import quality_v9_10_1 as q101


_ORIGINAL_ENTITY_MERGE = q10._merge_entity_tags
LOCAL_EDITORIAL_REPORT: list[dict] = []

_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "to", "of", "in", "on", "for", "from", "with", "at",
    "this", "that", "these", "those", "it", "its", "is", "are", "was", "were", "be", "been", "being",
    "i", "im", "i'm", "you", "your", "we", "they", "he", "she", "my", "our", "their", "do", "does",
    "did", "have", "has", "had", "can", "could", "would", "should", "will", "just", "really", "like",
    "yeah", "yes", "no", "okay", "ok", "so", "well", "then", "than", "what", "why", "how", "when",
    "where", "who", "which", "one", "two", "thing", "things", "get", "got", "make", "made", "say", "said",
    "o", "a", "os", "as", "um", "uma", "de", "da", "do", "das", "dos", "e", "ou", "em", "para", "por",
    "com", "que", "isso", "esse", "essa", "ele", "ela", "eu", "voce", "você", "meu", "minha", "nosso",
    "tipo", "entao", "então", "sim", "nao", "não", "como", "quando", "onde", "quem", "qual", "muito",
}

_DANGLING = {
    "and", "but", "because", "so", "or", "if", "when", "which", "that", "with", "to", "for", "from",
    "e", "mas", "porque", "então", "entao", "ou", "se", "quando", "que", "com", "para", "por",
}

_CONTINUATION_STARTS = {
    "and", "but", "because", "so", "or", "which", "that", "then", "also", "because", "where", "when",
    "e", "mas", "porque", "então", "entao", "ou", "que", "também", "tambem", "onde", "quando",
}

_GENERIC_FORMAT = {"interview", "film", "movie", "podcast", "popquiz", "trivia", "cast", "conversation"}


def _clean(value, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _text_between(segments: list[dict], start: float, end: float) -> str:
    return " ".join(
        _clean(s.get("text"))
        for s in segments
        if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end and _clean(s.get("text"))
    ).strip()


def _segment_gap(segments: list[dict], idx: int) -> float:
    if idx + 1 >= len(segments):
        return 99.0
    return max(0.0, float(segments[idx + 1]["start"]) - float(segments[idx]["end"]))


def _strong_boundary(segments: list[dict], idx: int) -> bool:
    text = _clean(segments[idx].get("text"))
    if not text:
        return False
    low = text.casefold().rstrip(" \"'’”")
    last_words = re.findall(r"[A-Za-zÀ-ÿ']+", low)
    if last_words and last_words[-1] in _DANGLING:
        return False
    gap = _segment_gap(segments, idx)
    if re.search(r"[.!?…][\"'’”]?$", text):
        return True
    if gap >= 0.72:
        return True
    return False


def _safe_start(segments: list[dict], start: float) -> float:
    if not segments:
        return start
    idx = next((i for i, s in enumerate(segments) if float(s["end"]) > start), 0)
    current = float(segments[idx]["start"])
    low = max(float(segments[0]["start"]), current - 9.0)
    # Prefer the first segment after a natural boundary; otherwise keep a modest lead-in.
    for j in range(idx - 1, -1, -1):
        if float(segments[j]["end"]) < low:
            break
        if _strong_boundary(segments, j):
            return float(segments[j + 1]["start"]) if j + 1 < len(segments) else current
    for s in segments:
        if low <= float(s["start"]) <= current:
            return float(s["start"])
    return current


def _safe_end(segments: list[dict], start: float, desired_end: float, min_seconds: int, max_seconds: int) -> float:
    """Choose a real speech closure without Gemini.

    It first looks forward for a complete sentence / natural pause. If the hard duration
    limit would cut speech, it retreats to the last safe closure instead of chopping a sentence.
    """
    if not segments:
        return desired_end
    low = start + min_seconds
    hard = min(float(segments[-1]["end"]), start + max_seconds)
    desired = max(low, min(hard, desired_end))

    # Start at the segment that contains or immediately follows the desired endpoint.
    idx = next((i for i, s in enumerate(segments) if float(s["end"]) >= desired), len(segments) - 1)
    forward_limit = min(hard, desired + 24.0)
    best_forward = None
    for j in range(idx, len(segments)):
        end = float(segments[j]["end"])
        if end > forward_limit + 0.05:
            break
        if end < low:
            continue
        if not _strong_boundary(segments, j):
            continue
        # If the next segment is an obvious continuation and starts instantly, keep listening.
        if j + 1 < len(segments):
            nxt = _clean(segments[j + 1].get("text")).casefold()
            first = re.findall(r"[A-Za-zÀ-ÿ']+", nxt)
            gap = _segment_gap(segments, j)
            if first and first[0] in _CONTINUATION_STARTS and gap < 0.45:
                continue
        best_forward = end
        break
    if best_forward is not None:
        return best_forward

    # No safe close ahead before the hard limit: retreat to the latest complete thought.
    lower_back = max(low, desired - 24.0)
    backward = []
    for j, seg in enumerate(segments):
        end = float(seg["end"])
        if lower_back <= end <= hard and _strong_boundary(segments, j):
            backward.append(end)
    if backward:
        return max(backward)

    # Absolute fallback: end at a complete Whisper segment, never in the middle of one.
    ends = [float(s["end"]) for s in segments if low <= float(s["end"]) <= hard]
    return max(ends) if ends else hard


def _opening_penalty(text: str) -> float:
    first = [w.casefold() for w in re.findall(r"[A-Za-zÀ-ÿ']+", text)[:5]]
    if not first:
        return 8.0
    vague = {"he", "she", "it", "they", "this", "that", "ele", "ela", "isso", "esse", "essa", "yeah", "yes", "sim"}
    return 7.0 if first[0] in vague else 0.0


def _ending_bonus(segments: list[dict], end: float) -> float:
    idx = min(range(len(segments)), key=lambda i: abs(float(segments[i]["end"]) - end)) if segments else 0
    if not segments:
        return 0.0
    return 9.0 if _strong_boundary(segments, idx) else -12.0


def select_local_editorial(
    segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str
) -> list[q2.ClipPlan]:
    """Local-first selection: scoring + context + deterministic speech-safe endings."""
    LOCAL_EDITORIAL_REPORT.clear()
    q8.RETENTION_META.clear()
    q6.HOOK_BY_BOUNDS = {}
    if not segments:
        return []

    raw = autoclip.select_best(segments, min_seconds, max_seconds, max(12, count * 10))
    candidates: list[q2.ClipPlan] = []
    for item in raw:
        start = _safe_start(segments, float(item.start))
        desired_end = max(float(item.end), start + min_seconds)
        end = _safe_end(segments, start, desired_end, min_seconds, max_seconds)
        if end - start < min_seconds * 0.9 or end - start > max_seconds + 0.3:
            continue
        text = _text_between(segments, start, end)
        if len(autoclip.words(text)) < 25:
            continue
        score = autoclip.score_window(text, end - start)
        score += _ending_bonus(segments, end)
        score -= _opening_penalty(text)
        # Prefer a concise complete thought over a needlessly long window when scores are similar.
        if end - start > 105:
            score -= min(8.0, (end - start - 105) * 0.10)
        candidates.append(q2.ClipPlan(start, end, round(max(0.0, min(100.0, score)), 1), text, "Local-first editorial selection"))

    candidates.sort(key=lambda p: p.score, reverse=True)
    selected: list[q2.ClipPlan] = []
    for plan in candidates:
        overlap = any(
            max(0.0, min(plan.end, old.end) - max(plan.start, old.start))
            / max(1.0, min(plan.end - plan.start, old.end - old.start)) > 0.25
            for old in selected
        )
        if overlap:
            continue
        selected.append(plan)
        key = (round(plan.start, 2), round(plan.end, 2))
        q8.RETENTION_META[key] = {
            "ending_strength": 90,
            "ending_type": "local_speech_boundary",
            "loopable": False,
            "duration_reason": "Local-first: janela interessante ajustada até um fechamento real de fala.",
        }
        LOCAL_EDITORIAL_REPORT.append({
            "clip": len(selected), "start": plan.start, "end": plan.end, "score": plan.score,
            "ending": "speech-safe local boundary",
        })
        if len(selected) >= count:
            break

    # Extremely defensive fallback if scoring produced nothing.
    if not selected and raw:
        item = raw[0]
        start = _safe_start(segments, float(item.start))
        end = _safe_end(segments, start, float(item.end), min_seconds, max_seconds)
        text = _text_between(segments, start, end)
        selected.append(q2.ClipPlan(start, end, float(item.score), text, "Local-first fallback"))
    return selected[:count]


def _topic_keywords(text: str, limit: int = 4) -> list[str]:
    words = [w.casefold() for w in re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{2,}", text)]
    freq = Counter(w for w in words if w not in _STOP and len(w) >= 4)
    return [word for word, _ in freq.most_common(limit)]


def _source_intelligence_local(segments: list[dict]) -> dict:
    meta = q98._source_metadata()
    native = q98._native_youtube_captions()
    result = q98._fallback_intelligence(meta, native)
    result["analysis_mode"] = "local_first"

    # Build a lightweight topic map locally when YouTube chapters are absent.
    if not result.get("topic_map"):
        duration = float(meta.get("duration") or (segments[-1]["end"] if segments else 0))
        topics = []
        cursor = 0.0
        while cursor < duration and len(topics) < 24:
            end = min(duration, cursor + 90.0)
            text = _text_between(segments, cursor, end)
            keys = _topic_keywords(text, 3)
            label = " / ".join(k.title() for k in keys) if keys else "Conversation"
            topics.append({"start": round(cursor, 2), "end": round(end, 2), "topic": label, "transition_after": True})
            cursor = end
        result["topic_map"] = topics

    # Public-person candidates from the title are validated without any generative AI.
    participants = []
    seen = set()
    for name in q101._candidate_names_v9_10_1(meta.get("title", ""))[:8]:
        key = _norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            is_person, _ = q10._wiki_person(name)
        except Exception:
            is_person = False
        if is_person:
            participants.append({"name": name, "role": "public participant", "confidence": 0.88, "evidence": "title + public validation"})
        if len(participants) >= 6:
            break
    result["participants"] = participants
    premise = _clean(meta.get("description"), 600) or _clean(meta.get("title"), 500)
    result["premise"] = premise
    return result


def _semantic_topic(plan_text: str, source: dict) -> str:
    blob = f"{source.get('title', '')} {plan_text}".casefold()
    if any(x in blob for x in ("imax", "camera", "filming", "filmed", "shooting", "shoot", "on set", "film method", "filming method")):
        title = source.get("title", "")
        owner = re.search(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)[’']s\s+(?:filming|film|method|process)", title)
        if owner:
            return f"{owner.group(1)}'s Filming Process"
        return "Filming Process"
    if any(x in blob for x in ("audition", "casting", "cast for")):
        return "Audition Story"
    if any(x in blob for x in ("stunt", "stunts", "action scene")):
        return "Stunts"
    if any(x in blob for x in ("character", "playing", "portray", "portraying", "role")):
        return "Role"
    if any(x in blob for x in ("script", "screenplay", "writing", "story")):
        return "Story"
    if any(x in blob for x in ("practical effect", "visual effect", "vfx", "cgi", "effect")):
        return "Practical Effects"
    if any(x in blob for x in ("budget", "cost", "million", "money")):
        return "Budget"
    if any(x in blob for x in ("career", "acting", "actor", "actress", "performance")):
        return "Acting Process"
    if any(x in blob for x in ("training", "workout", "practice", "rehearsal")):
        return "Training"
    keys = _topic_keywords(plan_text, 2)
    return " ".join(k.title() for k in keys) if keys else "Behind-the-Scenes Story"


def _too_close_to_source(caption: str, title: str) -> bool:
    cap = " ".join(w.casefold() for w in q99._words(caption))
    src = " ".join(w.casefold() for w in q99._words(title))
    if not cap or not src:
        return False
    if SequenceMatcher(None, cap, src).ratio() >= 0.66:
        return True
    a, b = set(cap.split()), set(src.split())
    return len(a & b) / max(1, len(a)) >= 0.72


def _local_caption_v9_11(plan, source: dict, language: str) -> str:
    signals = q99._format_signals(source, plan)
    # Preserve the good deterministic quiz logic from v9.9.
    if signals.get("quiz"):
        candidate = q99._local_caption(plan, source, language)
        if candidate and not _too_close_to_source(candidate, source.get("title", "")):
            return candidate

    topic = _semantic_topic(plan.text, source)
    work = q101._work_hint_v9_10_1(source, [])

    if language == "pt-BR":
        if work:
            caption = f"Por dentro de {work}: como funciona {topic.lower()}"
        else:
            caption = f"Por que {topic.lower()} funciona de um jeito diferente?"
    else:
        if work:
            caption = f"Inside {work}: How the {topic} Really Works"
        else:
            caption = f"Why This {topic} Works So Differently"

    caption = _clean(caption, 150)
    if _too_close_to_source(caption, source.get("title", "")):
        caption = (f"A Closer Look at the {topic}" if language != "pt-BR" else f"Um olhar mais próximo sobre {topic.lower()}")
    return caption


def _merge_entity_tags_v9_11(plan, prepared: dict, source: dict):
    tags, report = _ORIGINAL_ENTITY_MERGE(plan, prepared, source)
    entity_tags: list[str] = []
    for name in report.get("focal_people", []) + report.get("support_people", []) + report.get("characters", []):
        tag = q99._tag_from_phrase(name)
        if tag:
            entity_tags.append(tag)
    entity_tags.extend(q10._specific_work_tags(source, report.get("work", "")))

    context = f"{source.get('title', '')} {plan.text}"
    for concept in ("Marvel", "MCU", "X-Men", "Spider-Man", "Stranger Things", "IMAX"):
        if concept.casefold() in context.casefold():
            tag = q99._tag_from_phrase(concept)
            if tag:
                entity_tags.append(tag)

    allowed_keys = {_norm(x) for x in entity_tags if x}
    clip_norm = _norm(plan.text)
    title_norm = _norm(source.get("title", ""))
    channel_norm = _norm(source.get("channel", ""))
    signals = q99._format_signals(source, plan)

    ordered = entity_tags + list(tags)
    out: list[str] = []
    seen: set[str] = set()
    generic_used = 0
    for raw in ordered:
        tag = q99._safe_tag(raw)
        key = _norm(tag)
        if not tag or not key or key in seen:
            continue
        # Publisher/source brand is provenance, not a discovery topic for the clip.
        if channel_norm and (key == channel_norm or key in channel_norm or channel_norm in key):
            continue
        if key in _GENERIC_FORMAT:
            relevant = (
                (key in {"interview", "conversation"} and signals.get("interview"))
                or (key in {"film", "movie"} and signals.get("movie"))
                or (key in {"popquiz", "trivia"} and signals.get("quiz"))
                or (key == "cast" and signals.get("cast"))
                or key == "podcast"
            )
            if not relevant or generic_used >= 2:
                continue
            generic_used += 1
        elif key not in allowed_keys:
            # Unclassified metadata tags must actually occur in the selected clip, not merely in source metadata.
            if key not in clip_norm:
                continue
        seen.add(key)
        out.append(tag)
        if len(out) >= 10:
            break

    # Guarantee the actual work/person entities stay first even when metadata is noisy.
    if not out:
        out = [x for x in entity_tags if x][:8]
    report["hashtags"] = out
    return out, report


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_client = autoclip.gemini_client
    original_source_intel = q98._source_intelligence
    original_selector = q98._BASE_FINAL_SELECT
    original_batch = q99._batch_social_v9_9
    original_caption = q99._local_caption
    original_merge = q10._merge_entity_tags
    original_hook_review = q95._refine_hooks_batch

    try:
        # Gemini is removed from the critical path. Nothing below waits for quota/retries.
        autoclip.gemini_client = lambda: None
        q98._source_intelligence = _source_intelligence_local
        q98._BASE_FINAL_SELECT = select_local_editorial
        q99._batch_social_v9_9 = lambda *args, **kwargs: {}
        q99._local_caption = _local_caption_v9_11
        q10._merge_entity_tags = _merge_entity_tags_v9_11
        q95._refine_hooks_batch = lambda *args, **kwargs: {}

        autoclip.log(
            "Quality v9.11: Local-First Editor ativo · Gemini fora do caminho crítico · final de fala + caption + hashtags locais"
        )
        q101.run(url, clips_count, min_seconds, max_seconds, whisper_model)

        autoclip.summary("\n### Local-First Editor v9.11\n")
        autoclip.summary(
            "- **Gemini:** não é usado para seleção, final, hook, caption ou hashtags. Falhas/quota da API não alteram o resultado editorial."
        )
        autoclip.summary(
            "- **Final Speech Guard local:** procura fechamento real de fala; se o limite cair no meio de uma frase, avança até uma conclusão segura ou recua ao último fechamento completo."
        )
        autoclip.summary(
            "- **Caption Originality Guard:** a legenda social é comparada ao título do YouTube e rejeitada quando parece apenas uma cópia/redução dele."
        )
        autoclip.summary(
            "- **Clip-Specific Hashtag Guard:** marcas do canal e tags de metadata sem relação com o trecho são removidas; pessoas, personagem, obra e conceitos do clipe ficam em primeiro lugar."
        )
        for item in LOCAL_EDITORIAL_REPORT:
            autoclip.summary(
                f"- Corte {item['clip']}: {autoclip.fmt_time(item['start'])}–{autoclip.fmt_time(item['end'])} · score {item['score']} · `{item['ending']}`"
            )
        autoclip.summary(
            "\nSe o idioma solicitado exigir tradução e não houver um tradutor local disponível, a v9.11 preserva o idioma original em vez de depender do Gemini ou travar a publicação.\n"
        )
    finally:
        autoclip.gemini_client = original_client
        q98._source_intelligence = original_source_intel
        q98._BASE_FINAL_SELECT = original_selector
        q99._batch_social_v9_9 = original_batch
        q99._local_caption = original_caption
        q10._merge_entity_tags = original_merge
        q95._refine_hooks_batch = original_hook_review
