from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import autoclip
import quality_v4 as q4
import quality_v6 as q6
import quality_v9_2 as q92
import quality_v9_3 as q93


_BASE_ANALYZER = q92.analyze_v9_2


@dataclass
class FaceTrack:
    track_id: int
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    last_center: float = 0.0
    last_width: float = 0.0
    wins: int = 0


@dataclass
class SpeakerSample:
    rel_time: float
    track_id: int | None
    score: float


@dataclass
class SpeakerSpan:
    start: float
    end: float
    track_id: int
    box: q92.FocusBox
    confidence: float


def _mouth_speech_score(frame_a, frame_b, face) -> float:
    """Prefer mouth-specific motion over general head/body motion."""
    import cv2

    x, y, w, h = [int(v) for v in face]
    gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
    fh, fw = gray_a.shape[:2]
    x1, x2 = max(0, x), min(fw, x + w)
    mouth_y1 = max(0, y + int(h * 0.50))
    mouth_y2 = min(fh, y + int(h * 0.92))
    upper_y1 = max(0, y + int(h * 0.10))
    upper_y2 = min(fh, y + int(h * 0.46))
    if x2 <= x1 or mouth_y2 <= mouth_y1 or upper_y2 <= upper_y1:
        return 0.0

    mouth_a = gray_a[mouth_y1:mouth_y2, x1:x2]
    mouth_b = gray_b[mouth_y1:mouth_y2, x1:x2]
    upper_a = gray_a[upper_y1:upper_y2, x1:x2]
    upper_b = gray_b[upper_y1:upper_y2, x1:x2]
    if mouth_a.size == 0 or upper_a.size == 0:
        return 0.0

    mouth = float(np.mean(cv2.absdiff(mouth_a, mouth_b)))
    upper = float(np.mean(cv2.absdiff(upper_a, upper_b)))
    specific = max(0.0, mouth - 0.68 * upper)
    ratio = mouth / max(1.5, upper)
    return specific * (0.75 + 0.25 * min(2.0, ratio))


def _assign_tracks(
    tracks: list[FaceTrack],
    faces: list[tuple[int, int, int, int]],
    time_value: float,
    frame_width: int,
) -> list[tuple[FaceTrack, tuple[int, int, int, int]]]:
    assigned: list[tuple[FaceTrack, tuple[int, int, int, int]]] = []
    used: set[int] = set()

    for face in sorted(faces, key=lambda f: f[2] * f[3], reverse=True):
        x, y, w, h = face
        center = float(x + w / 2)
        best = None
        best_cost = 1e9
        for track in tracks:
            if track.track_id in used or not track.boxes:
                continue
            size_ratio = w / max(1.0, track.last_width)
            if not 0.45 <= size_ratio <= 2.2:
                continue
            distance = abs(center - track.last_center)
            max_distance = max(frame_width * 0.12, w * 1.25, track.last_width * 1.25)
            if distance > max_distance:
                continue
            cost = distance / max(1.0, max_distance) + abs(np.log(max(0.05, size_ratio))) * 0.25
            if cost < best_cost:
                best_cost = cost
                best = track

        if best is None:
            best = FaceTrack(len(tracks))
            tracks.append(best)
        best.boxes.append(face)
        best.times.append(time_value)
        best.last_center = center
        best.last_width = float(w)
        used.add(best.track_id)
        assigned.append((best, face))
    return assigned


def _track_box(track: FaceTrack, fallback_y: float) -> q92.FocusBox:
    if not track.boxes:
        return q92.FocusBox(track.last_center, fallback_y, 0.0, 0.0, 0.0)
    return q92._box_from_faces(track.boxes, track.last_center, fallback_y)


