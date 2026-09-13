from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

import autoclip
import quality_v2 as q2
import quality_v4 as q4
import quality_v5 as q5

_ORIGINAL_SELECT = q2.select_contextual_clips
_ORIGINAL_PREPARE = q2.prepare_clip_content
_ORIGINAL_WRITE = q4.write_compact_ass
_ORIGINAL_ANALYZE = q4.analyze_static_shots
_ORIGINAL_DOWNLOAD = autoclip.download_youtube

CURRENT_PROFILE = "podcast"
CURRENT_TITLE = ""
HOOK_BY_BOUNDS: dict[tuple[float, float], str] = {}

PROFILE_LABELS = {
    "auto": "Auto",
    "podcast": "Podcast/Entrevista",
    "talking": "Talking Head",
    "gameplay": "Gameplay",
    "film": "Filme/Série",
}


def _norm_profile(value: str) -> str:
    text = (value or "Auto").strip().lower()
    if text in {"podcast", "podcast/entrevista", "entrevista", "interview"}:
        return "podcast"
    if text in {"talking head", "talking", "talking_head"}:
        return "talking"
    if text in {"gameplay", "game", "jogo"}:
        return "gameplay"
    if text in {"filme/série", "filme/serie", "filme", "série", "serie", "movie", "film", "series"}:
        return "film"
    return "auto"


def _profile_rules(profile: str) -> str:
    if profile == "podcast":
        return "Priorize histórias, opiniões e trocas de conversa autossuficientes."
    if profile == "talking":
        return "Priorize uma ideia, dica, argumento ou história completa da pessoa principal."
    if profile == "gameplay":
        return "Priorize ação, decisão, surpresa, falha, vitória ou reação; evite menus e espera."
    return "Priorize uma cena ou sequência autossuficiente e preserve a continuidade narrativa."


def _video_dimensions(video: Path) -> tuple[int, int]:
    import cv2
    cap = cv2.VideoCapture(str(video))
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    if width <= 0 or height <= 0:
        raise RuntimeError("Não foi possível detectar a resolução do vídeo.")
    return width, height


def _cascades():
    import cv2
    return (
        cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml"),
        cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml"),
    )


def _infer_profile(video: Path, title: str) -> str:
    import cv2
    low = title.lower()
    if any(w in low for w in ("gameplay", "walkthrough", "playthrough", "speedrun", "gaming", "minecraft", "fortnite", "valorant", "roblox", "gta")):
        return "gameplay"
    if any(w in low for w in ("podcast", "interview", "entrevista", "talk show")):
        return "podcast"
    if any(w in low for w in ("movie", "film", "trailer", "scene", "série", "serie")):
        return "film"

    frontal, profile = _cascades()
    cap = cv2.VideoCapture(str(video))
    duration = max(1.0, autoclip.probe_duration(video))
    face_frames = 0
    multi_frames = 0
    samples = 14
    try:
        for i in range(samples):
            t = duration * (i + 0.5) / samples
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            faces = q4._detect_faces(frame, frontal, profile)
            if faces:
                face_frames += 1
            if len(faces) >= 2:
                multi_frames += 1
    finally:
        cap.release()

    face_ratio = face_frames / max(1, samples)
    multi_ratio = multi_frames / max(1, samples)
    if multi_ratio >= 0.22:
        return "podcast"
    if face_ratio >= 0.55:
        return "talking"
    return "film"


def _capture_download(url: str, target_dir: Path):
    global CURRENT_PROFILE, CURRENT_TITLE
    video, info = _ORIGINAL_DOWNLOAD(url, target_dir)
    CURRENT_TITLE = str(info.get("title") or "Vídeo do YouTube")
    requested = _norm_profile(os.getenv("CONTENT_PROFILE", "Auto"))
    CURRENT_PROFILE = _infer_profile(video, CURRENT_TITLE) if requested == "auto" else requested
    autoclip.progress("Perfil", 26, f"{PROFILE_LABELS[CURRENT_PROFILE]} selecionado")
    return video, info


def _text_between(segments: list[dict], start: float, end: float) -> str:
    return " ".join(
        str(s.get("text") or "").strip()
        for s in segments
        if float(s["end"]) > start and float(s["start"]) < end
    ).strip()


def _ambiguous(text: str) -> bool:
    clean = re.sub(r"\s+", " ", text).strip().lower()
    if not clean:
        return True
    bad = (
        "he ", "she ", "they ", "it ", "this ", "that ", "yeah", "yes", "no ", "and ", "but ", "so ",
        "ele ", "ela ", "eles ", "elas ", "isso ", "isto ", "sim", "não", "nao", "e ", "mas ", "então ", "entao ",
    )
    return clean.startswith(bad)


