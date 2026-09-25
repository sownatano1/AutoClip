from __future__ import annotations

import os
import re

import requests

import autoclip
import quality_v6 as q6
import quality_v9_8 as q98
import quality_v9_10_1 as q101
import quality_v9_11 as q11
import quality_v9_13 as q13
import quality_v9_14 as q14


HOOK_REPORT: list[dict] = []
_ORIGINAL_HYBRID_SELECTOR = q13._hybrid_selector

_GENERIC = (
    "you won't believe",
    "you wont believe",
    "watch until the end",
    "watch till the end",
    "this is crazy",
    "this changes everything",
    "what happens next",
    "a closer look",
    "this moment",
    "você não vai acreditar",
    "voce nao vai acreditar",
    "assista até o final",
    "assista ate o final",
)

_VAGUE = {
    "this", "that", "it", "they", "he", "she", "something", "things",
    "isso", "esse", "essa", "ele", "ela", "eles", "elas", "algo",
}


def _clean(value, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.strip(" \t\r\n\"'“”‘’")
    return text[:limit] if limit else text


def _words(value: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ0-9]+(?:['’][A-Za-zÀ-ÿ]+)?", str(value or ""))


def _norm_words(value: str) -> list[str]:
    return [w.casefold().replace("’", "'") for w in _words(value)]


def _target_language() -> str:
    raw = os.getenv("SUBTITLE_LANGUAGE", "English").strip().lower()
    if "portugu" in raw or raw == "pt-br":
        return "Brazilian Portuguese"
    if "original" in raw:
        lang = str((q98._source_metadata() or {}).get("language") or "").lower()
        return "Brazilian Portuguese" if lang.startswith("pt") else "English"
    return "English"


def _is_transcript_copy(hook: str, transcript: str) -> bool:
    hw = _norm_words(hook)
    tw = _norm_words(transcript)
    if not hw or not tw:
        return False

    hnorm = " ".join(hw)
    tnorm = " ".join(tw)
    if len(hw) >= 4 and hnorm in tnorm:
        return True

    # Any exact 4-word phrase from the hook appearing in dialogue is too extractive.
    if len(hw) >= 4:
        for i in range(len(hw) - 3):
            phrase = " ".join(hw[i:i + 4])
            if phrase in tnorm:
                return True

    common = len(set(hw) & set(tw)) / max(1, len(set(hw)))
    return len(hw) >= 5 and common >= 0.82


def _valid_hook(hook: str, plan) -> bool:
    hook = _clean(hook, 120)
    words = _words(hook)
    if not (4 <= len(words) <= 9):
        return False

    low = hook.casefold()
    if any(x in low for x in _GENERIC):
        return False
    if words and words[0].casefold() in _VAGUE:
        return False
    if _is_transcript_copy(hook, plan.text):
        return False
    if hook.endswith((",", ";", ":", "-", "—")):
        return False
    return True


def _participant_hint() -> str:
    intel = q98.SOURCE_INTELLIGENCE or {}
    names: list[str] = []
    for item in intel.get("participants") or []:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"), 80)
        try:
            confidence = float(item.get("confidence", 0) or 0)
        except Exception:
            confidence = 0.0
        if name and confidence >= 0.55:
            names.append(name)
    return names[0] if len(names) == 1 else ""


def _work_hint(source: dict) -> str:
    try:
        value = _clean(q101._work_hint_v9_10_1(source, []), 70)
        if value and len(_words(value)) <= 5:
            return value
    except Exception:
        pass
    return ""


def _local_context_hook(plan, source: dict) -> str:
    """Create an editorial hook from clip context; never quote dialogue."""
    language = _target_language()
    text = plan.text.casefold()
    work = _work_hint(source)
    person = _participant_hint()
    topic = q11._semantic_topic(plan.text, source)

    if language == "Brazilian Portuguese":
        if any(x in text for x in ("filming", "filmed", "shooting", "camera", "on set", "film method")):
            candidates = [
                f"Como {work} foi filmado" if work else "",
                f"{person} explica o processo de filmagem" if person else "",
                "Como esse processo de filmagem funciona",
            ]
        elif any(x in text for x in ("audition", "casting", "cast for")):
            candidates = [
                f"Como foi o teste para {work}" if work else "",
                f"{person} conta como foi o teste" if person else "",
                "O que aconteceu durante o teste de elenco",
            ]
        elif any(x in text for x in ("director", "directing", "directed")):
            candidates = [
                f"Como foi trabalhar em {work}" if work else "",
                f"{person} fala sobre trabalhar com o diretor" if person else "",
                "Como foi trabalhar com esse diretor",
            ]
        elif any(x in text for x in ("role", "character", "playing", "portray", "portraying")):
            candidates = [
                f"O desafio por trás desse papel em {work}" if work else "",
                f"{person} explica o desafio desse papel" if person else "",
                "O desafio por trás desse papel",
            ]
        elif any(x in text for x in ("practical effect", "visual effect", "vfx", "cgi", "effect")):
            candidates = [
                f"Como os efeitos de {work} foram feitos" if work else "",
                "Como esses efeitos foram realmente feitos",
            ]
        elif any(x in text for x in ("stunt", "stunts", "action scene")):
            candidates = [
                f"Como as cenas de ação de {work} foram feitas" if work else "",
                "Como essa cena de ação foi construída",
            ]
        else:
            candidates = [
                f"{person} explica {topic.lower()}" if person else "",
                f"Por dentro de {topic.lower()}",
            ]
    else:
        if any(x in text for x in ("filming", "filmed", "shooting", "camera", "on set", "film method")):
            candidates = [
                f"How {work} Was Actually Filmed" if work else "",
                f"{person} Explains the Filming Process" if person else "",
                "How This Filming Process Actually Works",
            ]
        elif any(x in text for x in ("audition", "casting", "cast for")):
            candidates = [
                f"How the {work} Audition Really Went" if work else "",
                f"{person} Explains the Audition Process" if person else "",
                "What Really Happened During the Audition",
            ]
        elif any(x in text for x in ("director", "directing", "directed")):
            candidates = [
                f"What Working on {work} Was Like" if work else "",
                f"{person} on Working With the Director" if person else "",
                "What Working With This Director Was Like",
            ]
        elif any(x in text for x in ("role", "character", "playing", "portray", "portraying")):
            candidates = [
                f"The Challenge Behind This {work} Role" if work else "",
                f"{person} Explains This Role's Challenge" if person else "",
                "The Challenge Behind This Role",
            ]
        elif any(x in text for x in ("practical effect", "visual effect", "vfx", "cgi", "effect")):
            candidates = [
                f"How {work}'s Effects Were Actually Made" if work else "",
                "How These Effects Were Actually Made",
            ]
        elif any(x in text for x in ("stunt", "stunts", "action scene")):
            candidates = [
                f"How {work}'s Action Scenes Were Made" if work else "",
                "How This Action Scene Was Built",
            ]
        else:
            candidates = [
                f"{person} Explains {topic}" if person else "",
                f"Inside the Story Behind {topic}",
            ]

    for candidate in candidates:
        candidate = _clean(candidate, 120)
        if candidate and _valid_hook(candidate, plan):
            return candidate

    # Last resort: derive from the contextual caption logic, but never from raw dialogue.
    try:
        contextual = q14._local_context_caption(plan, source, "pt-BR" if language == "Brazilian Portuguese" else "en")
    except Exception:
        contextual = ""
    words = _words(contextual)
    if len(words) > 9:
        contextual = " ".join(words[:9])
    return contextual if _valid_hook(contextual, plan) else ""


def _groq_context_hook(plan, source: dict) -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        return ""

    model, supports_images = q13.choose_groq_model(prefer_vision=True)
    if not model:
        return ""

    intel = q98.SOURCE_INTELLIGENCE or {}
    language = _target_language()
    prompt = f"""You are writing the TOP-SCREEN HOOK shown for the first 8 seconds of ONE short-form clip.

LANGUAGE: {language}
SOURCE TITLE: {_clean(source.get('title'), 350)}
SOURCE PREMISE: {_clean(intel.get('premise'), 650)}
OPTIONAL VIDEO CONTEXT: {_clean(intel.get('twelvelabs_visual_context'), 450) or 'none'}
KNOWN PARTICIPANT: {_participant_hint() or 'none confirmed'}
WORK / FRANCHISE: {_work_hint(source) or 'none confirmed'}

FULL CLIP TRANSCRIPT:
{_clean(plan.text, 2600)}

Write a NEW editorial hook that gives the viewer context for what this clip is about.

STRICT RULES:
- 4 to 8 words preferred, maximum 9.
- Explain or tease the SPECIFIC subject, story, process, challenge, reveal, or opinion in this clip.
- It must make sense before the viewer hears the dialogue.
- DO NOT quote, paraphrase closely, or lift a memorable sentence from the transcript.
- DO NOT simply use the first sentence or any random spoken line.
- Avoid vague hooks like "This Changes Everything", "What Happens Next", "A Closer Look", or "This Moment".
- Avoid pronouns with no referent.
- Use a supported person's/work's name when it makes the hook clearer.
- Never identify a person from appearance.
- No hashtags, no quotation marks, no "Hook:" prefix.
- Do not invent facts.

Return ONLY JSON:
{{"hook":"..."}}"""

    content = [{"type": "text", "text": prompt}]
    if supports_images and q13.SOURCE_VIDEO is not None:
        try:
            image = q13._frame_data_url(
                q13.SOURCE_VIDEO,
                plan.start + (plan.end - plan.start) * 0.50,
            )
            if image:
                content.append({"type": "image_url", "image_url": {"url": image}})
        except Exception:
            pass

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.25,
                "max_completion_tokens": 140,
                "response_format": {"type": "json_object"},
            },
            timeout=(6, 16),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:180]}")
        body = response.json()
        choices = body.get("choices") or []
        message = (choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
        parsed = q13._safe_json(message.get("content"))
        hook = _clean(parsed.get("hook"), 120)
        if _valid_hook(hook, plan):
            return hook
        if hook:
            autoclip.log(f"Context Hook Guard: sugestão externa rejeitada por parecer fala/vaga · {hook}")
    except Exception as exc:
        autoclip.log(f"Context Hook Guard: Groq indisponível/ignorado · {exc}")
    return ""


def _selector_with_context_hooks(
    segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str
):
    plans = _ORIGINAL_HYBRID_SELECTOR(segments, min_seconds, max_seconds, count, source_title)

    q6.HOOK_BY_BOUNDS = {}
    HOOK_REPORT.clear()
    if not plans:
        return plans

    enabled = os.getenv("SHOW_CONTEXT_HOOK", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return plans

    source = q98._source_metadata()
    for idx, plan in enumerate(plans, 1):
        hook = _groq_context_hook(plan, source)
        mode = "groq_context"
        if not hook:
            hook = _local_context_hook(plan, source)
            mode = "local_context"

        # Absolute safety: an extractive hook is never displayed.
        if hook and _is_transcript_copy(hook, plan.text):
            autoclip.log(f"Context Hook Guard corte {idx}: hook rejeitado por copiar a fala")
            hook = ""

        if hook:
            q6.HOOK_BY_BOUNDS[(round(plan.start, 2), round(plan.end, 2))] = hook
            autoclip.log(f"Context Hook Guard corte {idx}: {hook} · {mode}")
        else:
            autoclip.log(f"Context Hook Guard corte {idx}: nenhum hook contextual seguro; topo ficará sem hook")
            mode = "none"

        HOOK_REPORT.append({
            "clip": idx,
            "hook": hook,
            "mode": mode,
            "start": plan.start,
            "end": plan.end,
        })

    return plans


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original = q13._hybrid_selector
    try:
        q13._hybrid_selector = _selector_with_context_hooks
        autoclip.log(
            "Quality v9.15: Context Hook Guard · hook editorial contextual · cópia de fala proibida · fallback local"
        )
        q14.run(url, clips_count, min_seconds, max_seconds, whisper_model)

        autoclip.summary("\n### Quality v9.15 — Context Hook Guard\n")
        if not HOOK_REPORT:
            autoclip.summary("- Nenhum hook foi gerado (desativado ou sem cortes).")
        for item in HOOK_REPORT:
            hook = item["hook"] or "(sem hook — nenhum texto contextual seguro)"
            autoclip.summary(
                f"- **Corte {item['clip']}** — **{hook}** · modo `{item['mode']}` · primeiros **8 s**"
            )
        autoclip.summary(
            "\nO hook agora é uma frase editorial nova baseada no contexto do corte inteiro. "
            "Qualquer sugestão que reproduza ou copie de perto uma fala da transcrição é rejeitada. "
            "Groq é opcional; se estiver indisponível, o sistema usa contexto local e nunca volta ao fallback extrativo antigo.\n"
        )
    finally:
        q13._hybrid_selector = original
