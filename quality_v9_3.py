from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import autoclip
import quality_v2 as q2
import quality_v4 as q4
import quality_v6 as q6
import quality_v6_1 as q61
import quality_v8 as q8
import quality_v9_2 as q92


_BASE_SELECTOR = q8.select_retention_clips
_BASE_RENDERER = q92.render_v9_2
END_REVIEW: dict[tuple[float, float], dict] = {}
AUTO_REVIEW_RESULTS: list[dict] = []


@dataclass
class RenderReview:
    passed: bool
    critical: bool
    issues: list[str]
    focus_failures: list[int]
    split_failures: list[int]
    subtitle_ok: bool


def _flag(name: str, default: bool = True) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


def _auto_review_enabled() -> bool:
    return _flag("AUTO_REVIEW", True)


def _text_between(segments: list[dict], start: float, end: float) -> str:
    return " ".join(
        str(s.get("text") or "").strip()
        for s in segments
        if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end
    ).strip()


def _ending_context(plan: q2.ClipPlan, segments: list[dict]) -> str:
    start = max(float(segments[0]["start"]), plan.end - 28.0)
    end = min(float(segments[-1]["end"]), plan.end + 24.0)
    rows = []
    for s in segments:
        ss, ee = float(s["start"]), float(s["end"])
        if ee <= start or ss >= end:
            continue
        text = re.sub(r"\s+", " ", str(s.get("text") or "")).strip()
        if text:
            rows.append(f"[{ss:.2f}-{ee:.2f}] {text}")
    return "\n".join(rows)


def _snap_to_segment_end(value: float, segments: list[dict], low: float, high: float) -> float:
    options = [float(s["end"]) for s in segments if low <= float(s["end"]) <= high]
    if not options:
        return max(low, min(high, value))
    return min(options, key=lambda x: abs(x - value))


def _transfer_bound_metadata(old_key: tuple[float, float], new_key: tuple[float, float]) -> None:
    if old_key == new_key:
        return
    if old_key in q6.HOOK_BY_BOUNDS and new_key not in q6.HOOK_BY_BOUNDS:
        q6.HOOK_BY_BOUNDS[new_key] = q6.HOOK_BY_BOUNDS.pop(old_key)
    if old_key in q8.RETENTION_META and new_key not in q8.RETENTION_META:
        q8.RETENTION_META[new_key] = q8.RETENTION_META.pop(old_key)


def _deterministic_finish(plan: q2.ClipPlan, segments: list[dict], max_seconds: int) -> q2.ClipPlan:
    """Never stop inside a Whisper segment; finish at the next speech boundary when possible."""
    new_end = plan.end
    for s in segments:
        ss, ee = float(s["start"]), float(s["end"])
        if ss < plan.end < ee - 0.12:
            hard = min(float(segments[-1]["end"]), plan.start + max_seconds)
            new_end = min(ee, hard)
            break
    if new_end <= plan.end + 0.05:
        return plan
    text = _text_between(segments, plan.start, new_end)
    return q2.ClipPlan(plan.start, new_end, plan.score, text, plan.reason)


