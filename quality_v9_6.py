from __future__ import annotations

from pathlib import Path

import numpy as np

import autoclip
import quality_v4 as q4
import quality_v6 as q6
import quality_v9_2 as q92
import quality_v9_3 as q93
import quality_v9_4 as q94
import quality_v9_5 as q95


_BASE_ANALYZE = q94.analyze_v9_4


def _mouth_window_score(frame_a, frame_b, frame_c, face) -> float:
    """Use several moments and discount head motion more aggressively than v9.4."""
    import cv2

    x, y, w, h = [int(v) for v in face]
    frames = [frame_a, frame_b, frame_c]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    fh, fw = grays[0].shape[:2]
    x1, x2 = max(0, x), min(fw, x + w)
    mouth_y1 = max(0, y + int(h * 0.50))
    mouth_y2 = min(fh, y + int(h * 0.92))
    upper_y1 = max(0, y + int(h * 0.10))
    upper_y2 = min(fh, y + int(h * 0.46))
    if x2 <= x1 or mouth_y2 <= mouth_y1 or upper_y2 <= upper_y1:
        return 0.0

    mouth_vals = []
    upper_vals = []
    for i, j in ((0, 1), (1, 2), (0, 2)):
        ma = grays[i][mouth_y1:mouth_y2, x1:x2]
        mb = grays[j][mouth_y1:mouth_y2, x1:x2]
        ua = grays[i][upper_y1:upper_y2, x1:x2]
        ub = grays[j][upper_y1:upper_y2, x1:x2]
        if ma.size and mb.size and ua.size and ub.size:
            mouth_vals.append(float(np.mean(cv2.absdiff(ma, mb))))
            upper_vals.append(float(np.mean(cv2.absdiff(ua, ub))))
    if not mouth_vals:
        return 0.0

    mouth = float(np.median(mouth_vals))
    upper = float(np.median(upper_vals)) if upper_vals else 0.0
    specific = max(0.0, mouth - 0.90 * upper)
    ratio = mouth / max(1.8, upper)
    return specific * (0.72 + 0.28 * min(2.0, ratio))


