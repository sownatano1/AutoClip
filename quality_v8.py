from __future__ import annotations

import os
import re
from pathlib import Path

import autoclip
import quality_v2 as q2
import quality_v4 as q4
import quality_v5 as q5
import quality_v6 as q6
import quality_v6_1 as q61
import quality_v7 as q7


RETENTION_META: dict[tuple[float, float], dict] = {}


def _smart_emphasis_enabled() -> bool:
    return os.getenv("SMART_SUBTITLE_EMPHASIS", "true").strip().lower() in {"1", "true", "yes"}


def _natural_loop_enabled() -> bool:
    return os.getenv("NATURAL_LOOP", "true").strip().lower() in {"1", "true", "yes"}


def _text_between(segments: list[dict], start: float, end: float) -> str:
    return " ".join(
        str(s.get("text") or "").strip()
        for s in segments
        if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end
    ).strip()


def _ending_text(segments: list[dict], start: float, end: float, seconds: float = 9.0) -> str:
    return _text_between(segments, max(start, end - seconds), end)


def _ending_looks_weak(text: str) -> bool:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return True
    low = clean.lower().rstrip('"\'’” ')
    dangling = (
        " and", " but", " because", " so", " or", " if", " when", " which", " that", " with", " to",
        " e", " mas", " porque", " então", " entao", " ou", " se", " quando", " que", " com", " para",
    )
    if low.endswith(dangling):
        return True
    # A final sentence mark is not mandatory in Whisper, but a comma-like ending is a strong sign of truncation.
    if clean.endswith((",", ";", ":", "—", "-")):
        return True
    words = clean.split()
    return len(words) < 4


def _extend_end(plan: q2.ClipPlan, segments: list[dict], seconds: float, max_seconds: int) -> q2.ClipPlan:
    room = max(0.0, max_seconds - (plan.end - plan.start))
    extra = min(max(0.0, seconds), room, 14.0)
    if extra < 0.5:
        return plan
    hard_end = min(float(segments[-1]["end"]), plan.end + extra)
    new_end = plan.end
    for s in segments:
        s_end = float(s.get("end", 0))
        if s_end <= plan.end + 0.05:
            continue
        if s_end > hard_end + 0.05:
            break
        new_end = s_end
        text = str(s.get("text") or "").strip()
        if re.search(r"[.!?…][\"'’”]?$", text):
            break
    if new_end <= plan.end + 0.1:
        return plan
    text = _text_between(segments, plan.start, new_end)
    score = autoclip.score_window(text, new_end - plan.start)
    return q2.ClipPlan(plan.start, new_end, round(score, 1), text, plan.reason)


def _overlaps(plan: q2.ClipPlan, selected: list[q2.ClipPlan]) -> bool:
    return any(
        max(0.0, min(plan.end, other.end) - max(plan.start, other.start))
        / max(1.0, min(plan.end - plan.start, other.end - other.start)) > 0.25
        for other in selected
    )


