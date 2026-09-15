from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections import Counter

import requests

import autoclip
import quality_v9_7 as q97
import quality_v9_8 as q98
import quality_v9_9 as q99


_BASE_PREPARE = q99.prepare_clip_content_v9_9
ENTITY_HASHTAG_REPORT: list[dict] = []
_HTTP_HEADERS = {"User-Agent": "AutoClip/9.10 (entity hashtag enrichment)"}
_CACHE: dict[tuple[str, str], object] = {}

_PERSON_WORDS = {
    "actor", "actress", "filmmaker", "director", "singer", "rapper", "musician", "comedian",
    "youtuber", "streamer", "creator", "performer", "television personality", "screenwriter",
}
_NON_PERSON = {
    "Spider Man", "Spider-Man", "Brand New Day", "Marvel Studios", "Marvel Entertainment", "Marvel Cinematic",
    "Stranger Things", "Jean Grey", "Peter Parker", "New York", "Pop Quiz", "Comic Con", "X Men", "X-Men",
}
_LOW_PRIORITY = {
    "film", "movie", "cast", "interview", "podcast", "conversation", "trivia", "popquiz", "quiz",
    "cinema", "gq", "collider", "entertainment",
}
_ROLE_SUFFIXES = {
    "role", "movie", "film", "interview", "trailer", "clip", "cast", "scene", "exclusive", "character",
}


def _clean(value, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _contains(text: str, phrase: str) -> bool:
    return _norm(phrase) in _norm(text) if phrase else False


def _candidate_names(text: str) -> list[str]:
    text = _clean(text)
    found: list[str] = []
    # Two/three-word names are the safest local signal.
    for match in re.finditer(r"\b([A-Z][A-Za-zÀ-ÿ'’.-]{1,24}(?:\s+[A-Z][A-Za-zÀ-ÿ'’.-]{1,24}){1,2})\b", text):
        value = match.group(1).strip(" .,:;!?-'\"")
        if value and value not in _NON_PERSON:
            found.append(value)
    # One-word public names such as Zendaya can be recovered from metadata/title and then validated remotely.
    for match in re.finditer(r"\b([A-Z][a-zÀ-ÿ'’.-]{4,24})\b", text):
        value = match.group(1)
        if value not in _NON_PERSON and value.casefold() not in {"Marvel", "Spider", "Brand", "Interview", "Exclusive"}:
            found.append(value)
    return found


def _source_participants() -> list[str]:
    out: list[str] = []
    intel = q98.SOURCE_INTELLIGENCE or {}
    for item in intel.get("participants", []) if isinstance(intel.get("participants"), list) else []:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"), 100)
        try:
            confidence = float(item.get("confidence", 0) or 0)
        except Exception:
            confidence = 0.0
        if name and confidence >= 0.55:
            out.append(name)
    return out


def _wiki_person(name: str) -> tuple[bool, str]:
    key = ("person", _norm(name))
    if key in _CACHE:
        return _CACHE[key]  # type: ignore[return-value]
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "srsearch": f'"{name}" actor OR actress',
                "srlimit": 3, "format": "json", "utf8": 1,
            },
            headers=_HTTP_HEADERS,
            timeout=7,
        )
        response.raise_for_status()
        results = response.json().get("query", {}).get("search", [])
        if not results:
            _CACHE[key] = (False, "")
            return False, ""
        pageids = "|".join(str(x.get("pageid")) for x in results[:3] if x.get("pageid"))
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "prop": "extracts", "explaintext": 1, "pageids": pageids, "format": "json"},
            headers=_HTTP_HEADERS,
            timeout=7,
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        best = ""
        for page in pages.values():
            title = str(page.get("title") or "")
            extract = str(page.get("extract") or "")
            blob = f"{title} {extract[:2500]}"
            if _norm(name) == _norm(title) or _norm(name) in _norm(blob[:500]):
                if any(word in blob.casefold() for word in _PERSON_WORDS):
                    best = extract
                    break
        result = (bool(best), best)
        _CACHE[key] = result
        return result
    except Exception:
        _CACHE[key] = (False, "")
        return False, ""


