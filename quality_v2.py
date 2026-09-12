from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import autoclip


@dataclass
class ClipPlan:
    start: float
    end: float
    score: float
    text: str
    reason: str = ""


@dataclass
class FramingPlan:
    width: int
    height: int
    center_x: float | None
    safe_fit: bool
    reason: str


def _gemini_json(prompt: str, attempts: int = 3):
    client = autoclip.gemini_client()
    if not client:
        return None
    model = autoclip.env("GEMINI_MODEL", "gemini-3.8-flash")
    waits = [12, 30, 60]
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            return autoclip.extract_json(response.text or "")
        except Exception as exc:
            last_error = exc
            text = str(exc)
            retryable = "429" in text or "503" in text or "RESOURCE_EXHAUSTED" in text or "UNAVAILABLE" in text
            if not retryable or attempt >= attempts - 1:
                break
            delay = waits[min(attempt, len(waits) - 1)]
            autoclip.log(f"Gemini ocupado/limitado; nova tentativa em {delay}s")
            time.sleep(delay)
    if last_error:
        autoclip.log(f"Aviso: Gemini indisponível nesta etapa: {last_error}")
    return None


def _segments_between(segments: list[dict], start: float, end: float) -> list[dict]:
    return [dict(s) for s in segments if float(s["end"]) > start and float(s["start"]) < end]


def _text_between(segments: list[dict], start: float, end: float) -> str:
    return " ".join(str(s.get("text") or "").strip() for s in segments if float(s["end"]) > start and float(s["start"]) < end).strip()


def _editor_transcript(segments: list[dict], bucket_seconds: int = 20) -> str:
    """Represent the whole video compactly so the editor sees beginning, middle and end."""
    if not segments:
        return ""
    rows: list[str] = []
    bucket_start = math.floor(float(segments[0]["start"]) / bucket_seconds) * bucket_seconds
    bucket_end = bucket_start + bucket_seconds
    parts: list[str] = []
    for seg in segments:
        seg_start = float(seg["start"])
        while seg_start >= bucket_end:
            if parts:
                rows.append(f"[{autoclip.fmt_time(bucket_start)}] {' '.join(parts)}")
            parts = []
            bucket_start = bucket_end
            bucket_end += bucket_seconds
        text = re.sub(r"\s+", " ", str(seg.get("text") or "")).strip()
        if text:
            parts.append(text)
    if parts:
        rows.append(f"[{autoclip.fmt_time(bucket_start)}] {' '.join(parts)}")
    return "\n".join(rows)


def _contextual_bounds(segments: list[dict], start: float, end: float, min_seconds: int, max_seconds: int) -> tuple[float, float]:
    if not segments:
        return start, end

    start = max(float(segments[0]["start"]), start)
    end = min(float(segments[-1]["end"]), end)

    # Give the viewer setup before the payoff. Prefer a sentence boundary up to 12 s earlier.
    lead_target = max(float(segments[0]["start"]), start - 12.0)
    first_idx = next((i for i, s in enumerate(segments) if float(s["end"]) >= start), 0)
    contextual_start = start
    for i in range(first_idx, -1, -1):
        s_start = float(segments[i]["start"])
        if s_start < lead_target:
            break
        prev_text = str(segments[i - 1].get("text") or "").strip() if i > 0 else ""
        if i == 0 or re.search(r"[.!?…][\"']?$", prev_text):
            contextual_start = s_start
    if contextual_start == start:
        # Even without punctuation, a short lead-in is better than beginning mid-thought.
        for s in segments:
            s_start = float(s["start"])
            if s_start >= lead_target:
                contextual_start = s_start
                break

    # Finish the thought instead of cutting the final sentence abruptly.
    hard_end = min(float(segments[-1]["end"]), contextual_start + max_seconds)
    desired_end = max(end, contextual_start + min_seconds)
    contextual_end = min(desired_end, hard_end)
    end_idx = next((i for i, s in enumerate(segments) if float(s["end"]) >= contextual_end), len(segments) - 1)
    for i in range(end_idx, len(segments)):
        s_end = float(segments[i]["end"])
        if s_end > min(hard_end, desired_end + 10.0):
            break
        contextual_end = s_end
        if re.search(r"[.!?…][\"']?$", str(segments[i].get("text") or "").strip()):
            break

    contextual_end = min(contextual_end, hard_end)
    if contextual_end - contextual_start < min_seconds:
        contextual_end = min(hard_end, contextual_start + min_seconds)
    return contextual_start, contextual_end


