from __future__ import annotations

import os
import re
from pathlib import Path

import autoclip
import quality_v4 as q4
import quality_v5 as q5
import quality_v6 as q6


def _hook_duration() -> float:
    raw = os.getenv("HOOK_DURATION_SECONDS", "8").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 8.0
    return max(1.0, min(15.0, value))


def write_ass_v6_1(segments: list[dict], start: float, end: float, target: Path) -> None:
    hook = q6.HOOK_BY_BOUNDS.get((round(start, 2), round(end, 2)), "")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,DejaVu Sans,70,&H00FFFFFF,&H000000FF,&H00000000,&H50000000,0,0,0,0,100,100,0,0,1,3.0,0,2,90,90,180,1
Style: Hook,DejaVu Sans,46,&H00FFFFFF,&H000000FF,&H00000000,&H72000000,1,0,0,0,100,100,0,0,3,0,0,8,90,90,250,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events: list[str] = []
    if hook:
        safe_hook = q6._wrap_hook(hook).replace("{", "(").replace("}", ")")
        duration = min(_hook_duration(), max(0.2, end - start))
        events.append(
            f"Dialogue: 1,0:00:00.00,{q4._ass_timestamp(duration)},Hook,,0,0,0,,{safe_hook}"
        )

    for seg in segments:
        seg_start = max(start, float(seg["start"]))
        seg_end = min(end, float(seg["end"]))
        text = re.sub(r"\s+", " ", str(seg.get("text") or "")).strip()
        if seg_end <= seg_start or not text:
            continue
        chunks = q5._caption_chunks(text)
        weights = [max(1, len(c.split())) for c in chunks]
        total = sum(weights) or 1
        consumed = 0
        for pos, chunk in enumerate(chunks):
            cue_start = seg_start + (seg_end - seg_start) * (consumed / total)
            consumed += weights[pos]
            cue_end = seg_end if pos == len(chunks) - 1 else seg_start + (seg_end - seg_start) * (consumed / total)
            if cue_end - cue_start < 0.16:
                cue_end = min(seg_end, cue_start + 0.16)
            safe = chunk.replace("{", "(").replace("}", ")").replace("\n", " ")
            events.append(
                f"Dialogue: 0,{q4._ass_timestamp(cue_start-start)},{q4._ass_timestamp(cue_end-start)},Default,,0,0,0,,{safe}"
            )
    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_writer = q6.write_ass_v6
    try:
        q6.write_ass_v6 = write_ass_v6_1
        duration = _hook_duration()
        autoclip.log(f"Quality v6.1: legenda 70 · safe-zone TikTok (legenda +25px / hook 250px) · hook até {duration:g}s")
        q6.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary(
            f"\n### Ajustes v6.1\nLegenda: **70**, levemente elevada para zona segura do TikTok · hook superior reposicionado · duração configurada: **{duration:g} segundos**.\n"
        )
    finally:
        q6.write_ass_v6 = original_writer