def _rank_people(plan, caption: str, source: dict) -> list[dict]:
    title = source.get("title", "")
    desc = source.get("description", "")
    tags = source.get("tags") or []
    participants = _source_participants()
    candidates: set[str] = set(participants)
    for blob in (caption, plan.text, title, desc[:2500], " | ".join(tags[:30])):
        candidates.update(_candidate_names(blob))

    ranked: list[dict] = []
    participant_norm = {_norm(x) for x in participants}
    tag_norm = {_norm(x) for x in tags}
    for name in candidates:
        if len(name) < 5 or name in _NON_PERSON:
            continue
        score = 0
        locations: list[str] = []
        if _contains(caption, name):
            score += 16; locations.append("caption")
        if _contains(plan.text, name):
            score += 13; locations.append("clip")
        if _contains(title, name):
            score += 10; locations.append("title")
        if _norm(name) in participant_norm:
            score += 10; locations.append("source-intelligence")
        if _norm(name) in tag_norm:
            score += 6; locations.append("metadata-tag")
        if _contains(desc, name):
            score += 2; locations.append("description")
        if score < 5:
            continue
        is_person, extract = _wiki_person(name)
        # Source Intelligence can establish a participant even if Wikipedia lookup fails.
        if not is_person and _norm(name) not in participant_norm:
            continue
        ranked.append({"name": name, "score": score, "locations": locations, "wiki": extract})

    ranked.sort(key=lambda x: (-x["score"], len(x["name"])))
    # Merge surname/long-name duplicates.
    out: list[dict] = []
    for item in ranked:
        if any(_norm(item["name"]) in _norm(old["name"]) or _norm(old["name"]) in _norm(item["name"]) for old in out):
            continue
        out.append(item)
        if len(out) >= 5:
            break
    return out


def _work_hint(source: dict, existing_tags: list[str]) -> str:
    title = source.get("title", "")
    # Good for titles such as "... Spider-Man: Brand New Day Role".
    match = re.search(
        r"\b([A-Z][A-Za-z0-9]*(?:[- ][A-Z][A-Za-z0-9]*){0,2}):\s*"
        r"([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,4})",
        title,
    )
    if match:
        left = match.group(1).strip()
        right_words = match.group(2).split()
        while right_words and right_words[-1].casefold() in _ROLE_SUFFIXES:
            right_words.pop()
        if right_words:
            return f"{left}: {' '.join(right_words)}"
    subject = q99._franchise_subject(title)
    if subject:
        return subject
    for tag in existing_tags:
        raw = tag.lstrip("#")
        if _norm(raw) not in _LOW_PRIORITY and len(raw) >= 6:
            return raw
    return ""


def _character_candidates(text: str, person: str) -> list[str]:
    if not text:
        return []
    person_re = re.escape(person)
    patterns = [
        rf"{person_re}.{{0,140}}?\b(?:plays|playing|portrays|portraying|stars as|role as|cast as)\s+(?:the\s+)?(?:X[- ]Men['’]s\s+)?([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){{0,2}})",
        rf"{person_re}['’]s\s+([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){{0,2}})",
        rf"([A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){{0,2}}).{{0,100}}?\b(?:played|portrayed) by\s+{person_re}",
    ]
    out: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            raw = _clean(match.group(1), 80)
            # Restore title-style candidate from case-insensitive regex and trim trailing prose words.
            words = raw.split()
            while words and words[-1].casefold() in _ROLE_SUFFIXES | {"in", "for", "and", "the", "a"}:
                words.pop()
            raw = " ".join(words)
            if not raw or len(words) > 3:
                continue
            low = raw.casefold()
            if any(x in low for x in ("spider-man", "spider man", "brand new", "marvel", "stranger things")):
                continue
            if _norm(raw) == _norm(person):
                continue
            out.append(raw)
    return out