def _candidate_from_bounds(segments: list[dict], start: float, end: float, min_seconds: int, max_seconds: int, score: float | None = None, reason: str = "") -> ClipPlan | None:
    start, end = _contextual_bounds(segments, start, end, min_seconds, max_seconds)
    duration = end - start
    if duration < min_seconds * 0.9 or duration > max_seconds + 0.5:
        return None
    text = _text_between(segments, start, end)
    if len(autoclip.words(text)) < 25:
        return None
    final_score = float(score) if score is not None else autoclip.score_window(text, duration)
    return ClipPlan(start, end, round(max(0.0, min(100.0, final_score)), 1), text, reason)


def select_contextual_clips(segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str) -> list[ClipPlan]:
    timeline = _editor_transcript(segments)
    prompt = f"""Você é um editor profissional de vídeos curtos. Analise a TRANSCRIÇÃO INTEIRA antes de escolher qualquer corte.

Fonte: {source_title}
Objetivo: escolher {count} trechos independentes para TikTok/Reels, cada um entre {min_seconds} e {max_seconds} segundos.

Regras obrigatórias:
- O espectador nunca deve sentir que caiu no meio de uma conversa sem contexto.
- Nos primeiros 5 a 12 segundos de cada corte deve ficar claro sobre quem/o que estão falando e por que aquilo importa.
- Se o momento mais forte depende de uma explicação anterior, inclua essa explicação ANTES do payoff.
- Não comece em pronome/reação isolada como “ele”, “ela”, “isso”, “sim”, “não”, “eu fiquei...” quando o referente não está claro.
- Dê preferência a mini-histórias completas: setup -> desenvolvimento -> payoff/conclusão.
- Evite cortar uma frase no começo ou no fim.
- Escolha momentos diferentes entre si e sem sobreposição relevante.
- Pense como alguém vendo o criador pela primeira vez, sem conhecer o vídeo original.

Responda SOMENTE JSON válido:
{{"clips":[{{"start":123.4,"end":228.0,"score":92,"reason":"por que funciona e qual é o contexto"}}]}}

Os tempos são segundos desde o início do vídeo.

TRANSCRIÇÃO COMPLETA COM MARCAS DE TEMPO:
{timeline}
"""
    data = _gemini_json(prompt, attempts=3)
    selected: list[ClipPlan] = []
    if isinstance(data, dict) and isinstance(data.get("clips"), list):
        for item in data["clips"]:
            try:
                plan = _candidate_from_bounds(
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
            overlap = any(max(0.0, min(plan.end, s.end) - max(plan.start, s.start)) / max(1.0, min(plan.end-plan.start, s.end-s.start)) > 0.25 for s in selected)
            if not overlap:
                selected.append(plan)
            if len(selected) >= count:
                break

    # Reliable local fallback, but add a setup lead-in so it still feels like a story.
    if len(selected) < count:
        fallback = autoclip.select_best(segments, min_seconds, max_seconds, count * 3)
        for item in fallback:
            plan = _candidate_from_bounds(segments, item.start, item.end, min_seconds, max_seconds, item.score, "Seleção local com contexto adicional")
            if not plan:
                continue
            overlap = any(max(0.0, min(plan.end, s.end) - max(plan.start, s.start)) / max(1.0, min(plan.end-plan.start, s.end-s.start)) > 0.25 for s in selected)
            if not overlap:
                selected.append(plan)
            if len(selected) >= count:
                break
    return selected[:count]


def prepare_clip_content(plans: list[ClipPlan], segments: list[dict], source_language: str | None) -> list[dict]:
    payload = []
    raw_by_clip: dict[int, list[dict]] = {}
    for clip_no, plan in enumerate(plans, 1):
        raw = _segments_between(segments, plan.start, plan.end)
        raw_by_clip[clip_no] = raw
        payload.append({
            "clip": clip_no,
            "start": round(plan.start, 2),
            "end": round(plan.end, 2),
            "segments": [{"i": i, "text": str(s.get("text") or "").strip()} for i, s in enumerate(raw)],
        })

    prompt = f"""Você prepara {len(plans)} cortes para TikTok. Idioma detectado da fonte: {source_language or 'desconhecido'}.

Para CADA corte:
1. Se a fala não estiver em português, traduza cada segmento para português do Brasil natural e fiel. Não resuma e não acrescente informação.
2. Se já estiver em português, preserve a fala, apenas corrigindo erro óbvio de transcrição quando o contexto deixar inequívoco.
3. Crie title, caption e 5-8 hashtags baseados somente naquele corte.
4. Traduções devem manter exatamente o mesmo campo i recebido.

Responda SOMENTE JSON válido no formato:
{{"clips":[{{"clip":1,"title":"...","caption":"...","hashtags":["#..."],"translations":[{{"i":0,"text":"..."}}]}}]}}

DADOS DOS CORTES:
{json.dumps(payload, ensure_ascii=False)}
"""
    data = _gemini_json(prompt, attempts=3)
    parsed_by_clip: dict[int, dict] = {}
    if isinstance(data, dict) and isinstance(data.get("clips"), list):
        for item in data["clips"]:
            try:
                parsed_by_clip[int(item.get("clip"))] = item
            except Exception:
                pass

    prepared: list[dict] = []
    for clip_no, plan in enumerate(plans, 1):
        raw = [dict(s) for s in raw_by_clip[clip_no]]
        item = parsed_by_clip.get(clip_no, {})
        translations = item.get("translations") if isinstance(item, dict) else None
        if isinstance(translations, list):
            for tr in translations:
                try:
                    i = int(tr.get("i", -1))
                    text = re.sub(r"\s+", " ", str(tr.get("text") or "")).strip()
                    if 0 <= i < len(raw) and text:
                        raw[i]["text"] = text
                except Exception:
                    pass

        fallback_title, fallback_caption, fallback_tags = autoclip.fallback_copy(plan.text)
        title = str(item.get("title") or fallback_title).strip()[:80] if isinstance(item, dict) else fallback_title
        caption = str(item.get("caption") or fallback_caption).strip()[:350] if isinstance(item, dict) else fallback_caption
        hashtags = [str(x).strip() for x in (item.get("hashtags") or fallback_tags) if str(x).strip()][:8] if isinstance(item, dict) else fallback_tags
        prepared.append({"segments": raw, "title": title, "caption": caption, "hashtags": hashtags})
    return prepared


def _caption_chunks(text: str, max_words: int = 5, max_chars: int = 30) -> list[str]:
    tokens = re.sub(r"\s+", " ", text).strip().split(" ")
    chunks: list[str] = []
    current: list[str] = []
    for token in tokens:
        proposed = " ".join(current + [token]).strip()
        if current and (len(current) >= max_words or len(proposed) > max_chars):
            chunks.append(" ".join(current))
            current = [token]
        else:
            current.append(token)
    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if c]


