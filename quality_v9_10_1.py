from __future__ import annotations

import re

import autoclip
import quality_v9_10 as q10


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _work_hint_v9_10_1(source: dict, existing_tags: list[str]) -> str:
    title = _clean(source.get("title", ""))
    metadata_tags = [_clean(x) for x in (source.get("tags") or []) if _clean(x)]

    for colon in [m.start() for m in re.finditer(":", title)]:
        prefix = title[:colon].strip()
        suffix = re.split(r"[|•]", title[colon + 1:], maxsplit=1)[0].strip()

        # Prefer an explicit metadata entity that occurs immediately before the colon.
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
            elif len(words) >= 2 and all(w[:1].isupper() for w in words[-2:]) and words[-2].casefold() not in {
                "talks", "about", "secret", "exclusive", "reveals", "opens", "her", "his", "the",
            }:
                left = " ".join(words[-2:])
            else:
                left = words[-1]

        right_words = []
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", suffix):
            if word.casefold() in {
                "role", "interview", "exclusive", "trailer", "clip", "cast", "scene", "explained",
                "breakdown", "reaction", "reacts", "talks", "opens", "reveals", "on", "with", "and",
            }:
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
    original = q10._work_hint
    try:
        q10._work_hint = _work_hint_v9_10_1
        autoclip.log("Quality v9.10.1: parser de obra/franquia refinado para Entity Hashtag Intelligence")
        q10.run(url, clips_count, min_seconds, max_seconds, whisper_model)
    finally:
        q10._work_hint = original
