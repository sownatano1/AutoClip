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
    tokens = re.findall(r"\b[A-Z][A-Za-zÀ-ÿ'’.-]{1,25}\b", _clean(text))
    found: list[str] = []

    # Two-word windows catch names even when a title continues with a capitalized verb:
    # "Sadie Sink Opens..." -> "Sadie Sink", not "Sadie Sink Opens".
    for size in (2, 3):
        for i in range(0, max(0, len(tokens) - size + 1)):
            group = tokens[i:i + size]
            if any(word.casefold() in _EDITORIAL_TOKENS for word in group):
                continue
            value = " ".join(group)
            if value not in q10._NON_PERSON:
                found.append(value)

    # One-word stage names such as Zendaya are allowed, but q10 later validates them
    # against a public-person source before using them.
    for token in tokens:
        if len(token) >= 5 and token.casefold() not in _EDITORIAL_TOKENS and token not in q10._NON_PERSON:
            found.append(token)

    # Add the stricter original extraction too; deduplication happens here before remote validation.
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
        if len(out) >= 24:
            break
    return out


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
    # Keep a stable alias so the enhanced extractor can reuse the original implementation.
    q10._ORIGINAL_CANDIDATE_NAMES = original_names
    try:
        q10._work_hint = _work_hint_v9_10_1
        q10._candidate_names = _candidate_names_v9_10_1
        autoclip.log(
            "Quality v9.10.1: Entity Hashtag Intelligence refinado · nomes de participantes + obra/franquia"
        )
        q10.run(url, clips_count, min_seconds, max_seconds, whisper_model)
    finally:
        q10._work_hint = original_work
        q10._candidate_names = original_names
