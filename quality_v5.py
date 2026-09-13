from __future__ import annotations

import json
import os
import re
from pathlib import Path

import autoclip
import quality_v2 as q2
import quality_v4 as q4

_ORIGINAL_PREPARE = q2.prepare_clip_content


def _normalize_language(value: str) -> str:
    text = (value or "English").strip().lower()
    if text in {"english", "en", "inglês", "ingles"}:
        return "en"
    if text in {"português (brasil)", "portugues (brasil)", "pt-br", "pt_br", "portuguese", "português", "portugues"}:
        return "pt-BR"
    return "original"


def _caption_chunks(text: str, max_words: int = 4, max_chars: int = 24) -> list[str]:
    tokens = re.sub(r"\s+", " ", text).strip().split()
    chunks: list[str] = []
    current: list[str] = []
    for token in tokens:
        proposed = " ".join(current + [token])
        if current and (len(current) >= max_words or len(proposed) > max_chars):
            chunks.append(" ".join(current))
            current = [token]
        else:
            current.append(token)
    if current:
        chunks.append(" ".join(current))
    return chunks


def write_compact_ass(segments: list[dict], start: float, end: float, target: Path) -> None:
    """Same lower-screen placement as v4, with a moderate size increase."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,DejaVu Sans,60,&H00FFFFFF,&H000000FF,&H00000000,&H50000000,0,0,0,0,100,100,0,0,1,2.8,0,2,90,90,155,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events: list[str] = []
    for seg in segments:
        seg_start = max(start, float(seg["start"]))
        seg_end = min(end, float(seg["end"]))
        text = re.sub(r"\s+", " ", str(seg.get("text") or "")).strip()
        if seg_end <= seg_start or not text:
            continue
        chunks = _caption_chunks(text)
        if not chunks:
            continue
        weights = [max(1, len(c.split())) for c in chunks]
        total_weight = sum(weights)
        consumed = 0
        for pos, chunk in enumerate(chunks):
            cue_start = seg_start + (seg_end - seg_start) * (consumed / total_weight)
            consumed += weights[pos]
            cue_end = seg_end if pos == len(chunks) - 1 else seg_start + (seg_end - seg_start) * (consumed / total_weight)
            if cue_end - cue_start < 0.16:
                cue_end = min(seg_end, cue_start + 0.16)
            safe = chunk.replace("{", "(").replace("}", ")").replace("\n", " ")
            events.append(
                f"Dialogue: 0,{q4._ass_timestamp(cue_start-start)},{q4._ass_timestamp(cue_end-start)},Default,,0,0,0,,{safe}"
            )
    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _raw_segments_for_plan(plan: q2.ClipPlan, segments: list[dict]) -> list[dict]:
    return [
        dict(s)
        for s in segments
        if float(s.get("end", 0)) > plan.start and float(s.get("start", 0)) < plan.end
    ]


def _english_fallback(text: str) -> tuple[str, str, list[str]]:
    clean = re.sub(r"\s+", " ", text).strip()
    title = " ".join(clean.split()[:10]).strip(" .,:;!?")[:80] or "Highlight"
    caption = clean[:350]
    return title, caption, ["#podcast", "#clips", "#conversation", "#tiktok"]


def _generate_metadata(text: str, language: str) -> tuple[str, str, list[str]]:
    if language == "pt-BR":
        return autoclip.generate_copy(text, "pt")

    fallback = _english_fallback(text) if language == "en" else autoclip.fallback_copy(text)
    client = autoclip.gemini_client()
    if not client:
        return fallback

    target = "English" if language == "en" else "the same language as the source text"
    model = autoclip.env("GEMINI_MODEL", "gemini-3.8-flash")
    prompt = f'''Create TikTok metadata in {target}, based ONLY on the excerpt below.
Return ONLY valid JSON: {{"title":"...","caption":"...","hashtags":["#...", "#..."]}}.
Title max 80 characters, caption max 350 characters, 5-8 hashtags. Do not invent facts.
Excerpt:\n{text[:12000]}'''
    try:
        response = client.models.generate_content(model=model, contents=prompt)
        data = autoclip.extract_json(response.text or "")
        title = str(data.get("title") or "").strip()[:80]
        caption = str(data.get("caption") or "").strip()[:350]
        tags = [str(x).strip() for x in data.get("hashtags", []) if str(x).strip()][:8]
        if title and caption:
            return title, caption, tags
    except Exception as exc:
        autoclip.log(f"Aviso: metadata no idioma escolhido falhou: {exc}")
    return fallback


def _translate_batch_to_english(raw: list[dict]) -> list[dict]:
    """Used only when English is requested but the source itself is not English."""
    client = autoclip.gemini_client()
    if not client or not raw:
        return raw
    model = autoclip.env("GEMINI_MODEL", "gemini-3.8-flash")
    payload = [{"i": i, "text": str(s.get("text") or "").strip()} for i, s in enumerate(raw)]
    prompt = (
        'Translate the following subtitle segments into natural, faithful English. '
        'Do not summarize or add information. Return ONLY JSON in the format '
        '[{"i":0,"text":"..."}].\n' + json.dumps(payload, ensure_ascii=False)
    )
    translated = [dict(s) for s in raw]
    try:
        response = client.models.generate_content(model=model, contents=prompt)
        data = autoclip.extract_json(response.text or "")
        if isinstance(data, list):
            for item in data:
                idx = int(item.get("i", -1))
                text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
                if 0 <= idx < len(translated) and text:
                    translated[idx]["text"] = text
    except Exception as exc:
        autoclip.log(f"Aviso: tradução para inglês falhou; mantendo idioma original: {exc}")
    return translated


def prepare_clip_content(plans: list[q2.ClipPlan], segments: list[dict], source_language: str | None) -> list[dict]:
    language = _normalize_language(os.getenv("SUBTITLE_LANGUAGE", "English"))
    source = (source_language or "").lower()

    # Portuguese keeps the previous translation path. English and Original bypass
    # Portuguese translation entirely, which is important for English-source podcasts.
    if language == "pt-BR":
        return _ORIGINAL_PREPARE(plans, segments, source_language)

    prepared: list[dict] = []
    for plan in plans:
        raw = _raw_segments_for_plan(plan, segments)
        if language == "en" and source and not source.startswith("en"):
            subtitle_segments = _translate_batch_to_english(raw)
        else:
            subtitle_segments = raw

        metadata_text = " ".join(str(s.get("text") or "").strip() for s in subtitle_segments).strip() or plan.text
        title, caption, hashtags = _generate_metadata(metadata_text, language)
        prepared.append({
            "segments": subtitle_segments,
            "title": title,
            "caption": caption,
            "hashtags": hashtags,
        })
    return prepared


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    """Reuse the proven v4 editor/framing, replacing only subtitle size/language handling."""
    original_writer = q4.write_compact_ass
    original_prepare = q4.q2.prepare_clip_content
    try:
        q4.write_compact_ass = write_compact_ass
        q4.q2.prepare_clip_content = prepare_clip_content
        language = _normalize_language(os.getenv("SUBTITLE_LANGUAGE", "English"))
        autoclip.log(f"Legendas: idioma={language} · fonte=60 · posição inferior preservada")
        q4.run(url, clips_count, min_seconds, max_seconds, whisper_model)
    finally:
        q4.write_compact_ass = original_writer
        q4.q2.prepare_clip_content = original_prepare