def select_v9_3(
    segments: list[dict], min_seconds: int, max_seconds: int, count: int, source_title: str
) -> list[q2.ClipPlan]:
    plans = _BASE_SELECTOR(segments, min_seconds, max_seconds, count, source_title)
    if not plans:
        return plans

    END_REVIEW.clear()
    reviewed = [_deterministic_finish(p, segments, max_seconds) for p in plans]

    payload = []
    for idx, plan in enumerate(reviewed, 1):
        payload.append(
            {
                "clip": idx,
                "current_start": round(plan.start, 2),
                "current_end": round(plan.end, 2),
                "context_around_end": _ending_context(plan, segments),
            }
        )

    prompt = f"""Você é o revisor FINAL de cortes curtos. Sua tarefa é revisar SOMENTE o encerramento dos cortes abaixo.

Um final aprovado precisa:
- concluir a frase atual;
- concluir a ideia/história/pergunta que o corte prometeu;
- NÃO terminar enquanto alguém ainda está falando;
- NÃO incluir os primeiros segundos de um assunto novo só porque vieram logo depois da conclusão;
- evitar despedidas fracas, transições e frases que claramente pedem continuação.

Para cada corte, classifique:
- good: termina naturalmente;
- unfinished_speech: cortou fala/ideia antes da conclusão;
- new_topic_started: a história já terminou, mas o corte entrou no começo de outro assunto;
- weak_close: não está truncado, porém existe um fechamento claramente melhor alguns segundos antes/depois.

Escolha ideal_end em segundos absolutos, sempre em uma marca de fim de fala mostrada no contexto. Para unfinished_speech, avance apenas até concluir a mesma ideia. Para new_topic_started, volte ao último fechamento completo antes do novo assunto. Não invente conteúdo.

Responda SOMENTE JSON válido:
{{"clips":[{{"clip":1,"status":"good","ideal_end":123.45,"confidence":0.95,"reason":"..."}}]}}

Fonte: {source_title}
CORTES:
{payload}
"""

    data = q2._gemini_json(prompt, attempts=3)
    by_clip = {}
    if isinstance(data, dict) and isinstance(data.get("clips"), list):
        for item in data["clips"]:
            try:
                by_clip[int(item.get("clip"))] = item
            except Exception:
                pass

    final: list[q2.ClipPlan] = []
    for idx, plan in enumerate(reviewed, 1):
        old_key = (round(plan.start, 2), round(plan.end, 2))
        item = by_clip.get(idx) or {}
        status = str(item.get("status") or "local_check").strip()
        reason = str(item.get("reason") or "").strip()
        confidence = float(item.get("confidence", 0) or 0)
        candidate_end = plan.end

        try:
            ideal = float(item.get("ideal_end"))
        except Exception:
            ideal = plan.end

        low = plan.start + min_seconds
        high = min(float(segments[-1]["end"]), plan.start + max_seconds)
        if confidence >= 0.55 and low <= ideal <= high:
            snapped = _snap_to_segment_end(ideal, segments, low, high)
            if status in {"unfinished_speech", "weak_close"} and snapped > plan.end - 0.3:
                candidate_end = snapped
            elif status == "new_topic_started" and snapped < plan.end - 0.3:
                candidate_end = snapped
            elif status == "good" and abs(snapped - plan.end) <= 2.0:
                candidate_end = snapped

        candidate_end = max(low, min(high, candidate_end))
        text = _text_between(segments, plan.start, candidate_end)
        adjusted = q2.ClipPlan(plan.start, candidate_end, plan.score, text, plan.reason)
        adjusted = _deterministic_finish(adjusted, segments, max_seconds)
        new_key = (round(adjusted.start, 2), round(adjusted.end, 2))
        _transfer_bound_metadata(old_key, new_key)
        END_REVIEW[new_key] = {
            "status": status,
            "confidence": round(confidence, 2),
            "reason": reason,
            "changed": abs(adjusted.end - plan.end) > 0.15,
            "old_end": plan.end,
            "new_end": adjusted.end,
        }
        final.append(adjusted)

    return final


def _ass_seconds(value: str) -> float:
    try:
        h, m, rest = value.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    except Exception:
        return -1.0


def _review_ass(ass: Path, duration: float) -> tuple[bool, list[str]]:
    issues = []
    try:
        text = ass.read_text(encoding="utf-8")
    except Exception as exc:
        return False, [f"legenda ilegível: {exc}"]
    if "Style: Default,DejaVu Sans,70," not in text:
        issues.append("estilo de legenda não está em tamanho 70")
    dialogues = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    if not dialogues:
        issues.append("nenhuma legenda foi encontrada")
    for line in dialogues:
        parts = line.split(",", 9)
        if len(parts) < 10:
            issues.append("linha ASS inválida")
            continue
        start = _ass_seconds(parts[1])
        end = _ass_seconds(parts[2])
        if start < -0.01 or end <= start or end > duration + 0.6:
            issues.append("timing de legenda fora do clipe")
            break
    return not issues, issues