def _speaker_timeline_v9_6(
    video: Path,
    plan,
    shot,
    transcript_segments: list[dict],
    audio_samples: np.ndarray,
    audio_rate: int,
):
    """Conservative active-speaker lock: strong evidence in, slow evidence out."""
    import cv2

    abs_start = plan.start + float(shot.start)
    abs_end = plan.start + float(shot.end)
    duration = max(0.1, abs_end - abs_start)
    if duration < 0.8:
        return [], [], 0

    frontal, profile = q6._cascades()
    cap = cv2.VideoCapture(str(video))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tracks: list[q94.FaceTrack] = []
    rows: list[tuple[float, int | None, float]] = []
    step = 0.30 if duration <= 14 else 0.36
    t = abs_start + min(0.18, duration * 0.07)
    sample_count = 0

    try:
        while t <= abs_end - 0.24:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_a, a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.10) * 1000)
            ok_b, b = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.20) * 1000)
            ok_c, c = cap.read()
            if not (ok_a and ok_b and ok_c):
                t += step
                continue

            faces = q4._detect_faces(a, frontal, profile)
            assigned = q94._assign_tracks(tracks, faces, t, width)
            sample_count += 1
            speech_active = q6._speech_active(transcript_segments, t)
            rel_audio = max(0.0, t - plan.start)
            energy = q6._energy(audio_samples, audio_rate, rel_audio) if audio_samples.size > 1 else (1.0 if speech_active else 0.0)

            scored: list[tuple[float, q94.FaceTrack]] = []
            if speech_active:
                gate = 0.62 + min(1.15, energy)
                for track, face in assigned:
                    raw = _mouth_window_score(a, b, c, face)
                    area = float(face[2] * face[3]) / max(1.0, a.shape[0] * a.shape[1])
                    # Size only breaks very close ties; it cannot choose a silent large face.
                    score = raw * gate + min(0.22, area * 4.0)
                    track.scores.append(score)
                    scored.append((score, track))

            winner = None
            top_score = 0.0
            if scored:
                scored.sort(key=lambda item: item[0], reverse=True)
                top_score, top_track = scored[0]
                second = scored[1][0] if len(scored) > 1 else 0.0
                margin = top_score - second
                ratio = top_score / max(0.25, second)
                if top_score >= 1.35 and (margin >= 0.30 or ratio >= 1.32):
                    winner = top_track.track_id
                    top_track.wins += 1
            rows.append((t - plan.start, winner, float(top_score)))
            t += step
    finally:
        cap.release()

    if not rows or not tracks:
        return [], tracks, sample_count

    # State machine: don't cut to every short interjection or noisy mouth movement.
    labels: list[int | None] = [None] * len(rows)
    current: int | None = None
    current_since = float(shot.start)
    pending: int | None = None
    pending_count = 0
    last_confirmed = -999.0

    for i, (rel_t, candidate, score) in enumerate(rows):
        if current is None:
            if candidate is None:
                pending = None
                pending_count = 0
                continue
            if candidate == pending:
                pending_count += 1
            else:
                pending = candidate
                pending_count = 1
            if pending_count >= 3:
                current = candidate
                current_since = max(float(shot.start), rel_t - step * (pending_count - 1))
                last_confirmed = rel_t
                for j in range(max(0, i - pending_count + 1), i + 1):
                    labels[j] = current
                pending = None
                pending_count = 0
            continue

        if candidate == current:
            labels[i] = current
            last_confirmed = rel_t
            pending = None
            pending_count = 0
            continue

        if candidate is None:
            # Hold through brief uncertainty instead of jumping to a random face.
            if rel_t - last_confirmed <= 1.15:
                labels[i] = current
            continue

        if candidate == pending:
            pending_count += 1
        else:
            pending = candidate
            pending_count = 1

        hold = rel_t - current_since
        very_strong = score >= 3.6
        needed = 2 if very_strong and hold >= 1.8 else 4
        if pending_count >= needed and (hold >= 2.7 or very_strong):
            switch_start = max(0, i - pending_count + 1)
            current = candidate
            current_since = rows[switch_start][0]
            last_confirmed = rel_t
            for j in range(switch_start, i + 1):
                labels[j] = current
            pending = None
            pending_count = 0
        else:
            # While a challenger is unproven, keep the verified speaker on screen.
            labels[i] = current

    groups: list[tuple[int, int, int]] = []
    i = 0
    while i < len(labels):
        label = labels[i]
        if label is None:
            i += 1
            continue
        j = i + 1
        while j < len(labels) and labels[j] == label:
            j += 1
        # Camera changes shorter than ~1.3 s are visually noisy and usually not worth it.
        if (j - i) * step >= 1.25:
            groups.append((i, j, int(label)))
        i = j

    spans: list[q94.SpeakerSpan] = []
    for start_i, end_i, label in groups:
        track = next((tr for tr in tracks if tr.track_id == label), None)
        if track is None:
            continue
        start_rel = max(float(shot.start), rows[start_i][0] - step * 0.48)
        end_rel = min(float(shot.end), rows[end_i - 1][0] + step * 0.62)
        if start_rel - float(shot.start) <= 1.35:
            start_rel = float(shot.start)
        if float(shot.end) - end_rel <= 0.85:
            end_rel = float(shot.end)
        if end_rel - start_rel < 1.20:
            continue
        relevant = [rows[k][2] for k in range(start_i, end_i) if rows[k][1] == label]
        confidence = float(np.median(relevant)) if relevant else 0.0
        spans.append(q94.SpeakerSpan(start_rel, end_rel, label, q94._track_box(track, height * 0.38), confidence))

    return spans, tracks, sample_count


def _relax_plan(plan: q92.Plan92) -> q92.Plan92:
    """Remove gratuitous micro punch-ins while preserving real scene/speaker changes."""
    shots: list[q92.Shot92] = []
    removed_punches = 0
    for shot in plan.shots:
        if shot.kind == "attention_punch" and shot.end - shot.start < 3.2:
            shot = q92.Shot92(shot.start, shot.end, "speaker", shot.primary, None, 1.0, shot.intensity)
            removed_punches += 1

        if (
            shots
            and shot.kind == shots[-1].kind
            and abs(shot.start - shots[-1].end) < 0.08
            and abs(shot.primary.x - shots[-1].primary.x) < plan.width * 0.07
            and ((shot.secondary is None) == (shots[-1].secondary is None))
        ):
            shots[-1].end = shot.end
        else:
            shots.append(shot)

    reason = plan.reason + f" · ritmo de câmera relaxado · {removed_punches} micro punch-in(s) neutralizado(s)"
    return q92.Plan92(plan.width, plan.height, shots, reason)


