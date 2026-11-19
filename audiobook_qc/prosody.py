"""Conservative, diagnostic-only prosody analysis."""
from __future__ import annotations

from typing import Any

import librosa
import numpy as np

_SAMPLE_RATE = 16_000
_HOP_LENGTH = 512
_FRAME_LENGTH = 2048
_CHUNK_SECONDS = 300


def _longest_true_run(values: np.ndarray) -> int:
    longest = current = 0
    for value in values.astype(bool):
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def summarize_prosody(
    f0: np.ndarray,
    voiced_flag: np.ndarray,
    *,
    sample_rate: int = _SAMPLE_RATE,
    hop_length: int = _HOP_LENGTH,
    thresholds: Any | None = None,
) -> dict[str, Any]:
    """Summarize F0 robustly; no result certifies expressive performance."""
    f0 = np.asarray(f0, dtype=float)
    voiced_flag = np.asarray(voiced_flag, dtype=bool)
    valid = voiced_flag & np.isfinite(f0) & (f0 > 0)
    longest_s = _longest_true_run(valid) * hop_length / sample_rate
    if not np.any(valid):
        return {
            "status": "NOT_EVALUATED", "pitch_median": 0, "pitch_min": 0,
            "pitch_max": 0, "pitch_range_semitones": 0, "pitch_std": 0,
            "voiced_pct": 0, "longest_voiced_duration_s": round(longest_s, 3),
            "monotony_flag": None,
        }

    voiced = f0[valid]
    low, high = np.percentile(voiced, [5, 95])
    pitch_range = float(12 * np.log2(high / max(low, 1e-9)))
    monotony = None
    status = "DIAGNOSTIC_ONLY"
    if thresholds is not None:
        minimum = float(thresholds.monotony_min_voiced_duration_s)
        if longest_s <= minimum:
            status = "INSUFFICIENT_EVIDENCE"
        else:
            monotony = bool(pitch_range < float(thresholds.monotony_range_semitones))
            status = "REVIEW_CANDIDATE" if monotony else "DIAGNOSTIC_ONLY"
    return {
        "status": status,
        "pitch_median": round(float(np.median(voiced))),
        "pitch_min": round(float(low)),
        "pitch_max": round(float(high)),
        "pitch_range_semitones": round(pitch_range, 2),
        "pitch_std": round(float(np.std(voiced)), 2),
        "voiced_pct": round(100 * float(np.mean(valid)), 1),
        "longest_voiced_duration_s": round(longest_s, 3),
        "monotony_flag": monotony,
    }


def is_monotonous(pitch_range_semitones: float, voiced_duration_s: float, thresholds: Any) -> bool:
    """Compatibility helper for the configured candidate rule."""
    return bool(
        pitch_range_semitones < thresholds.monotony_range_semitones
        and voiced_duration_s > thresholds.monotony_min_voiced_duration_s
    )


def analyze_prosody(audio_path: str, thresholds: Any) -> dict[str, Any]:
    """Run pYIN in bounded chunks and retain one series for reporting/plotting."""
    y, sr = librosa.load(audio_path, sr=_SAMPLE_RATE, mono=True)
    chunk_samples = _CHUNK_SECONDS * sr
    f0_parts: list[np.ndarray] = []
    voiced_parts: list[np.ndarray] = []
    time_parts: list[np.ndarray] = []
    rms_parts: list[np.ndarray] = []
    rms_time_parts: list[np.ndarray] = []

    for start in range(0, max(len(y), 1), chunk_samples):
        chunk = y[start:start + chunk_samples]
        if not len(chunk):
            continue
        f0, voiced, _ = librosa.pyin(
            chunk, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"),
            sr=sr, frame_length=_FRAME_LENGTH, hop_length=_HOP_LENGTH,
        )
        offset = start / sr
        f0_parts.append(np.asarray(f0, dtype=float))
        voiced_parts.append(np.asarray(voiced, dtype=bool))
        time_parts.append(librosa.times_like(f0, sr=sr, hop_length=_HOP_LENGTH) + offset)
        rms = librosa.feature.rms(y=chunk, frame_length=_FRAME_LENGTH, hop_length=_HOP_LENGTH)[0]
        rms_parts.append(rms)
        rms_time_parts.append(librosa.times_like(rms, sr=sr, hop_length=_HOP_LENGTH) + offset)

    f0_all = np.concatenate(f0_parts) if f0_parts else np.array([], dtype=float)
    voiced_all = np.concatenate(voiced_parts) if voiced_parts else np.array([], dtype=bool)
    summary = summarize_prosody(f0_all, voiced_all, sample_rate=sr, hop_length=_HOP_LENGTH, thresholds=thresholds)
    summary.update({
        "_f0": f0_all,
        "_f0_times": np.concatenate(time_parts) if time_parts else np.array([], dtype=float),
        "_rms": np.concatenate(rms_parts) if rms_parts else np.array([], dtype=float),
        "_rms_times": np.concatenate(rms_time_parts) if rms_time_parts else np.array([], dtype=float),
    })
    return summary