def select_retention_clips(
    segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str
) -> list[q2.ClipPlan]:
    """Choose the shortest complete story and insist on a satisfying ending."""
    q6.HOOK_BY_BOUNDS = {}
    RETENTION_META.clear()
    timeline = q2._editor_transcript(segments)
    wanted = min(8, max(count * 3, count + 2))
    prefer_loop = _natural_loop_enabled()

    prompt = f"""Você é um editor de retenção para TikTok/Reels/Shorts. Leia a TRANSCRIÇÃO INTEIRA antes de escolher.

Fonte: {source_title}
Perfil: {q6.PROFILE_LABELS.get(q6.CURRENT_PROFILE, q6.CURRENT_PROFILE)}
Precisamos de {count} cortes finais. Gere até {wanted} candidatos sem sobreposição.

DURAÇÃO INTELIGENTE:
- Cada corte pode ter entre {min_seconds} e {max_seconds} segundos.
- NÃO estique um trecho para atingir uma duração arbitrária.
- Escolha o MENOR intervalo que entregue contexto/setup + desenvolvimento + payoff/conclusão.
- Se a história fica completa em 35 ou 47 segundos, termine ali. Se precisa de 80 ou 120, use isso.
- Todo segundo deve ajudar compreensão, curiosidade, emoção ou payoff.

COMEÇO:
- Pense em alguém que nunca viu o vídeo.
- Avalie explicitamente os primeiros 5 segundos.
- Se faltarem contexto ou referentes, first5_clear=false e lead_seconds informa quanto voltar (0–15 s).
- Não comece no meio de frase nem com pronome/reação sem referente.

FINAL:
- Os últimos 5–10 segundos devem entregar uma conclusão, resposta, punchline, surpresa, decisão ou afirmação forte.
- Nunca termine em frase incompleta, transição, silêncio conceitual ou preparação para algo que ficou fora do corte.
- ending_strength é 0–100. Use >=70 apenas para finais realmente satisfatórios.
- Se o final escolhido ainda precisa de mais fala para fechar, informe extend_end_seconds (0–14).
- ending_type deve ser um de: conclusion, answer, punchline, surprise, decision, strong_statement.
- loopable=true SOMENTE quando a última ideia conduz naturalmente de volta à abertura no replay, sem repetir nem inventar fala.
- {'Quando dois candidatos forem igualmente bons, prefira o que também tiver loop natural.' if prefer_loop else 'Não priorize loop; priorize apenas o melhor fechamento.'}

CONTEXTO E HOOK:
- {q6._profile_rules(q6.CURRENT_PROFILE)}
- hook: no máximo 8 palavras e somente informação sustentada pelo conteúdo.
- Se não houver hook factual seguro, use string vazia.
- {q6._hook_language()}

Responda SOMENTE JSON válido:
{{"clips":[{{"start":123.4,"end":170.0,"score":92,"reason":"...","duration_reason":"...","first5_clear":true,"lead_seconds":0,"ending_strength":88,"ending_type":"conclusion","extend_end_seconds":0,"loopable":false,"hook":"..."}}]}}

TRANSCRIÇÃO COMPLETA:
{timeline}
"""

    data = q2._gemini_json(prompt, attempts=3)
    selected: list[q2.ClipPlan] = []
    show_hook = os.getenv("SHOW_CONTEXT_HOOK", "true").strip().lower() in {"1", "true", "yes"}

    if isinstance(data, dict) and isinstance(data.get("clips"), list):
        for item in data["clips"]:
            try:
                plan = q2._candidate_from_bounds(
                    segments,
                    float(item["start"]),
                    float(item["end"]),
                    min_seconds,
                    max_seconds,
                    float(item.get("score", 0) or 0),
                    str(item.get("reason") or "").strip(),
                )
            except Exception:
                plan = None
            if not plan:
                continue

            first_clear = bool(item.get("first5_clear", True))
            if q6._ambiguous(_text_between(segments, plan.start, min(plan.end, plan.start + 6))):
                first_clear = False
            if not first_clear:
                try:
                    lead = float(item.get("lead_seconds", 8) or 8)
                except Exception:
                    lead = 8.0
                extended = q6._extend_plan(plan, segments, lead, max_seconds)
                if not extended:
                    continue
                plan = extended

            try:
                ending_strength = float(item.get("ending_strength", 0) or 0)
            except Exception:
                ending_strength = 0.0
            local_weak = _ending_looks_weak(_ending_text(segments, plan.start, plan.end))
            if ending_strength < 70 or local_weak:
                try:
                    extend_seconds = float(item.get("extend_end_seconds", 8) or 8)
                except Exception:
                    extend_seconds = 8.0
                plan = _extend_end(plan, segments, extend_seconds, max_seconds)
                local_weak = _ending_looks_weak(_ending_text(segments, plan.start, plan.end))
                # Do not keep an obviously truncated ending if another candidate can replace it.
                if local_weak and ending_strength < 58:
                    continue

            if _overlaps(plan, selected):
                continue

            hook = re.sub(r"\s+", " ", str(item.get("hook") or "")).strip()
            if len(hook) > 70:
                hook = ""
            selected.append(plan)
            key = (round(plan.start, 2), round(plan.end, 2))
            if show_hook and hook:
                q6.HOOK_BY_BOUNDS[key] = hook
            RETENTION_META[key] = {
                "ending_strength": round(ending_strength, 1),
                "ending_type": str(item.get("ending_type") or "").strip(),
                "loopable": bool(item.get("loopable", False)) if prefer_loop else False,
                "duration_reason": str(item.get("duration_reason") or "").strip(),
            }
            if len(selected) >= count:
                break

    # Local fallback: allow short complete moments, then extend only enough to finish the final sentence.
    if len(selected) < count:
        fallback = autoclip.select_best(segments, min_seconds, max_seconds, count * 5)
        for item in fallback:
            plan = q2._candidate_from_bounds(
                segments, item.start, item.end, min_seconds, max_seconds, item.score,
                "Fallback local com duração flexível e fechamento completo",
            )
            if not plan:
                continue
            if q6._ambiguous(_text_between(segments, plan.start, min(plan.end, plan.start + 6))):
                plan = q6._extend_plan(plan, segments, 8, max_seconds)
                if not plan:
                    continue
            if _ending_looks_weak(_ending_text(segments, plan.start, plan.end)):
                plan = _extend_end(plan, segments, 10, max_seconds)
            if _overlaps(plan, selected):
                continue
            selected.append(plan)
            key = (round(plan.start, 2), round(plan.end, 2))
            RETENTION_META[key] = {
                "ending_strength": 0,
                "ending_type": "local_fallback",
                "loopable": False,
                "duration_reason": "Menor janela local disponível com contexto e fechamento de frase.",
            }
            if len(selected) >= count:
                break
    return selected[:count]