def _wiki_relation_text(person: str, work: str) -> str:
    key = ("wiki-relation", _norm(person + work))
    if key in _CACHE:
        return str(_CACHE[key])
    chunks: list[str] = []
    try:
        query = f'"{person}" "{work}"' if work else f'"{person}" character role'
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json", "utf8": 1},
            headers=_HTTP_HEADERS,
            timeout=7,
        )
        response.raise_for_status()
        results = response.json().get("query", {}).get("search", [])
        pageids = "|".join(str(x.get("pageid")) for x in results if x.get("pageid"))
        if pageids:
            response = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "prop": "extracts", "explaintext": 1, "pageids": pageids, "format": "json"},
                headers=_HTTP_HEADERS,
                timeout=7,
            )
            response.raise_for_status()
            for page in response.json().get("query", {}).get("pages", {}).values():
                chunks.append(str(page.get("extract") or "")[:6000])
    except Exception:
        pass
    text = "\n".join(chunks)
    _CACHE[key] = text
    return text


def _news_relation_items(person: str, work: str) -> list[str]:
    key = ("news", _norm(person + work))
    if key in _CACHE:
        return list(_CACHE[key])  # type: ignore[arg-type]
    items: list[str] = []
    try:
        query = f'"{person}" "{work}" character' if work else f'"{person}" character role'
        response = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers=_HTTP_HEADERS,
            timeout=8,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        for item in root.findall(".//item")[:10]:
            title = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("title") or ""))
            desc = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
            blob = _clean(f"{title}. {desc}", 1600)
            low = blob.casefold()
            if any(x in low for x in ("rumor", "rumoured", "rumored", "reportedly", "may play", "could play", "speculation")):
                continue
            if _contains(blob, person):
                items.append(blob)
    except Exception:
        pass
    _CACHE[key] = items
    return items


def _confirmed_characters(person: str, source_text: str, work: str, wiki_person_text: str = "") -> list[str]:
    local = _character_candidates(source_text, person)
    if local:
        return list(dict.fromkeys(local))[:2]

    votes: Counter[str] = Counter()
    pretty: dict[str, str] = {}
    wiki_text = "\n".join([wiki_person_text, _wiki_relation_text(person, work)])
    for char in _character_candidates(wiki_text, person):
        key = _norm(char)
        votes[key] += 1
        pretty[key] = char
    for item in _news_relation_items(person, work):
        seen_here: set[str] = set()
        for char in _character_candidates(item, person):
            key = _norm(char)
            if key not in seen_here:
                votes[key] += 1
                seen_here.add(key)
                pretty[key] = char
    # External-only relationships need corroboration, preventing rumor hashtags.
    confirmed = [pretty[key] for key, count in votes.most_common() if count >= 2 and key in pretty]
    return confirmed[:2]


def _specific_work_tags(source: dict, work: str) -> list[str]:
    out: list[str] = []
    if work:
        if ":" in work:
            franchise, subtitle = [x.strip() for x in work.split(":", 1)]
            out.extend([q99._tag_from_phrase(franchise), q99._tag_from_phrase(f"{franchise} {subtitle}")])
        else:
            out.append(q99._tag_from_phrase(work))
    # Metadata can contain the exact film/show tag even when the title parser cannot recover it.
    for raw in source.get("tags") or []:
        words = q99._words(raw)
        if 1 <= len(words) <= 5 and any(x in str(raw).casefold() for x in ("spider", "marvel", "x-men", "x men", "brand new day", "stranger things")):
            out.append(q99._tag_from_phrase(str(raw)))
    return [x for x in out if x]


