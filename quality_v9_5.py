from __future__ import annotations

import os
import re

import autoclip
import quality_v2 as q2
import quality_v6 as q6
import quality_v6_1 as q61
import quality_v9_3 as q93
import quality_v9_4 as q94


_ORIGINAL_SELECT = q93.select_v9_3
HOOK_REVIEW: list[dict] = []


_GENERIC_HOOKS = (
    "you won't believe",
    "you wont believe",
    "watch until the end",
    "watch till the end",
    "this is crazy",
    "this changes everything",
    "what happens next",
    "você não vai acreditar",
    "voce nao vai acreditar",
    "assista até o final",
    "assista ate o final",
    "isso é insano",
    "isso e insano",
)

_FILLER_STARTS = (
    "yeah", "yes", "so", "well", "okay", "ok", "like", "you know", "i mean",
    "right", "actually", "basically", "and", "but",
    "sim", "então", "entao", "bem", "tipo", "sabe", "quer dizer", "na verdade", "e", "mas",
)

_IMPACT_WORDS = {
    "lost", "lose", "won", "win", "secret", "mistake", "truth", "failed", "failure", "risk",
    "money", "million", "billion", "first", "last", "never", "always", "why", "how", "what",
    "perdi", "perdeu", "ganhei", "ganhou", "segredo", "erro", "verdade", "falhou", "fracasso",
    "risco", "dinheiro", "milhão", "milhao", "nunca", "sempre", "porquê", "porque", "como", "o quê",
}


def _hook_enabled() -> bool:
    return os.getenv("SHOW_CONTEXT_HOOK", "true").strip().lower() in {"1", "true", "yes", "on"}


def _clean_hook(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"^(?:hook|gancho)\s*[:\-–—]\s*", "", text, flags=re.IGNORECASE)
    text = text.strip(" \t\n\r\"'“”‘’")
    if not text:
        return ""

    low = text.lower()
    if any(low.startswith(generic) for generic in _GENERIC_HOOKS):
        return ""

    words = text.split()
    if len(words) > 8:
        words = words[:8]
    text = " ".join(words).strip()
    text = text.rstrip(".,;:–—-")

    vague = {
        "this is why", "here is why", "this happened", "that happened", "the truth",
        "isso aconteceu", "é por isso", "e por isso", "a verdade",
    }
    if text.lower() in vague or len(text.split()) < 3:
        return ""
    return text