def _extend_plan(plan: q2.ClipPlan, segments: list[dict], lead: float, max_seconds: int) -> q2.ClipPlan | None:
    room = max(0.0, max_seconds - (plan.end - plan.start))
    lead = min(15.0, max(0.0, lead), room)
    if lead < 0.8:
        return None
    wanted = max(float(segments[0]["start"]), plan.start - lead)
    starts = [float(s["start"]) for s in segments if wanted <= float(s["start"]) < plan.start]
    new_start = min(starts) if starts else wanted
    text = _text_between(segments, new_start, plan.end)
    if not text:
        return None
    return q2.ClipPlan(new_start, plan.end, autoclip.score_window(text, plan.end - new_start), text, plan.reason)


def _hook_language() -> str:
    lang = q5._normalize_language(os.getenv("SUBTITLE_LANGUAGE", "English"))
    if lang == "pt-BR":
        return "Escreva o hook em português do Brasil."
    if lang == "en":
        return "Write the hook in English."
    return "Write the hook in the same language as the source dialogue."


def select_contextual_v6(segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str) -> list[q2.ClipPlan]:
    global HOOK_BY_BOUNDS
    HOOK_BY_BOUNDS = {}
    timeline = q2._editor_transcript(segments)
    wanted = min(6, max(count * 3, count))
    prompt = f"""Você é um editor profissional de vídeos curtos. Leia a TRANSCRIÇÃO INTEIRA.
Perfil: {PROFILE_LABELS[CURRENT_PROFILE]}.
Fonte: {source_title}
Devolva até {wanted} candidatos fortes para obter {count} cortes finais, sem sobreposição.
Cada candidato deve ter entre {min_seconds} e {max_seconds} segundos.

Regras obrigatórias:
- Pense como alguém que nunca viu o vídeo original.
- Avalie os PRIMEIROS 5 SEGUNDOS de cada candidato.
- Se os primeiros 5 segundos não deixam claro quem/o que está sendo discutido, use first5_clear=false
  e informe lead_seconds (0 a 15) indicando quanto contexto anterior é necessário.
- Não comece no meio de frase ou com pronome/reação sem referente.
- Prefira contexto/setup -> desenvolvimento -> payoff/conclusão.
- Não corte a ideia final.
- {_profile_rules(CURRENT_PROFILE)}
- hook: no máximo 8 palavras, somente informação sustentada pelo próprio trecho/contexto.
- Se não houver hook factual seguro, use string vazia.
- {_hook_language()}

Responda SOMENTE JSON:
{{"clips":[{{"start":123.4,"end":220.0,"score":92,"reason":"...","first5_clear":true,"lead_seconds":0,"hook":"..."}}]}}

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
                    segments, float(item["start"]), float(item["end"]),
                    min_seconds, max_seconds, float(item.get("score", 0) or 0),
                    str(item.get("reason") or "").strip(),
                )
            except Exception:
                plan = None
            if not plan:
                continue

            clear = bool(item.get("first5_clear", True))
            if _ambiguous(_text_between(segments, plan.start, min(plan.end, plan.start + 6))):
                clear = False
            if not clear:
                try:
                    lead = float(item.get("lead_seconds", 8) or 8)
                except Exception:
                    lead = 8.0
                extended = _extend_plan(plan, segments, lead, max_seconds)
                if not extended:
                    continue
                plan = extended

            if any(
                max(0.0, min(plan.end, s.end) - max(plan.start, s.start))
                / max(1.0, min(plan.end - plan.start, s.end - s.start)) > 0.25
                for s in selected
            ):
                continue

            hook = re.sub(r"\s+", " ", str(item.get("hook") or "")).strip()
            if len(hook) > 70:
                hook = ""
            selected.append(plan)
            if show_hook and hook:
                HOOK_BY_BOUNDS[(round(plan.start, 2), round(plan.end, 2))] = hook
            if len(selected) >= count:
                break

    if len(selected) < count:
        for item in autoclip.select_best(segments, min_seconds, max_seconds, count * 4):
            plan = q2._candidate_from_bounds(
                segments, item.start, item.end, min_seconds, max_seconds, item.score,
                "Fallback local com validação de contexto",
            )
            if not plan:
                continue
            if _ambiguous(_text_between(segments, plan.start, min(plan.end, plan.start + 6))):
                plan = _extend_plan(plan, segments, 8, max_seconds)
                if not plan:
                    continue
            if any(
                max(0.0, min(plan.end, s.end) - max(plan.start, s.start))
                / max(1.0, min(plan.end - plan.start, s.end - s.start)) > 0.25
                for s in selected
            ):
                continue
            selected.append(plan)
            if len(selected) >= count:
                break
    return selected[:count]


def _wrap_hook(text: str) -> str:
    words = re.sub(r"\s+", " ", text).strip().split()
    if len(" ".join(words)) <= 32:
        return " ".join(words)
    first, second = [], []
    for word in words:
        if not second and len(" ".join(first + [word])) <= 30 and len(first) < 5:
            first.append(word)
        else:
            second.append(word)
    return " ".join(first) + (r"\N" + " ".join(second) if second else "")


def write_ass_v6(segments: list[dict], start: float, end: float, target: Path) -> None:
    hook = HOOK_BY_BOUNDS.get((round(start, 2), round(end, 2)), "")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,DejaVu Sans,60,&H00FFFFFF,&H000000FF,&H00000000,&H50000000,0,0,0,0,100,100,0,0,1,2.8,0,2,90,90,155,1
Style: Hook,DejaVu Sans,46,&H00FFFFFF,&H000000FF,&H00000000,&H72000000,1,0,0,0,100,100,0,0,3,0,0,8,90,90,115,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events: list[str] = []
    if hook:
        safe_hook = _wrap_hook(hook).replace("{", "(").replace("}", ")")
        events.append(f"Dialogue: 1,0:00:00.00,0:00:02.00,Hook,,0,0,0,,{safe_hook}")

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


def _scene_threshold() -> float:
    return {"podcast": 0.36, "talking": 0.40, "gameplay": 0.53, "film": 0.31}.get(CURRENT_PROFILE, 0.38)


def _scene_boundaries(video: Path, start: float, end: float) -> list[float]:
    import cv2
    duration = max(0.2, end - start)
    cap = cv2.VideoCapture(str(video))
    bounds = [0.0]
    prev_hist = prev_gray = None
    last_cut = -10.0
    rel = 0.0
    try:
        while rel < duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + rel) * 1000)
            ok, frame = cap.read()
            if not ok:
                rel += 0.42
                continue
            small = cv2.resize(frame, (160, 90))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev_hist is not None:
                hd = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
                fd = float(np.mean(cv2.absdiff(prev_gray, gray))) / 255.0
                score = 0.76 * hd + 0.24 * min(1.0, fd * 4)
                if score >= _scene_threshold() and rel - last_cut >= 1.0 and rel >= 0.8:
                    bounds.append(rel)
                    last_cut = rel
            prev_hist, prev_gray = hist, gray
            rel += 0.42
    finally:
        cap.release()
    if duration - bounds[-1] < 0.7 and len(bounds) > 1:
        bounds[-1] = duration
    else:
        bounds.append(duration)
    return sorted(set(round(x, 3) for x in bounds))


def _nearest_speech_cut(desired: float, low: float, high: float, segments: list[dict], clip_start: float) -> float:
    options = [float(s["end"]) - clip_start for s in segments if low <= float(s["end"]) - clip_start <= high]
    return min(options, key=lambda x: abs(x - desired)) if options else desired


def _intervals(scene_bounds: list[float], segments: list[dict], clip_start: float) -> list[tuple[float, float, int]]:
    target, maximum = {
        "podcast": (5.4, 7.2), "talking": (7.2, 9.5),
        "gameplay": (8.0, 10.5), "film": (9.0, 12.0),
    }.get(CURRENT_PROFILE, (6.0, 8.0))
    out = []
    for scene_id, (a, b) in enumerate(zip(scene_bounds, scene_bounds[1:])):
        cursor = a
        while b - cursor > maximum:
            desired = cursor + target
            low = cursor + max(3.0, target - 1.5)
            high = min(b - 1.0, cursor + maximum)
            cut = _nearest_speech_cut(desired, low, high, segments, clip_start) if CURRENT_PROFILE in {"podcast", "talking"} else desired
            cut = max(cursor + 2.5, min(b - 0.8, cut))
            out.append((cursor, cut, scene_id))
            cursor = cut
        if b - cursor >= 0.55:
            out.append((cursor, b, scene_id))
    return out


def _extract_audio(video: Path, start: float, end: float, path: Path) -> tuple[np.ndarray, int]:
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{end-start:.3f}",
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32), rate


def _energy(samples: np.ndarray, rate: int, rel_t: float) -> float:
    if samples.size < 2:
        return 0.0
    center = int(max(0.0, rel_t) * rate)
    half = int(0.14 * rate)
    chunk = samples[max(0, center-half):min(samples.size, center+half)]
    if chunk.size < 2:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
    stride = max(1, rate // 200)
    scale = float(np.percentile(np.abs(samples[::stride]), 90))
    return min(2.0, rms / max(300.0, scale))


def _speech_active(segments: list[dict], abs_t: float) -> bool:
    return any(float(s["start"]) - 0.15 <= abs_t <= float(s["end"]) + 0.15 for s in segments)


class SpeakerRegistry:
    def __init__(self, width: int, crop_w: int):
        self.width = width
        self.crop_w = crop_w
        self.centers: dict[str, float] = {}
        self.next_id = 0

    def assign(self, center: float) -> str:
        if self.centers:
            speaker, dist = min(((sid, abs(c-center)) for sid, c in self.centers.items()), key=lambda x: x[1])
            if dist <= self.crop_w * 0.36:
                self.centers[speaker] = 0.82 * self.centers[speaker] + 0.18 * center
                return speaker
        sid = f"Speaker {chr(ord('A') + min(self.next_id, 25))}"
        self.next_id += 1
        self.centers[sid] = center
        return sid


def _motion_center(video: Path, abs_start: float, abs_end: float, width: int) -> float:
    import cv2
    cap = cv2.VideoCapture(str(video))
    centers = []
    try:
        for frac in (0.25, 0.5, 0.75):
            t = abs_start + (abs_end-abs_start) * frac
            cap.set(cv2.CAP_PROP_POS_MSEC, t*1000)
            ok1, a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t+0.14)*1000)
            ok2, b = cap.read()
            if not ok1 or not ok2:
                continue
            ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (160, 90))
            gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (160, 90))
            col = cv2.absdiff(ga, gb).astype(np.float32).mean(axis=0)
            if float(col.sum()) > 1e-6:
                xs = np.arange(160, dtype=np.float32)
                centers.append(float((xs*col).sum()/col.sum()) / 159 * width)
    finally:
        cap.release()
    return float(np.median(centers)) if centers else width/2


def _face_or_motion(video: Path, abs_start: float, abs_end: float, width: int) -> float:
    import cv2
    frontal, profile = _cascades()
    cap = cv2.VideoCapture(str(video))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, ((abs_start+abs_end)/2)*1000)
        ok, frame = cap.read()
        faces = q4._detect_faces(frame, frontal, profile) if ok else []
        if faces:
            x, y, w, h = max(faces, key=lambda f: int(f[2])*int(f[3]))
            return float(x+w/2)
    finally:
        cap.release()
    return _motion_center(video, abs_start, abs_end, width)


def analyze_static_v6(video: Path, plan: q2.ClipPlan, transcript_segments: list[dict]) -> q4.ShotPlan:
    import cv2
    width, height = _video_dimensions(video)
    crop_w = min(width, max(2, int(height*9/16)))
    half = crop_w/2
    bounds = _scene_boundaries(video, plan.start, plan.end)
    intervals = _intervals(bounds, transcript_segments, plan.start)
    shots: list[q4.Shot] = []

    if CURRENT_PROFILE in {"podcast", "talking"}:
        wav = autoclip.WORK / f"speaker_{int(plan.start*1000)}.wav"
        try:
            samples, rate = _extract_audio(video, plan.start, plan.end, wav)
        except Exception as exc:
            autoclip.log(f"Aviso: energia de áudio indisponível: {exc}")
            samples, rate = np.zeros(1, dtype=np.float32), 16000
        frontal, profile = _cascades()
        registry = SpeakerRegistry(width, crop_w)
        cap = cv2.VideoCapture(str(video))
        previous = ""
        speakers_seen: set[str] = set()
        try:
            for idx, (a, b, scene_id) in enumerate(intervals):
                by_speaker: dict[str, dict] = {}
                for frac in (0.25, 0.5, 0.75):
                    rel_t = a + (b-a)*frac
                    abs_t = plan.start + rel_t
                    cap.set(cv2.CAP_PROP_POS_MSEC, abs_t*1000)
                    ok1, fa = cap.read()
                    cap.set(cv2.CAP_PROP_POS_MSEC, (abs_t+0.13)*1000)
                    ok2, fb = cap.read()
                    if not ok1 or not ok2:
                        continue
                    scored = q4._activity_scores(fa, fb, frontal, profile)
                    e = _energy(samples, rate, rel_t)
                    speech = _speech_active(transcript_segments, abs_t)
                    for rank, (motion_score, center, area) in enumerate(scored[:3]):
                        sid = registry.assign(center)
                        ent = by_speaker.setdefault(sid, {"score": 0.0, "centers": [], "hits": 0, "reaction": 0.0})
                        gate = 1.0 if speech else 0.28
                        ent["score"] += motion_score * gate * (0.75 + min(1.5, e))
                        ent["reaction"] += (motion_score if rank > 0 else motion_score*0.35)
                        ent["centers"].append(center)
                        ent["hits"] += 1
                if by_speaker:
                    if previous in by_speaker:
                        by_speaker[previous]["score"] *= 1.12
                    ranked = sorted(by_speaker.items(), key=lambda kv: (kv[1]["score"], kv[1]["hits"]), reverse=True)
                    sid, best = ranked[0]
                    center = float(np.median(best["centers"]))
                    previous = sid
                    speakers_seen.add(sid)
                    alternate = None
                    if len(ranked) > 1:
                        aid, alt = max(ranked[1:], key=lambda kv: kv[1]["reaction"])
                        ac = float(np.median(alt["centers"]))
                        if alt["hits"] >= 2 and abs(ac-center) > crop_w*0.27:
                            alternate = (aid, ac)
                    duration = b-a
                    if CURRENT_PROFILE == "podcast" and alternate and duration >= 5 and idx % 3 == 2:
                        aid, ac = alternate
                        r_end = min(a+1.25, b-2.4)
                        if r_end > a+0.8:
                            shots.append(q4.Shot(a, r_end, max(half, min(width-half, ac)), "reaction"))
                            shots.append(q4.Shot(r_end, b, max(half, min(width-half, center)), "speaker"))
                            speakers_seen.add(aid)
                            continue
                    shots.append(q4.Shot(a, b, max(half, min(width-half, center)), "speaker"))
                else:
                    center = _motion_center(video, plan.start+a, plan.start+b, width)
                    shots.append(q4.Shot(a, b, max(half, min(width-half, center)), "fallback"))
        finally:
            cap.release()
            wav.unlink(missing_ok=True)
        reason = f"{len(shots)} planos estáticos · {max(1,len(bounds)-1)} cena(s) original(is) · {len(speakers_seen)} Speaker A/B/C acompanhado(s)"
    elif CURRENT_PROFILE == "gameplay":
        for a, b, scene_id in intervals:
            center = _motion_center(video, plan.start+a, plan.start+b, width)
            shots.append(q4.Shot(a, b, max(half, min(width-half, center)), "action"))
        reason = f"{len(shots)} planos · foco na atividade do gameplay · {max(1,len(bounds)-1)} cena(s)"
    else:
        for a, b, scene_id in intervals:
            center = _face_or_motion(video, plan.start+a, plan.start+b, width)
            shots.append(q4.Shot(a, b, max(half, min(width-half, center)), "scene"))
        reason = f"{len(shots)} planos alinhados a {max(1,len(bounds)-1)} cena(s) original(is)"

    if not shots:
        shots = [q4.Shot(0.0, plan.end-plan.start, width/2, "fallback")]
    return q4.ShotPlan(width, height, shots, reason)


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    autoclip.download_youtube = _capture_download
    q4.q2.select_contextual_clips = select_contextual_v6
    q4.q2.prepare_clip_content = q5.prepare_clip_content
    q4.write_compact_ass = write_ass_v6
    q4.analyze_static_shots = analyze_static_v6
    try:
        autoclip.log("Quality v6: falantes + cenas + perfis + contexto/hook")
        q4.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary(
            f"\n### Quality v6\nPerfil: **{PROFILE_LABELS[CURRENT_PROFILE]}** · "
            f"cenas originais respeitadas · foco por fala/áudio/rosto · "
            f"validação dos primeiros 5 s · hook contextual opcional · legenda 60.\n"
        )
    finally:
        autoclip.download_youtube = _ORIGINAL_DOWNLOAD
        q4.q2.select_contextual_clips = _ORIGINAL_SELECT
        q4.q2.prepare_clip_content = _ORIGINAL_PREPARE
        q4.write_compact_ass = _ORIGINAL_WRITE
        q4.analyze_static_shots = _ORIGINAL_ANALYZE