def _merge_entity_tags(plan, prepared: dict, source: dict) -> tuple[list[str], dict]:
    caption = prepared.get("caption", "")
    existing = list(prepared.get("hashtags") or [])
    people = _rank_people(plan, caption, source)
    # Focal = actually relevant to this clip/caption; supporting cast needs title/clip/Source Intelligence evidence.
    focal = [p for p in people if any(x in p["locations"] for x in ("caption", "clip"))][:2]
    if not focal and people:
        focal = people[:1]
    support = [
        p for p in people if p not in focal and any(x in p["locations"] for x in ("title", "clip", "source-intelligence"))
    ][:3]

    work = _work_hint(source, existing)
    source_text = _clean(" ".join([
        source.get("title", ""), source.get("description", ""), " ".join(source.get("tags") or []), plan.text,
    ]), 18000)
    chars: list[str] = []
    for person in focal:
        chars.extend(_confirmed_characters(person["name"], source_text, work, person.get("wiki", "")))
    chars = list(dict.fromkeys(chars))[:3]

    high: list[str] = []
    high.extend(q99._tag_from_phrase(p["name"]) for p in focal)
    high.extend(q99._tag_from_phrase(c) for c in chars)
    high.extend(_specific_work_tags(source, work))

    context = (source_text + " " + " ".join(existing)).casefold()
    for concept in ("Marvel", "MCU", "X-Men", "Spider-Man", "Stranger Things"):
        if concept.casefold() in context:
            high.append(q99._tag_from_phrase(concept))
    high.extend(q99._tag_from_phrase(p["name"]) for p in support)

    # Strong existing entity tags first; generic format tags only fill remaining useful slots.
    strong_existing: list[str] = []
    generic_existing: list[str] = []
    for tag in existing:
        key = _norm(tag)
        if key in _LOW_PRIORITY:
            generic_existing.append(tag)
        else:
            strong_existing.append(tag)

    ordered = high + strong_existing + generic_existing
    out: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        tag = q99._safe_tag(raw)
        key = _norm(tag)
        if not tag or not key or key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= 12:
            break

    report = {
        "caption": caption,
        "focal_people": [p["name"] for p in focal],
        "support_people": [p["name"] for p in support],
        "characters": chars,
        "work": work,
        "hashtags": out,
    }
    return out, report


def prepare_clip_content_v9_10(plans, segments: list[dict], source_language: str | None) -> list[dict]:
    prepared = _BASE_PREPARE(plans, segments, source_language)
    source = q99._metadata()
    ENTITY_HASHTAG_REPORT.clear()

    for idx, (plan, item) in enumerate(zip(plans, prepared), 1):
        tags, report = _merge_entity_tags(plan, item, source)
        item["hashtags"] = tags
        if idx - 1 < len(q97.SOCIAL_CAPTIONS):
            q97.SOCIAL_CAPTIONS[idx - 1]["hashtags"] = tags
        if idx - 1 < len(q99.SOCIAL_GUARD):
            q99.SOCIAL_GUARD[idx - 1]["hashtags"] = tags
        report["clip"] = idx
        ENTITY_HASHTAG_REPORT.append(report)
        entity_bits = []
        if report["focal_people"]:
            entity_bits.append("pessoa=" + ", ".join(report["focal_people"]))
        if report["characters"]:
            entity_bits.append("personagem=" + ", ".join(report["characters"]))
        autoclip.log(
            f"Entity Hashtag Intelligence corte {idx}: {' · '.join(entity_bits) or 'entidades locais'} · "
            + " ".join(tags)
        )
    return prepared


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_prepare = q99.prepare_clip_content_v9_9
    try:
        q99.prepare_clip_content_v9_9 = prepare_clip_content_v9_10
        autoclip.log(
            "Quality v9.10: Entity Hashtag Intelligence · pessoa focal → personagem confirmado → obra/franquia → participantes → formato"
        )
        q99.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Entity Hashtag Intelligence v9.10\n")
        for report in ENTITY_HASHTAG_REPORT:
            autoclip.summary(f"- **Corte {report['clip']}** — {report['caption']}")
            if report["focal_people"]:
                autoclip.summary("  - Pessoa(s) focal(is): " + ", ".join(report["focal_people"]))
            if report["characters"]:
                autoclip.summary("  - Personagem(ns) confirmado(s): " + ", ".join(report["characters"]))
            if report["support_people"]:
                autoclip.summary("  - Participantes secundários: " + ", ".join(report["support_people"]))
            if report["work"]:
                autoclip.summary("  - Obra/franquia: " + report["work"])
            autoclip.summary("  - Hashtags finais: " + " ".join(report["hashtags"]))
        autoclip.summary(
            "\nA v9.10 prioriza **entidades**, não palavras soltas: pessoa do clipe, personagem confirmado, "
            "obra/franquia, universo e participantes reais. Relações ator→personagem obtidas fora do vídeo são "
            "aceitas apenas quando aparecem de forma não especulativa em fontes públicas corroboradas; falhas de rede não bloqueiam o post.\n"
        )
    finally:
        q99.prepare_clip_content_v9_9 = original_prepare