def _important_span(text: str) -> tuple[int, int] | None:
    """Return one meaningful span to emphasize, or None. Intentionally conservative."""
    if not _smart_emphasis_enabled():
        return None
    clean = text
    patterns = [
        r"(?:R\$|US\$|\$|€|£)\s?\d[\d.,]*",
        r"\b\d[\d.,]*\s?(?:%|percent|por cento|million|billion|milhão|milhões|milhao|milhoes|mil|k|m|b)\b",
        r"\b\d{2,}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if match:
            return match.span()

    impact = {
        "never", "always", "biggest", "worst", "best", "impossible", "insane", "crazy", "secret",
        "mistake", "lost", "lose", "won", "win", "money", "million", "billion", "first", "last",
        "truth", "failed", "failure", "dead", "killed", "risk", "dangerous", "shocking", "finally",
        "nunca", "sempre", "maior", "pior", "melhor", "impossível", "impossivel", "segredo", "erro",
        "perdi", "perdeu", "ganhei", "ganhou", "dinheiro", "milhão", "milhao", "verdade", "falhou",
        "fracasso", "risco", "perigoso", "chocante", "finalmente",
    }
    for match in re.finditer(r"\b[\wÀ-ÿ'-]+\b", clean, flags=re.UNICODE):
        token = match.group(0)
        if token.lower() in impact or (len(token) >= 3 and token.isupper()):
            return match.span()
    return None


def _style_caption_chunk(chunk: str) -> str:
    safe = chunk.replace("{", "(").replace("}", ")").replace("\n", " ")
    span = _important_span(safe)
    if not span:
        return safe
    start, end = span
    # One restrained emphasis per caption: warm yellow, bold, ~11% larger than the approved 65px base.
    open_tag = r"{\b1\fs72\c&H0000D7FF&}"
    close_tag = r"{\rDefault}"
    return safe[:start] + open_tag + safe[start:end] + close_tag + safe[end:]


def write_ass_v8(segments: list[dict], start: float, end: float, target: Path) -> None:
    hook = q6.HOOK_BY_BOUNDS.get((round(start, 2), round(end, 2)), "")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,DejaVu Sans,65,&H00FFFFFF,&H000000FF,&H00000000,&H50000000,0,0,0,0,100,100,0,0,1,3.0,0,2,90,90,155,1
Style: Hook,DejaVu Sans,46,&H00FFFFFF,&H000000FF,&H00000000,&H72000000,1,0,0,0,100,100,0,0,3,0,0,8,90,90,115,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events: list[str] = []
    if hook:
        safe_hook = q6._wrap_hook(hook).replace("{", "(").replace("}", ")")
        duration = min(q61._hook_duration(), max(0.2, end - start))
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
            styled = _style_caption_chunk(chunk)
            events.append(
                f"Dialogue: 0,{q4._ass_timestamp(cue_start-start)},{q4._ass_timestamp(cue_end-start)},Default,,0,0,0,,{styled}"
            )
    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_selector = q6.select_contextual_v6
    original_writer = q61.write_ass_v6_1
    try:
        q6.select_contextual_v6 = select_retention_clips
        q61.write_ass_v6_1 = write_ass_v8
        autoclip.log(
            f"Quality v8: duração inteligente {min_seconds}-{max_seconds}s · final forte · "
            f"ênfase de legenda={'on' if _smart_emphasis_enabled() else 'off'} · "
            f"loop natural={'on' if _natural_loop_enabled() else 'off'}"
        )
        q7.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Retenção v8\n")
        autoclip.summary(
            f"Duração inteligente: **{min_seconds}–{max_seconds}s**, escolhendo o menor corte completo. "
            f"Ênfase discreta nas legendas: **{'ativada' if _smart_emphasis_enabled() else 'desativada'}**.\n"
        )
        for key, meta in sorted(RETENTION_META.items()):
            start, end = key
            duration = end - start
            loop_text = " · loop editorial natural" if meta.get("loopable") else ""
            ending = meta.get("ending_type") or "avaliado"
            strength = meta.get("ending_strength", 0)
            autoclip.summary(
                f"- {autoclip.fmt_time(start)}–{autoclip.fmt_time(end)} ({duration:.0f}s) — "
                f"final **{ending}** / força {strength:g}{loop_text}"
            )
            if meta.get("duration_reason"):
                autoclip.summary(f"  - Duração: {meta['duration_reason']}")
        autoclip.summary(
            "\nLoop v8 é editorial: quando marcado, o fim foi escolhido para reconectar naturalmente à abertura no replay; "
            "o AutoClip não duplica fala nem força uma transição artificial.\n"
        )
    finally:
        q6.select_contextual_v6 = original_selector
        q61.write_ass_v6_1 = original_writer