def _strip_filler(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip(" \t\n\r,.;:–—-\"'“”‘’")
    changed = True
    while changed and clean:
        changed = False
        low = clean.lower()
        for filler in _FILLER_STARTS:
            prefix = filler + " "
            if low.startswith(prefix):
                clean = clean[len(prefix):].lstrip(" ,.;:–—-")
                changed = True
                break
    return clean


def _window_score(text: str, start_rel: float) -> float:
    clean = _strip_filler(text)
    words = clean.split()
    if len(words) < 4:
        return -100.0
    score = 0.0
    if 5 <= len(words) <= 12:
        score += 4.0
    elif len(words) <= 18:
        score += 2.0
    if q6._ambiguous(clean):
        score -= 5.0
    low_words = {re.sub(r"[^\wÀ-ÿ'-]", "", w.lower()) for w in words}
    score += min(3.0, sum(1 for w in low_words if w in _IMPACT_WORDS) * 0.9)
    if re.search(r"(?:[$€£]|R\$|\b\d[\d.,]*\b|\b\d+%\b)", clean):
        score += 2.4
    if "?" in clean:
        score += 0.8
    score += max(0.0, 2.0 - start_rel / 8.0)
    return score


def _fallback_hook(segments: list[dict], plan: q2.ClipPlan) -> str:
    """Extract a factual hook from the clip itself when AI hook generation is unavailable."""
    clip_start = float(plan.start)
    horizon = min(float(plan.end), clip_start + 24.0)
    relevant = [
        s for s in segments
        if float(s.get("end", 0)) > clip_start and float(s.get("start", 0)) < horizon
        and str(s.get("text") or "").strip()
    ]
    candidates: list[tuple[float, str]] = []
    for i, seg in enumerate(relevant):
        pieces = [str(seg.get("text") or "").strip()]
        if i + 1 < len(relevant):
            pieces.append(str(relevant[i + 1].get("text") or "").strip())
        text = _strip_filler(" ".join(pieces))
        rel = max(0.0, float(seg.get("start", clip_start)) - clip_start)
        candidates.append((_window_score(text, rel), text))

    if not candidates:
        return ""
    _, best = max(candidates, key=lambda item: item[0])
    return _clean_hook(" ".join(best.split()[:8]))


def _recover_previous_hook(plan: q2.ClipPlan, existing: dict[tuple[float, float], str]) -> tuple[str, str]:
    key = (round(plan.start, 2), round(plan.end, 2))
    direct = _clean_hook(existing.get(key, ""))
    if direct:
        return direct, "direct"

    # Final Guard can alter the ending after the hook was generated. Recover the
    # hook from the closest old interval that has the same beginning / strong overlap.
    matches = []
    for (start, end), text in existing.items():
        cleaned = _clean_hook(text)
        if not cleaned:
            continue
        start_delta = abs(float(start) - plan.start)
        overlap = max(0.0, min(float(end), plan.end) - max(float(start), plan.start))
        denom = max(1.0, min(float(end) - float(start), plan.end - plan.start))
        overlap_ratio = overlap / denom
        if start_delta <= 0.45 or overlap_ratio >= 0.82:
            cost = start_delta * 4.0 + abs(float(end) - plan.end) * 0.08 - overlap_ratio
            matches.append((cost, cleaned))
    if matches:
        return min(matches, key=lambda item: item[0])[1], "recovered_after_end_change"
    return "", "missing"


def _clip_text(segments: list[dict], plan: q2.ClipPlan) -> str:
    text = " ".join(
        re.sub(r"\s+", " ", str(s.get("text") or "")).strip()
        for s in segments
        if float(s.get("end", 0)) > plan.start and float(s.get("start", 0)) < plan.end
    ).strip()
    # Enough context for a hook without bloating a single batch request.
    return text[:1800]


def _refine_hooks_batch(
    plans: list[q2.ClipPlan], segments: list[dict], source_title: str, seeds: list[str]
) -> dict[int, str]:
    """One optional AI call for all final hooks; failure leaves deterministic seeds intact."""
    if not os.getenv("GEMINI_API_KEY", "").strip():
        return {}

    payload = []
    for idx, (plan, seed) in enumerate(zip(plans, seeds), 1):
        payload.append(
            {
                "clip": idx,
                "seed_hook": seed,
                "clip_text": _clip_text(segments, plan),
            }
        )

    prompt = f"""Você é o revisor FINAL de hooks visuais para TikTok/Reels/Shorts.
O hook ficará no TOPO do vídeo por alguns segundos. Reescreva um hook por corte depois que a edição já foi definida.

REGRAS:
- 4 a 8 palavras; prefira 5–7 quando possível.
- Deve ser compreensível imediatamente para alguém que nunca viu o vídeo.
- Seja natural e humano, não pareça título robótico.
- Crie curiosidade sobre a tensão/ideia central sem inventar nada e sem entregar desnecessariamente todo o payoff.
- Preserve números, nomes e fatos somente quando aparecem no texto do corte.
- Evite começar com pronome sem referente: "he", "she", "they", "it", "ele", "ela", "isso".
- NÃO use clickbait genérico como "You won't believe", "Watch until the end", "This is crazy", "Você não vai acreditar".
- NÃO escreva "Hook:" nem coloque aspas.
- Não use ALL CAPS.
- Uma pergunta curta é permitida somente se ela for realmente sustentada pelo corte; caso contrário use uma frase declarativa.
- {q6._hook_language()}

Responda SOMENTE JSON válido:
{{"hooks":[{{"clip":1,"hook":"..."}}]}}

Fonte: {source_title}
CORTES:
{payload}
"""
    try:
        data = q2._gemini_json(prompt, attempts=1)
    except Exception as exc:
        autoclip.log(f"Hook Guard: revisão semântica indisponível; mantendo fallback: {exc}")
        return {}

    refined: dict[int, str] = {}
    if isinstance(data, dict) and isinstance(data.get("hooks"), list):
        for item in data["hooks"]:
            try:
                idx = int(item.get("clip"))
            except Exception:
                continue
            hook = _clean_hook(item.get("hook") or "")
            if hook:
                refined[idx] = hook
    return refined


def select_v9_5(
    segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str
) -> list[q2.ClipPlan]:
    plans = _ORIGINAL_SELECT(segments, min_seconds, max_seconds, count, source_title)
    HOOK_REVIEW.clear()
    if not plans:
        return plans

    if not _hook_enabled():
        q6.HOOK_BY_BOUNDS = {}
        return plans

    previous = dict(q6.HOOK_BY_BOUNDS)
    seeds: list[str] = []
    sources: list[str] = []
    for plan in plans:
        hook, source = _recover_previous_hook(plan, previous)
        if not hook:
            hook = _fallback_hook(segments, plan)
            source = "extractive_fallback" if hook else "unavailable"
        seeds.append(hook)
        sources.append(source)

    refined = _refine_hooks_batch(plans, segments, source_title, seeds)
    final_hooks: dict[tuple[float, float], str] = {}

    for idx, plan in enumerate(plans, 1):
        hook = refined.get(idx) or seeds[idx - 1]
        source = "final_ai_review" if idx in refined else sources[idx - 1]
        hook = _clean_hook(hook)
        if not hook:
            hook = _fallback_hook(segments, plan)
            source = "extractive_fallback" if hook else "unavailable"

        if hook:
            final_hooks[(round(plan.start, 2), round(plan.end, 2))] = hook
        HOOK_REVIEW.append(
            {
                "clip": idx,
                "hook": hook,
                "source": source,
                "start": plan.start,
                "end": plan.end,
            }
        )

    # Keep only final clip bounds. This prevents stale pre-review keys from
    # confusing the ASS writer, cover generator, or later metadata steps.
    q6.HOOK_BY_BOUNDS = final_hooks
    return plans


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_selector = q93.select_v9_3
    try:
        q93.select_v9_3 = select_v9_5
        autoclip.log(
            f"Quality v9.5: Hook Guard ativo · hook contextual até {q61._hook_duration():g}s · recuperação após Final Guard"
        )
        q94.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Hook Guard v9.5\n")
        if not _hook_enabled():
            autoclip.summary("Hook contextual: **desativado pelo workflow**.\n")
        else:
            for item in HOOK_REVIEW:
                hook = item.get("hook") or "(nenhum hook seguro encontrado)"
                source = item.get("source", "")
                autoclip.summary(
                    f"- **Corte {item['clip']}** — hook: **{hook}** · origem `{source}` · "
                    f"exibição até **{q61._hook_duration():g}s**"
                )
        autoclip.summary(
            "\nO Hook Guard reassocia o texto ao intervalo FINAL depois da revisão de encerramento, "
            "faz uma revisão semântica em lote quando o Gemini está disponível e mantém um fallback factual "
            "extraído do próprio clip se a IA estiver indisponível. Hooks genéricos/clickbait são rejeitados.\n"
        )
    finally:
        q93.select_v9_3 = original_selector
