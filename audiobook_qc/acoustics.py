"""Acoustic analysis with native-format checks and conservative diagnostics."""
from __future__ import annotations

from typing import Any

import librosa
import numpy as np

from audiobook_qc.config import ConfigError

_ANALYSIS_SAMPLE_RATE = 16_000
_RMS_WINDOW_SECONDS = 0.5
_RMS_SILENCE_THRESHOLD = 0.005


def _load_native_and_analysis(audio_path: str) -> tuple[np.ndarray, int, int, np.ndarray]:
    native, source_sr = librosa.load(audio_path, sr=None, mono=False)
    native = np.asarray(native, dtype=np.float32)
    if native.ndim == 1:
        channels = 1
        mono = native
    else:
        channels = int(native.shape[0])
        mono = np.mean(native, axis=0, dtype=np.float32)
    if source_sr != _ANALYSIS_SAMPLE_RATE and len(mono):
        mono = librosa.resample(mono, orig_sr=source_sr, target_sr=_ANALYSIS_SAMPLE_RATE)
    return native, int(source_sr), channels, np.asarray(mono, dtype=np.float32)


def _rms_windows(y: np.ndarray, sr: int) -> tuple[np.ndarray, list[dict[str, float]]]:
    size = max(1, int(sr * _RMS_WINDOW_SECONDS))
    rms = np.array([
        float(np.sqrt(np.mean(y[start:start + size] ** 2)))
        for start in range(0, len(y), size)
        if len(y[start:start + size])
    ])
    silent = rms < _RMS_SILENCE_THRESHOLD
    regions: list[dict[str, float]] = []
    region_start: int | None = None
    for index, is_silent in enumerate(np.r_[silent, False]):
        if is_silent and region_start is None:
            region_start = index
        elif not is_silent and region_start is not None:
            start_s = region_start * size / sr
            end_s = min(index * size, len(y)) / sr
            regions.append({
                "start": round(float(start_s), 3),
                "end": round(float(end_s), 3),
                "duration": round(float(end_s - start_s), 3),
            })
            region_start = None
    return rms, regions


def analyze_acoustics(audio_path: str, clip_sample_amplitude: float = 0.99) -> dict[str, Any]:
    """Check native clipping and expose uncalibrated analysis diagnostics."""
    native, source_sr, source_channels, y = _load_native_and_analysis(audio_path)
    native_abs = np.abs(native)
    peak = float(native_abs.max()) if native_abs.size else 0.0
    n_clip = int(np.sum(native_abs > clip_sample_amplitude))
    frac_clip = n_clip / int(native_abs.size) if native_abs.size else 0.0
    rms_all = float(np.sqrt(np.mean(y**2))) if len(y) else 0.0
    rms_series, silence_regions = _rms_windows(y, _ANALYSIS_SAMPLE_RATE)

    analysis_abs = np.abs(y)
    p10 = float(np.percentile(analysis_abs, 10)) if analysis_abs.size else 0.0
    p95 = float(np.percentile(analysis_abs, 95)) if analysis_abs.size else 0.0
    percentile_ratio = float(20 * np.log10(p95 / (p10 + 1e-9))) if p95 > 1e-7 else None
    has_signal = bool(peak > 1e-7 and rms_all > 1e-8)

    silence_threshold = max(1e-7, 0.01 * float(np.max(analysis_abs) if analysis_abs.size else 0.0))
    above = np.where(analysis_abs > silence_threshold)[0]
    duration = float(len(y) / _ANALYSIS_SAMPLE_RATE)
    lead = float(above[0] / _ANALYSIS_SAMPLE_RATE) if len(above) else duration
    trail = float((len(y) - 1 - above[-1]) / _ANALYSIS_SAMPLE_RATE) if len(above) else duration

    return {
        "source_sample_rate": source_sr,
        "source_channels": source_channels,
        "analysis_sample_rate": _ANALYSIS_SAMPLE_RATE,
        "sr": _ANALYSIS_SAMPLE_RATE,  # Compatibility alias; explicitly analysis-only.
        "duration": duration,
        "peak": peak,
        "clip_sample_amplitude": float(clip_sample_amplitude),
        "n_clipped": n_clip,
        "frac_clipped": frac_clip,
        "rms_all": rms_all,
        "rms_min": float(rms_series.min()) if len(rms_series) else 0.0,
        "rms_max": float(rms_series.max()) if len(rms_series) else 0.0,
        "has_signal": has_signal,
        "lead_silence": lead,
        "trail_silence": trail,
        "silence_count": len(silence_regions),
        "silence_regions": silence_regions,
        "amplitude_p10": p10,
        "amplitude_p95": p95,
        "amplitude_percentile_ratio_db": percentile_ratio,
        "noise_assessment": "NOT_CALIBRATED",
        "snr": None,
        "noise_to_signal": None,
    }


