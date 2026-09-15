from __future__ import annotations

import re

import autoclip
import quality_v9_10 as q10


_EDITORIAL_TOKENS = {
    "opens", "open", "reveals", "reveal", "talks", "talk", "explains", "explain", "tests", "test",
    "tries", "try", "reacts", "react", "shares", "share", "breaks", "break", "discusses", "discuss",
    "interview", "exclusive", "secret", "role", "cast", "trailer", "scene", "movie", "film", "about",
    "with", "and", "versus", "vs", "on", "in", "at", "from", "the", "her", "his", "their",
}


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _candidate_names_v9_10_1(text: str) -> list[str]:
    clean = _clean(text)
    found: list[str] = []

    # Overlapping windows preserve real adjacency in the original text.
    for size in (2, 3):
        pattern = r"(?=(\b" + r"\s+".join([r"[A-Z][A-Za-zÀ-ÿ'’.-]{1,25}"] * size) + r"\b))"
        for match in re.finditer(pattern, clean):
            value = match.group(1)
            group = value.split()
            if any(word.casefold() in _EDITORIAL_TOKENS for word in group):
                continue
            if value not in q10._NON_PERSON:
                found.append(value)

    # Single-name public figures such as Zendaya. Remote validation is mandatory later.
    for token in re.findall(r"\b[A-Z][A-Za-zÀ-ÿ'’.-]{4,25}\b", clean):
        if token.casefold() not in _EDITORIAL_TOKENS and token not in q10._NON_PERSON:
            found.append(token)

    try:
        found.extend(q10._ORIGINAL_CANDIDATE_NAMES(text))
    except Exception:
        pass

    out: list[str] = []
    seen: set[str] = set()
    for value in found:
        key = q10._norm(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= 18:
            break
    return out


def _rank_people_v9_10_1(plan, caption: str, source: dict) -> list[dict]:
    title = source.get("title", "")
    desc = source.get("description", "")
    tags = source.get("tags") or []
    participants = q10._source_participants()
    participant_norm = {q10._norm(x) for x in participants}
    tag_norm = {q10._norm(x) for x in tags}

    candidates: set[str] = set(participants)
    # Single-word names are useful in title/caption/tags, but too noisy in continuous transcript prose.
    for blob, allow_single in (
        (caption, True), (title, True), (plan.text, False), (desc[:2200], False), (" | ".join(tags[:30]), True),
    ):
        for name in _candidate_names_v9_10_1(blob):
            if allow_single or " " in name:
                candidates.add(name)

    scored: list[dict] = []
    for name in candidates:
        if len(name) < 5 or name in q10._NON_PERSON:
            continue
        score = 0
        locations: list[str] = []
        if q10._contains(caption, name):
            score += 16; locations.append("caption")
        if q10._contains(plan.text, name):
            score += 13; locations.append("clip")
        if q10._contains(title, name):
            score += 10; locations.append("title")
        if q10._norm(name) in participant_norm:
            score += 10; locations.append("source-intelligence")
        if q10._norm(name) in tag_norm:
            score += 6; locations.append("metadata-tag")
        if q10._contains(desc, name):
            score += 2; locations.append("description")
        if score >= 5:
            scored.append({"name": name, "score": score, "locations": locations})

    scored.sort(key=lambda x: (-x["score"], len(x["name"])))
    validated: list[dict] = []
    # Only the strongest candidates reach public validation; this keeps v9.10 fast.
    for item in scored[:10]:
        is_known_participant = q10._norm(item["name"]) in participant_norm
        is_person, extract = q10._wiki_person(item["name"])
        if not is_person and not is_known_participant:
            continue
        item["wiki"] = extract
        if any(
            q10._norm(item["name"]) in q10._norm(old["name"])
            or q10._norm(old["name"]) in q10._norm(item["name"])
            for old in validated
        ):
            continue
        validated.append(item)
        if len(validated) >= 5:
            break
    return validated


def _work_hint_v9_10_1(source: dict, existing_tags: list[str]) -> str:
    title = _clean(source.get("title", ""))
    metadata_tags = [_clean(x) for x in (source.get("tags") or []) if _clean(x)]

    for colon in [m.start() for m in re.finditer(":", title)]:
        prefix = title[:colon].strip()
        suffix = re.split(r"[|•]", title[colon + 1:], maxsplit=1)[0].strip()

        matching = []
        for tag in metadata_tags:
            if ":" in tag:
                continue
            if q10._norm(tag) and q10._norm(prefix).endswith(q10._norm(tag)) and len(q10.q99._words(tag)) <= 4:
                matching.append(tag)
        if matching:
            left = max(matching, key=len)
        else:
            words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", prefix)
            if not words:
                continue
            if "-" in words[-1]:
                left = words[-1]
            elif len(words) >= 2 and all(w[:1].isupper() for w in words[-2:]) and words[-2].casefold() not in _EDITORIAL_TOKENS:
                left = " ".join(words[-2:])
            else:
                left = words[-1]

        right_words = []
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", suffix):
            if word.casefold() in _EDITORIAL_TOKENS | {"explained", "breakdown"}:
                break
            if word[:1].isupper() and len(right_words) < 5:
                right_words.append(word)
            else:
                break
        if left and right_words:
            return f"{left}: {' '.join(right_words)}"

    for raw in metadata_tags:
        if ":" in raw and 2 <= len(q10.q99._words(raw)) <= 6:
            return raw

    subject = q10.q99._franchise_subject(title)
    if subject:
        return subject

    for tag in existing_tags:
        raw = tag.lstrip("#")
        if q10._norm(raw) not in q10._LOW_PRIORITY and len(raw) >= 6:
            return raw
    return ""


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_work = q10._work_hint
    original_names = q10._candidate_names
    original_rank = q10._rank_people
    q10._ORIGINAL_CANDIDATE_NAMES = original_names
    try:
        q10._work_hint = _work_hint_v9_10_1
        q10._candidate_names = _candidate_names_v9_10_1
        q10._rank_people = _rank_people_v9_10_1
        autoclip.log(
            "Quality v9.10.1: Entity Hashtag Intelligence refinado · participantes ranqueados + personagem + obra/franquia"
        )
        q10.run(url, clips_count, min_seconds, max_seconds, whisper_model)
    finally:
        q10._work_hint = original_work
        q10._candidate_names = original_names
        q10._rank_people = original_rank
