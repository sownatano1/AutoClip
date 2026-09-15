from __future__ import annotations

import re

import autoclip
import quality_v9_10 as q10


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _work_hint_v9_10_1(source: dict, existing_tags: list[str]) -> str:
    title = _clean(source.get("title", ""))

    # Find a franchise immediately before ':' instead of swallowing editorial words
    # such as "Secret", "Exclusive" or a person's name earlier in the title.
    for match in re.finditer(r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+)?(?:\s+[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?){0,2})\s*:\s*", title):
        left = match.group(1).strip()
        left_words = left.split()
        # The final title-cased token/group closest to ':' is usually the franchise.
        while len(left_words) > 1 and left_words[0].casefold() in {
            "secret", "exclusive", "new", "the", "her", "his", "their", "our", "about",
        }:
            left_words.pop(0)
        if len(left_words) > 1:
            # If the prefix still looks editorial, prefer the final franchise-like token.
            editorial = {"secret", "exclusive", "about", "role", "talks", "opens", "reveals"}
            while len(left_words) > 1 and left_words[0].casefold() in editorial:
                left_words.pop(0)
        left = " ".join(left_words)

        rest = title[match.end():]
        right_words = []
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", rest):
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

    # Strong metadata tags are safer than guessing from prose.
    for raw in source.get("tags") or []:
        text = _clean(raw)
        if ":" in text and 2 <= len(q10.q99._words(text)) <= 6:
            return text

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