def _speaker_timeline(
    video: Path,
    plan,
    shot,
    transcript_segments: list[dict],
    audio_samples: np.ndarray,
    audio_rate: int,
) -> tuple[list[SpeakerSpan], list[FaceTrack], int]:
    import cv2

    abs_start = plan.start + float(shot.start)
    abs_end = plan.start + float(shot.end)
    duration = max(0.1, abs_end - abs_start)
    if duration < 0.65:
        return [], [], 0

    frontal, profile = q6._cascades()
    cap = cv2.VideoCapture(str(video))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tracks: list[FaceTrack] = []
    samples: list[SpeakerSample] = []
    sample_count = 0

    # More frequent sampling is important when 3+ people are visible.
    step = 0.30 if duration <= 12 else 0.38
    t = abs_start + min(0.18, duration * 0.08)
    try:
        while t <= abs_end - 0.10:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_a, a = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + 0.115) * 1000)
            ok_b, b = cap.read()
            if not ok_a or not ok_b:
                t += step
                continue

            faces = q4._detect_faces(a, frontal, profile)
            assigned = _assign_tracks(tracks, faces, t, width)
            sample_count += 1
            speech_active = q6._speech_active(transcript_segments, t)
            rel_audio = max(0.0, t - plan.start)
            energy = q6._energy(audio_samples, audio_rate, rel_audio) if audio_samples.size > 1 else (1.0 if speech_active else 0.0)
            gate = (0.45 + min(1.35, energy)) if speech_active else 0.16

            scored: list[tuple[float, FaceTrack]] = []
            for track, face in assigned:
                raw = _mouth_speech_score(a, b, face)
                area = float(face[2] * face[3]) / max(1.0, a.shape[0] * a.shape[1])
                # Face size is only a tiny stabilizer; it must never dominate speaking evidence.
                score = raw * gate + min(0.65, area * 10.0)
                track.scores.append(score)
                scored.append((score, track))

            if scored and speech_active:
                scored.sort(key=lambda item: item[0], reverse=True)
                top_score, top_track = scored[0]
                second_score = scored[1][0] if len(scored) > 1 else 0.0
                margin = top_score - second_score
                # Require actual mouth evidence; otherwise keep the sample undecided.
                if top_score >= 1.05 and (margin >= 0.18 or top_score >= second_score * 1.16):
                    top_track.wins += 1
                    samples.append(SpeakerSample(t - plan.start, top_track.track_id, top_score))
                else:
                    samples.append(SpeakerSample(t - plan.start, None, top_score))
            else:
                samples.append(SpeakerSample(t - plan.start, None, 0.0))
            t += step
    finally:
        cap.release()

    if not samples or not tracks:
        return [], tracks, sample_count

    # Smooth isolated mistakes. A speaker change must persist across neighboring samples.
    labels = [s.track_id for s in samples]
    smoothed = labels[:]
    for i in range(len(labels)):
        window = [labels[j] for j in range(max(0, i - 1), min(len(labels), i + 2)) if labels[j] is not None]
        if not window:
            continue
        counts: dict[int, int] = {}
        for label in window:
            counts[label] = counts.get(label, 0) + 1
        winner, votes = max(counts.items(), key=lambda item: item[1])
        if votes >= 2 or labels[i] is None:
            smoothed[i] = winner

    # Fill only single undecided gaps between the same speaker; do not blindly lock forever.
    for i in range(1, len(smoothed) - 1):
        if smoothed[i] is None and smoothed[i - 1] is not None and smoothed[i - 1] == smoothed[i + 1]:
            smoothed[i] = smoothed[i - 1]

    # Build sustained speaker spans with boundaries halfway between samples.
    groups: list[tuple[int, int, int]] = []
    i = 0
    while i < len(smoothed):
        label = smoothed[i]
        if label is None:
            i += 1
            continue
        j = i + 1
        while j < len(smoothed) and smoothed[j] == label:
            j += 1
        if j - i >= 2 or (j - i == 1 and samples[i].score >= 3.0):
            groups.append((i, j, int(label)))
        i = j

    spans: list[SpeakerSpan] = []
    for gi, (start_i, end_i, label) in enumerate(groups):
        first_t = samples[start_i].rel_time
        last_t = samples[end_i - 1].rel_time
        start_rel = max(float(shot.start), first_t - step * 0.52)
        end_rel = min(float(shot.end), last_t + step * 0.58)
        if end_rel - start_rel < 0.45:
            continue
        track = next((tr for tr in tracks if tr.track_id == label), None)
        if track is None:
            continue
        relevant_scores = [samples[k].score for k in range(start_i, end_i) if samples[k].track_id == label]
        confidence = float(np.median(relevant_scores)) if relevant_scores else 0.0
        spans.append(SpeakerSpan(start_rel, end_rel, label, _track_box(track, height * 0.38), confidence))

    # If one track decisively dominates the entire spoken shot, allow it to own undecided gaps.
    if spans:
        wins = sorted(((tr.wins, tr.track_id) for tr in tracks), reverse=True)
        if wins and wins[0][0] >= max(3, int(sample_count * 0.38)):
            dominant_id = wins[0][1]
            total_win = sum(w for w, _ in wins) or 1
            if wins[0][0] / total_win >= 0.62:
                track = next(tr for tr in tracks if tr.track_id == dominant_id)
                dominant_box = _track_box(track, height * 0.38)
                # Do not erase real speaker changes; only expand if every detected span is the same person.
                if all(span.track_id == dominant_id for span in spans):
                    spans = [SpeakerSpan(float(shot.start), float(shot.end), dominant_id, dominant_box, spans[0].confidence)]

    return spans, tracks, sample_count


def _distinct_box(a: q92.FocusBox, b: q92.FocusBox, width: int) -> bool:
    if a.w <= 1 or b.w <= 1:
        return abs(a.x - b.x) >= width * 0.14
    return abs(a.x - b.x) >= max(width * 0.10, (a.w + b.w) * 0.58)