def _sample_output_frame(video: Path, seconds: float):
    import cv2
    cap = cv2.VideoCapture(str(video))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000)
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def _panel_face_metrics(frame, top: bool):
    import cv2
    frontal, profile = q6._cascades()
    h, w = frame.shape[:2]
    panel = frame[: h // 2] if top else frame[h // 2 :]
    faces = q4._detect_faces(panel, frontal, profile)
    if not faces:
        return None
    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
    return ((x + fw / 2) / w, (y + fh / 2) / (h / 2), fw / w, fh / (h / 2))


def _full_face_metrics(frame):
    frontal, profile = q6._cascades()
    h, w = frame.shape[:2]
    faces = q4._detect_faces(frame, frontal, profile)
    if not faces:
        return None
    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
    return ((x + fw / 2) / w, (y + fh / 2) / h, fw / w, fh / h)


def _review_rendered(output: Path, framing: q92.Plan92, ass: Path, duration: float) -> RenderReview:
    issues: list[str] = []
    focus_failures: list[int] = []
    split_failures: list[int] = []
    critical = False

    try:
        probe = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(output)],
            text=True,
        ).strip()
        if probe != "1080x1920":
            issues.append(f"resolução inesperada {probe}")
            critical = True
    except Exception as exc:
        issues.append(f"ffprobe falhou: {exc}")
        critical = True

    subtitle_ok, subtitle_issues = _review_ass(ass, duration)
    issues.extend(subtitle_issues)
    if not subtitle_ok:
        critical = True

    if q6.CURRENT_PROFILE in {"podcast", "talking"}:
        for idx, shot in enumerate(framing.shots):
            shot_len = shot.end - shot.start
            if shot_len < 0.7:
                continue
            checks = []
            for frac in (0.34, 0.68):
                frame = _sample_output_frame(output, shot.start + shot_len * frac)
                if frame is None:
                    continue
                if shot.kind == "split" and shot.secondary is not None:
                    top = _panel_face_metrics(frame, True)
                    bottom = _panel_face_metrics(frame, False)
                    if top is None or bottom is None:
                        checks.append(("split_missing", 1.0))
                    else:
                        top_err = abs(top[0] - 0.5)
                        bottom_err = abs(bottom[0] - 0.5)
                        gray_top = frame[: frame.shape[0] // 2]
                        gray_bottom = frame[frame.shape[0] // 2 :]
                        import cv2
                        gt = cv2.resize(cv2.cvtColor(gray_top, cv2.COLOR_BGR2GRAY), (96, 96))
                        gb = cv2.resize(cv2.cvtColor(gray_bottom, cv2.COLOR_BGR2GRAY), (96, 96))
                        duplicate_score = float(np.mean(cv2.absdiff(gt, gb)))
                        checks.append(("split", max(top_err, bottom_err), duplicate_score))
                else:
                    metrics = _full_face_metrics(frame)
                    if metrics is not None:
                        x_err = abs(metrics[0] - 0.5)
                        y = metrics[1]
                        checks.append(("focus", x_err, y))

            if shot.kind == "split" and shot.secondary is not None:
                bad = 0
                for item in checks:
                    if item[0] == "split_missing":
                        bad += 1
                    elif item[0] == "split" and (item[1] > 0.18 or item[2] < 7.5):
                        bad += 1
                if bad >= max(1, len(checks)):
                    split_failures.append(idx)
            else:
                bad = 0
                valid = 0
                for item in checks:
                    if item[0] == "focus":
                        valid += 1
                        if item[1] > 0.16 or not (0.16 <= item[2] <= 0.54):
                            bad += 1
                if valid >= 2 and bad == valid:
                    focus_failures.append(idx)

    if focus_failures:
        issues.append(f"foco fora do enquadramento em {len(focus_failures)} plano(s)")
    if split_failures:
        issues.append(f"split-screen inválido/duplicado em {len(split_failures)} plano(s)")
    passed = not critical and not focus_failures and not split_failures
    return RenderReview(passed, critical, issues, focus_failures, split_failures, subtitle_ok)


def _strict_portrait_transform(width: int, height: int, focus: q92.FocusBox, zoom_hint: float, kind: str) -> str:
    ratio = 9 / 16
    if width / height < ratio:
        return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"

    face_h = max(1.0, focus.h)
    desired_fraction = 0.31 if kind in {"reaction_emphasis", "attention_punch"} else 0.27
    crop_h = min(height * max(0.58, min(0.92, zoom_hint)), face_h / desired_fraction if focus.h > 1 else height * 0.76)
    crop_h = max(height * 0.52, min(float(height), crop_h))

    # If the subject is near a source edge, tighten enough that the face can actually sit near center.
    horizontal_room = max(80.0, 2.0 * min(focus.x, width - focus.x) * 0.96)
    crop_h = min(crop_h, horizontal_room / ratio)
    crop_h = max(2, int(crop_h))
    crop_h -= crop_h % 2
    crop_w = max(2, int(crop_h * ratio))
    crop_w -= crop_w % 2
    if crop_w > width:
        crop_w = width - (width % 2)
        crop_h = max(2, int(crop_w / ratio))
        crop_h -= crop_h % 2

    x = int(max(0, min(width - crop_w, focus.x - crop_w / 2)))
    target_y = crop_h * 0.36
    y = int(max(0, min(height - crop_h, focus.y - target_y))) if focus.h > 1 else int(max(0, (height - crop_h) / 2))
    return f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:1920,setsar=1"


def _strict_panel_transform(width: int, height: int, focus: q92.FocusBox) -> str:
    ratio = 1080 / 960
    if width / height < ratio:
        return "scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1"
    face_h = max(1.0, focus.h)
    crop_h = face_h / 0.37 if focus.h > 1 else height * 0.66
    crop_h = max(height * 0.44, min(height * 0.82, crop_h))
    horizontal_room = max(80.0, 2.0 * min(focus.x, width - focus.x) * 0.96)
    crop_h = min(crop_h, horizontal_room / ratio)
    crop_h = max(2, int(crop_h))
    crop_h -= crop_h % 2
    crop_w = max(2, int(crop_h * ratio))
    crop_w -= crop_w % 2
    if crop_w > width:
        crop_w = width - (width % 2)
        crop_h = max(2, int(crop_w / ratio))
        crop_h -= crop_h % 2
    x = int(max(0, min(width - crop_w, focus.x - crop_w / 2)))
    y = int(max(0, min(height - crop_h, focus.y - crop_h * 0.40))) if focus.h > 1 else int(max(0, (height - crop_h) / 2))
    return f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:960,setsar=1"


def _render_plan(
    video: Path,
    plan: q2.ClipPlan,
    framing: q92.Plan92,
    ass: Path,
    output: Path,
    idx: int,
    total: int,
    strict: bool,
    disable_splits: set[int] | None = None,
) -> None:
    disable_splits = disable_splits or set()
    width, height = framing.width, framing.height
    escaped = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    chains = []
    labels = "".join(f"[src{i}]" for i in range(len(framing.shots)))
    chains.append(f"[0:v]split={len(framing.shots)}{labels}")

    for i, shot in enumerate(framing.shots):
        use_split = shot.kind == "split" and shot.secondary is not None and i not in disable_splits
        if use_split:
            top = _strict_panel_transform(width, height, shot.primary) if strict else q92._panel_transform(width, height, shot.primary)
            bottom = _strict_panel_transform(width, height, shot.secondary) if strict else q92._panel_transform(width, height, shot.secondary)
            chains.append(f"[src{i}]split=2[s{i}a][s{i}b]")
            chains.append(f"[s{i}a]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{top}[top{i}]")
            chains.append(f"[s{i}b]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{bottom}[bot{i}]")
            chains.append(f"[top{i}][bot{i}]vstack=inputs=2,drawbox=x=0:y=956:w=1080:h=8:color=white@0.28:t=fill[v{i}]")
        else:
            transform = _strict_portrait_transform(width, height, shot.primary, shot.zoom, shot.kind) if strict else q92._portrait_transform(width, height, shot.primary, shot.zoom, shot.kind)
            chains.append(f"[src{i}]trim=start={shot.start:.3f}:end={shot.end:.3f},setpts=PTS-STARTPTS,{transform}[v{i}]")

    concat_inputs = "".join(f"[v{i}]" for i in range(len(framing.shots)))
    chains.append(f"{concat_inputs}concat=n={len(framing.shots)}:v=1:a=0[cutv]")
    chains.append(f"[cutv]subtitles='{escaped}'[outv]")
    duration = max(0.1, plan.end - plan.start)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{plan.start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
        "-filter_complex", ";".join(chains), "-map", "[outv]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "-pix_fmt", "yuv420p", "-aspect", "9:16",
        "-progress", "pipe:1", "-nostats", "-loglevel", "error", str(output),
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
                    if pct >= last + 10:
                        last = pct
                        overall = 68 + int(((idx - 1) + pct / 100) / max(1, total) * 18)
                        mode = "correção" if strict else "render"
                        autoclip.progress("Render", overall, f"Corte {idx}/{total}: {pct}% · {mode}")
                except Exception:
                    pass
    stderr = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"FFmpeg v9.3 falhou: {stderr[-3000:]}")


def render_v9_3(video: Path, plan: q2.ClipPlan, segments: list[dict], ass: Path, output: Path, idx: int, total: int):
    framing = q92.analyze_v9_2(video, plan, segments)
    duration = max(0.1, plan.end - plan.start)
    _render_plan(video, plan, framing, ass, output, idx, total, strict=False)

    if not _auto_review_enabled():
        return framing

    autoclip.progress("Auto Review", 87, f"Revisando corte {idx}/{total}: foco, split, legenda e estrutura")
    first = _review_rendered(output, framing, ass, duration)
    corrected = False
    final_review = first

    if not first.passed and not first.critical:
        autoclip.log("Auto Review encontrou: " + "; ".join(first.issues))
        # Invalid split screens are replaced by the primary-person shot; framing problems trigger a stricter crop.
        output.unlink(missing_ok=True)
        _render_plan(
            video, plan, framing, ass, output, idx, total,
            strict=bool(first.focus_failures),
            disable_splits=set(first.split_failures),
        )
        corrected = True
        final_review = _review_rendered(output, framing, ass, duration)

    if final_review.critical:
        raise RuntimeError("Auto Review encontrou erro crítico: " + "; ".join(final_review.issues))

    AUTO_REVIEW_RESULTS.append(
        {
            "clip": idx,
            "passed": final_review.passed,
            "corrected": corrected,
            "first_issues": first.issues,
            "remaining_issues": final_review.issues,
        }
    )
    if corrected:
        autoclip.log(
            f"Auto Review corte {idx}: correção automática aplicada; "
            + ("aprovado na segunda revisão" if final_review.passed else "restaram alertas não críticos")
        )
    else:
        autoclip.log(f"Auto Review corte {idx}: aprovado sem correções")
    return framing


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_selector = q8.select_retention_clips
    original_renderer = q92.render_v9_2
    AUTO_REVIEW_RESULTS.clear()
    try:
        q8.select_retention_clips = select_v9_3
        q92.render_v9_2 = render_v9_3
        autoclip.log(
            "Quality v9.3: final protegido + Auto Review={} + legenda 70".format(
                "on" if _auto_review_enabled() else "off"
            )
        )
        q92.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Quality v9.3 — Auto Review\n")
        autoclip.summary("Legenda: **70** · proteção de encerramento: **ativa**.\n")
        for key, info in sorted(END_REVIEW.items()):
            start, end = key
            changed = " · fim ajustado automaticamente" if info.get("changed") else ""
            autoclip.summary(
                f"- {autoclip.fmt_time(start)}–{autoclip.fmt_time(end)} — final **{info.get('status','revisado')}** "
                f"(confiança {info.get('confidence',0):g}){changed}"
            )
            if info.get("reason"):
                autoclip.summary(f"  - {info['reason']}")
        if _auto_review_enabled():
            for item in AUTO_REVIEW_RESULTS:
                state = "aprovado" if item["passed"] else "aprovado com alerta"
                correction = " · correção automática aplicada" if item["corrected"] else ""
                autoclip.summary(f"- **Auto Review corte {item['clip']}** — {state}{correction}")
                if item["remaining_issues"]:
                    autoclip.summary("  - Alertas: " + "; ".join(item["remaining_issues"]))
        autoclip.summary(
            "\nO Auto Review verifica o MP4 renderizado antes do upload: resolução, legenda/timing, centralização de rosto "
            "e consistência do split-screen. Quando encontra problema corrigível, renderiza novamente uma vez com crop mais rigoroso "
            "ou remove o split defeituoso.\n"
        )
    finally:
        q8.select_retention_clips = original_selector
        q92.render_v9_2 = original_renderer