def analyze_v9_6(video: Path, plan, transcript_segments: list[dict]) -> q92.Plan92:
    result = _BASE_ANALYZE(video, plan, transcript_segments)
    return _relax_plan(result)


def _open_portrait_transform(width: int, height: int, focus: q92.FocusBox, zoom_hint: float, kind: str) -> str:
    ratio = 9 / 16
    if width / height < ratio:
        return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"

    if focus.h > 1:
        desired_face_fraction = 0.21 if kind in {"reaction_emphasis", "attention_punch"} else 0.18
        crop_h = max(height * 0.82, focus.h / desired_face_fraction)
    else:
        crop_h = height * 0.90
    # Ignore aggressive old zoom hints; v9.6 intentionally keeps more shoulders/context.
    crop_h = max(crop_h, height * max(0.84, min(0.96, zoom_hint)))
    crop_h = min(float(height), crop_h)
    crop_h = max(2, int(crop_h))
    crop_h -= crop_h % 2
    crop_w = max(2, int(crop_h * ratio))
    crop_w -= crop_w % 2
    if crop_w > width:
        crop_w = width - (width % 2)
        crop_h = max(2, int(crop_w / ratio))
        crop_h -= crop_h % 2

    x = int(max(0, min(width - crop_w, focus.x - crop_w / 2)))
    target_face_y = crop_h * 0.36
    y = int(max(0, min(height - crop_h, focus.y - target_face_y))) if focus.h > 1 else int(max(0, (height - crop_h) / 2))
    return f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:1920,setsar=1"


def _open_panel_transform(width: int, height: int, focus: q92.FocusBox) -> str:
    ratio = 1080 / 960
    if width / height < ratio:
        return "scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1"

    if focus.h > 1:
        crop_h = max(height * 0.66, focus.h / 0.27)
    else:
        crop_h = height * 0.76
    crop_h = min(float(height), crop_h)
    crop_h = max(2, int(crop_h))
    crop_h -= crop_h % 2
    crop_w = max(2, int(crop_h * ratio))
    crop_w -= crop_w % 2
    if crop_w > width:
        crop_w = width - (width % 2)
        crop_h = max(2, int(crop_w / ratio))
        crop_h -= crop_h % 2
    x = int(max(0, min(width - crop_w, focus.x - crop_w / 2)))
    y = int(max(0, min(height - crop_h, focus.y - crop_h * 0.39))) if focus.h > 1 else int(max(0, (height - crop_h) / 2))
    return f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:960,setsar=1"


def _open_strict_portrait(width: int, height: int, focus: q92.FocusBox, zoom_hint: float, kind: str) -> str:
    # Auto Review may correct centering, but should not turn the correction into a face close-up.
    return _open_portrait_transform(width, height, focus, max(0.90, zoom_hint), kind)


def _open_strict_panel(width: int, height: int, focus: q92.FocusBox) -> str:
    return _open_panel_transform(width, height, focus)


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_timeline = q94._speaker_timeline
    original_analyze = q94.analyze_v9_4
    original_portrait = q92._portrait_transform
    original_panel = q92._panel_transform
    original_strict_portrait = q93._strict_portrait_transform
    original_strict_panel = q93._strict_panel_transform
    try:
        q94._speaker_timeline = _speaker_timeline_v9_6
        q94.analyze_v9_4 = analyze_v9_6
        q92._portrait_transform = _open_portrait_transform
        q92._panel_transform = _open_panel_transform
        q93._strict_portrait_transform = _open_strict_portrait
        q93._strict_panel_transform = _open_strict_panel

        autoclip.log(
            "Quality v9.6: active-speaker conservador · troca sustentada · câmera mais calma · enquadramento mais aberto"
        )
        q95.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Camera Director v9.6\n")
        autoclip.summary(
            "Active Speaker: **mais conservador** — novo falante precisa persistir antes da troca; "
            "incerteza curta mantém o último falante confirmado. Camera pacing: **mais calmo**, com micro punch-ins neutralizados. "
            "Enquadramento: **mais aberto**, preservando mais cabeça/ombros/contexto e evitando close excessivo.\n"
        )
    finally:
        q94._speaker_timeline = original_timeline
        q94.analyze_v9_4 = original_analyze
        q92._portrait_transform = original_portrait
        q92._panel_transform = original_panel
        q93._strict_portrait_transform = original_strict_portrait
        q93._strict_panel_transform = original_strict_panel