def _best_listener(tracks: list[FaceTrack], speaker_id: int, speaker_box: q92.FocusBox, width: int, height: int) -> q92.FocusBox | None:
    candidates = []
    for track in tracks:
        if track.track_id == speaker_id or len(track.boxes) < 2:
            continue
        box = _track_box(track, height * 0.38)
        if not _distinct_box(speaker_box, box, width):
            continue
        persistence = len(track.boxes)
        activity = float(np.median(track.scores)) if track.scores else 0.0
        candidates.append((persistence + min(3.0, activity * 0.25), box))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _apply_speaker_spans(
    shot: q92.Shot92,
    spans: list[SpeakerSpan],
    tracks: list[FaceTrack],
    width: int,
    height: int,
) -> list[q92.Shot92]:
    if not spans:
        return [shot]

    out: list[q92.Shot92] = []
    cursor = float(shot.start)
    for span in spans:
        s = max(float(shot.start), span.start)
        e = min(float(shot.end), span.end)
        if e - s < 0.35:
            continue
        if s > cursor + 0.25:
            out.append(q92.Shot92(cursor, s, shot.kind, shot.primary, shot.secondary, shot.zoom, shot.intensity))

        if shot.kind == "split":
            listener = _best_listener(tracks, span.track_id, span.box, width, height)
            if listener is not None:
                out.append(q92.Shot92(s, e, "split", span.box, listener, 1.0, max(shot.intensity, span.confidence)))
            else:
                # If we cannot prove two distinct people, prefer the actual speaker alone.
                out.append(q92.Shot92(s, e, "speaker", span.box, None, shot.zoom, span.confidence))
        else:
            out.append(q92.Shot92(s, e, shot.kind, span.box, None, shot.zoom, span.confidence))
        cursor = e

    if cursor < float(shot.end) - 0.25:
        out.append(q92.Shot92(cursor, float(shot.end), shot.kind, shot.primary, shot.secondary, shot.zoom, shot.intensity))
    return out or [shot]


def analyze_v9_4(video: Path, plan, transcript_segments: list[dict]) -> q92.Plan92:
    base = _BASE_ANALYZER(video, plan, transcript_segments)
    if q6.CURRENT_PROFILE not in {"podcast", "talking"}:
        return base

    wav = autoclip.WORK / f"active_speaker_{int(plan.start * 1000)}.wav"
    try:
        try:
            audio_samples, audio_rate = q6._extract_audio(video, plan.start, plan.end, wav)
        except Exception as exc:
            autoclip.log(f"Aviso v9.4: energia de áudio indisponível para active-speaker: {exc}")
            audio_samples, audio_rate = np.zeros(1, dtype=np.float32), 16000

        refined: list[q92.Shot92] = []
        locked_seconds = 0.0
        speaker_changes = 0
        previous_speaker_x: float | None = None
        multi_person_shots = 0

        for shot in base.shots:
            spans, tracks, sample_count = _speaker_timeline(
                video, plan, shot, transcript_segments, audio_samples, audio_rate
            )
            if len([t for t in tracks if len(t.boxes) >= 2]) >= 3:
                multi_person_shots += 1

            if spans:
                for span in spans:
                    locked_seconds += max(0.0, span.end - span.start)
                    if previous_speaker_x is not None and abs(span.box.x - previous_speaker_x) > base.width * 0.12:
                        speaker_changes += 1
                    previous_speaker_x = span.box.x
                refined.extend(_apply_speaker_spans(shot, spans, tracks, base.width, base.height))
            else:
                refined.append(shot)

        # Merge adjacent pieces that lock onto the same speaker and have identical mode.
        merged: list[q92.Shot92] = []
        for shot in refined:
            if (
                merged
                and shot.kind == merged[-1].kind
                and abs(shot.start - merged[-1].end) < 0.07
                and abs(shot.primary.x - merged[-1].primary.x) < base.width * 0.055
                and ((shot.secondary is None) == (merged[-1].secondary is None))
            ):
                merged[-1].end = shot.end
            else:
                merged.append(shot)

        reason = (
            base.reason
            + f" · active-speaker lock {locked_seconds:.1f}s"
            + f" · {speaker_changes} troca(s) sustentada(s) de falante"
            + f" · {multi_person_shots} plano(s) com 3+ pessoas"
        )
        return q92.Plan92(base.width, base.height, merged, reason)
    finally:
        wav.unlink(missing_ok=True)


def run(url: str, clips_count: int, min_seconds: int, max_seconds: int, whisper_model: str) -> None:
    original_analyzer = q92.analyze_v9_2
    try:
        q92.analyze_v9_2 = analyze_v9_4
        autoclip.log(
            "Quality v9.4: active-speaker lock · prioridade para movimento labial durante fala · hysteresis de troca"
        )
        q93.run(url, clips_count, min_seconds, max_seconds, whisper_model)
        autoclip.summary("\n### Active Speaker v9.4\n")
        autoclip.summary(
            "Em Podcast/Entrevista e Talking Head, o foco agora prioriza **quem está falando**: "
            "Whisper/áudio validam que há fala, movimento específico da boca escolhe a pessoa e uma troca só é aceita "
            "quando persiste por vários frames. Em planos com muitas pessoas, tamanho/posição do rosto deixam de dominar a decisão.\n"
        )
    finally:
        q92.analyze_v9_2 = original_analyzer