def build_acoustic_flags(acoustics: dict[str, Any], thresholds: Any, base_id: int = 5000) -> list[dict[str, Any]]:
    """Build only evidence-backed acoustic candidates from validated thresholds."""
    flags: list[dict[str, Any]] = []
    fid = base_id
    audio_thresholds = _resolve_audio_thresholds(thresholds)

    if not acoustics["has_signal"]:
        flags.append({
            "id": f"ac_{fid:04d}", "timestamp": 0.0,
            "timestamp_end": round(float(acoustics["duration"]), 2),
            "severity": "CRITICAL", "category": "SILENT_AUDIO",
            "description": "В аудиофайле не обнаружен пригодный для проверки сигнал.",
            "audio_heard": "", "book_reference": "", "asr_confidence": 0,
            "action": "Проверить исходный файл и экспорт записи, затем загрузить аудио повторно.",
        })
        fid += 1
    if acoustics["n_clipped"] > 0:
        flags.append({
            "id": f"ac_{fid:04d}", "timestamp": 0.0,
            "timestamp_end": round(float(acoustics["duration"]), 2),
            "severity": "CRITICAL", "category": "CLIPPING",
            "description": f"Пики выше порога в исходных каналах: {acoustics['n_clipped']} сэмплов ({acoustics['frac_clipped']*100:.4f}%)",
            "audio_heard": "", "book_reference": "", "asr_confidence": 0,
            "action": "Проверить перегруз исходной записи по каналам.",
        })
        fid += 1

    edge_limit = float(audio_thresholds["lead_or_trail_silence_review_s"])
    if acoustics["lead_silence"] > edge_limit or acoustics["trail_silence"] > edge_limit:
        flags.append({
            "id": f"ac_{fid:04d}", "timestamp": 0.0,
            "timestamp_end": round(float(acoustics["duration"]), 2),
            "severity": "REVIEW", "category": "ANOMALOUS_PAUSE",
            "description": f"Краевая тишина: начало={acoustics['lead_silence']:.1f}с, конец={acoustics['trail_silence']:.1f}с",
            "audio_heard": "", "book_reference": "", "asr_confidence": 0,
            "action": "Проверить границы и при необходимости обрезать файл.",
        })
        fid += 1

    duration = float(acoustics["duration"])
    for region in acoustics.get("silence_regions", []):
        if region["duration"] < edge_limit or region["start"] <= 0 or region["end"] >= duration:
            continue
        flags.append({
            "id": f"ac_{fid:04d}", "timestamp": region["start"], "timestamp_end": region["end"],
            "severity": "REVIEW", "category": "ANOMALOUS_PAUSE",
            "description": f"Внутренний тихий интервал длительностью {region['duration']:.1f}с",
            "audio_heard": "", "book_reference": "", "asr_confidence": 0,
            "action": "Проверить паузу на слух; это диагностический кандидат.",
        })
        fid += 1

    return flags


def _resolve_audio_thresholds(thresholds: Any) -> dict[str, Any]:
    """Accept validated Thresholds, RuntimeConfig, or a test-friendly mapping."""
    audio = getattr(thresholds, "audio", None)
    if audio is None:
        audio = getattr(getattr(thresholds, "thresholds", None), "audio", None)
    if audio is None and isinstance(thresholds, dict):
        root = thresholds.get("thresholds", thresholds)
        audio = root.get("audio") if isinstance(root, dict) else None
    if audio is None:
        raise ConfigError("thresholds.audio is required for acoustic flag classification")
    if not isinstance(audio, dict):
        audio = {
            "clip_sample_amplitude": float(audio.clip_sample_amplitude),
            "lead_or_trail_silence_review_s": float(audio.lead_or_trail_silence_review_s),
        }
    return audio