def write_compact_srt(segments: list[dict], start: float, end: float, target: Path) -> None:
    rows: list[str] = []
    cue_no = 1
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
        cursor = seg_start
        for pos, chunk in enumerate(chunks):
            if pos == len(chunks) - 1:
                cue_end = seg_end
            else:
                cue_end = cursor + (seg_end - seg_start) * (weights[pos] / total_weight)
                remaining_weight = sum(weights[pos + 1:])
                if remaining_weight:
                    # Recompute from the original segment so timings stay proportional.
                    consumed = sum(weights[:pos + 1])
                    cue_end = seg_start + (seg_end - seg_start) * (consumed / total_weight)
            if cue_end - cursor < 0.18:
                cue_end = min(seg_end, cursor + 0.18)
            rows.extend([
                str(cue_no),
                f"{autoclip.srt_timestamp(cursor-start)} --> {autoclip.srt_timestamp(cue_end-start)}",
                chunk,
                "",
            ])
            cue_no += 1
            cursor = cue_end
    target.write_text("\n".join(rows), encoding="utf-8")


def analyze_framing(video: Path, start: float, end: float) -> FramingPlan:
    import cv2

    cap = cv2.VideoCapture(str(video))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError("Não foi possível detectar resolução do vídeo.")

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    crop_w = max(2, int(height * 9 / 16))
    desired_centers: list[float] = []
    wide_multi_face = False
    duration = max(1.0, end - start)
    sample_count = 24
    min_face = max(36, int(min(width, height) * 0.045))

    try:
        for i in range(sample_count):
            t = start + duration * (i + 0.5) / sample_count
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = list(cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(min_face, min_face)))
            if not faces:
                continue
            faces.sort(key=lambda f: int(f[2]) * int(f[3]), reverse=True)
            primary = faces[0]
            primary_area = float(primary[2] * primary[3])
            relevant = [f for f in faces[:3] if float(f[2] * f[3]) >= primary_area * 0.38]
            centers = [float(x + w / 2) for x, y, w, h in relevant]
            if len(centers) >= 2:
                separation = max(centers) - min(centers)
                desired_centers.append((min(centers) + max(centers)) / 2)
                if separation > crop_w * 0.68:
                    wide_multi_face = True
            else:
                desired_centers.append(centers[0])
    finally:
        cap.release()

    source_ratio = width / height
    target_ratio = 9 / 16
    if source_ratio < target_ratio:
        return FramingPlan(width, height, None, True, "fonte mais estreita que 9:16")
    if not desired_centers:
        return FramingPlan(width, height, None, True, "sem rosto estável; preservando composição completa")

    sorted_centers = sorted(desired_centers)
    median = sorted_centers[len(sorted_centers) // 2]
    p10 = sorted_centers[max(0, int((len(sorted_centers) - 1) * 0.10))]
    p90 = sorted_centers[min(len(sorted_centers) - 1, int((len(sorted_centers) - 1) * 0.90))]
    speaker_shift = (p90 - p10) > crop_w * 0.52
    safe_fit = wide_multi_face or speaker_shift
    if wide_multi_face:
        reason = "duas ou mais pessoas ficariam cortadas no 9:16"
    elif speaker_shift:
        reason = "o foco muda muito de posição durante o corte"
    else:
        reason = "rosto dominante estável; crop centralizado no assunto"
    return FramingPlan(width, height, median, safe_fit, reason)


def render_clip(video: Path, plan: ClipPlan, srt: Path, output: Path, idx: int, total: int) -> FramingPlan:
    framing = analyze_framing(video, plan.start, plan.end)
    width, height = framing.width, framing.height
    source_ratio = width / height
    target_ratio = 9 / 16
    escaped = str(srt.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    # Short captions + smaller type. Bottom aligned, but kept clear of TikTok's bottom controls.
    sub = (
        f"subtitles='{escaped}':force_style='"
        "FontName=DejaVu Sans,FontSize=16,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=135,MarginL=70,MarginR=70'"
    )

    if source_ratio >= target_ratio and not framing.safe_fit:
        crop_w = int(height * target_ratio)
        crop_w -= crop_w % 2
        center = framing.center_x if framing.center_x is not None else width / 2
        x = int(max(0, min(width - crop_w, center - crop_w / 2)))
        filter_args = ["-vf", f"crop={crop_w}:{height}:{x}:0,scale=1080:1920,{sub}"]
    else:
        # When speakers change sides or multiple people matter, never amputate the conversation.
        # Preserve the complete source over a blurred 9:16 background instead of forcing a bad crop.
        vf = (
            "split=2[fg][bg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=28:12[bg2];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg2];"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,{sub}[outv]"
        )
        filter_args = ["-filter_complex", vf, "-map", "[outv]", "-map", "0:a?"]

    duration = max(0.1, plan.end - plan.start)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{plan.start:.3f}", "-i", str(video),
        "-t", f"{duration:.3f}",
    ] + filter_args + [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        "-pix_fmt", "yuv420p", "-progress", "pipe:1", "-nostats",
        "-loglevel", "error", str(output),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    last = -1
    if proc.stdout:
        for raw in proc.stdout:
            line = raw.strip()
            if line.startswith(("out_time_ms=", "out_time_us=")):
                try:
                    micros = float(line.split("=", 1)[1])
                    pct = int(max(0, min(99, micros / (duration * 1_000_000) * 100)))
                    if pct >= last + 5:
                        last = pct
                        overall = 68 + int(((idx - 1) + (pct / 100)) / max(1, total) * 18)
                        autoclip.progress("Render", overall, f"Corte {idx}/{total}: {pct}% · {framing.reason}")
                except Exception:
                    pass
    stderr = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"FFmpeg falhou: {stderr[-1500:]}")
    return framing


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    autoclip.check_configuration()
    shutil.rmtree(autoclip.WORK, ignore_errors=True)
    shutil.rmtree(autoclip.OUTPUT, ignore_errors=True)
    autoclip.WORK.mkdir(exist_ok=True)
    autoclip.OUTPUT.mkdir(exist_ok=True)

    autoclip.progress("Início", 2, "Baixando fonte")
    video, info = autoclip.download_youtube(url, autoclip.WORK)
    source_title = str(info.get("title") or "Vídeo do YouTube")
    autoclip.summary(f"# AutoClip Quality v2\n\n**Fonte:** {source_title}\n")

    transcript = autoclip.transcribe(video, whisper_model, autoclip.WORK)
    segments = transcript.get("segments") or []
    if not segments:
        raise RuntimeError("Whisper não encontrou fala suficiente.")

    autoclip.progress("Editor", 61, "Analisando a história inteira e procurando cortes com contexto")
    plans = select_contextual_clips(segments, min_seconds, max_seconds, clips_count, source_title)
    if not plans:
        raise RuntimeError("Nenhum trecho contextual adequado foi encontrado.")

    autoclip.progress("Editor", 64, "Preparando legendas curtas e metadados dos cortes")
    content = prepare_clip_content(plans, segments, transcript.get("language"))

    buffer = autoclip.BufferClient()
    results = []
    total = len(plans)
    for idx, plan in enumerate(plans, start=1):
        item = content[idx - 1]
        autoclip.progress("Corte", 66 + int((idx - 1) / total * 20), f"Preparando {idx}/{total} · score {plan.score}")
        srt = autoclip.WORK / f"clip_{idx}.srt"
        output = autoclip.OUTPUT / f"clip_{idx}.mp4"
        write_compact_srt(item["segments"], plan.start, plan.end, srt)
        framing = render_clip(video, plan, srt, output, idx, total)

        autoclip.progress("Cloudinary", 88 + int((idx - 1) / total * 6), f"Enviando corte {idx}/{total}")
        cloud_url, cloud_id = autoclip.cloudinary_upload(output, idx)
        text = f"{item['caption']}\n\n{' '.join(item['hashtags'])}".strip()
        autoclip.progress("Buffer", 94 + int((idx - 1) / total * 5), f"Adicionando corte {idx}/{total} à fila do TikTok")
        buffer_id = buffer.add_video_to_queue(cloud_url, text)
        results.append({
            "clip": idx,
            "score": plan.score,
            "title": item["title"],
            "cloudinary_url": cloud_url,
            "cloudinary_id": cloud_id,
            "buffer_post_id": buffer_id,
            "reason": plan.reason,
            "framing": framing.reason,
            "start": plan.start,
            "end": plan.end,
        })
        output.unlink(missing_ok=True)

    autoclip.progress("Concluído", 100, f"{len(results)} corte(s) contextuais enviados ao Buffer")
    autoclip.summary("## Resultado Quality v2\n")
    for r in results:
        autoclip.summary(
            f"- **Corte {r['clip']}** — {autoclip.fmt_time(r['start'])}–{autoclip.fmt_time(r['end'])} "
            f"— score {r['score']} — enquadramento: {r['framing']} — Buffer `{r['buffer_post_id']}` — {r['cloudinary_url']}"
        )
        if r["reason"]:
            autoclip.summary(f"  - Contexto editorial: {r['reason']}")
    autoclip.summary("\nLegendas: curtas, fonte menor e posicionadas na parte inferior. Cortes: selecionados com contexto de entrada. Enquadramento: crop apenas quando o assunto está estável; caso contrário, composição completa com fundo desfocado.\n")
