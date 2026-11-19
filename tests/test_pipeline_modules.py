"""Contract tests for ASR, acoustics and exports modules."""
from __future__ import annotations

import importlib.util
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from docx import Document

from audiobook_qc.config import load_runtime_config

ROOT = Path(__file__).resolve().parents[1]


def load_qc_module():
    spec = importlib.util.spec_from_file_location("run_qc_under_test", ROOT / "scripts" / "run_qc.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_transcribe_returns_segments_and_text(tmp_path: Path) -> None:
    qc = load_qc_module()
    audio = tmp_path / "speech.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16000, 16000, "NONE", "not compressed"))
        wav.writeframes((np.sin(np.linspace(0, 100, 16000)) * 32767).astype(np.int16).tobytes())

    segments, text = qc.transcribe(audio, model_name="tiny", language="ru")
    assert isinstance(segments, list)
    assert isinstance(text, list)
    assert all(segment.get("start") >= 0 for segment in segments)


def test_analyze_acoustics_uses_thresholds_and_returns_finite_metrics(tmp_path: Path) -> None:
    qc = load_qc_module()
    audio = tmp_path / "silence.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16000, 16000, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 16000)

    metrics = qc.analyze_acoustics(audio, clip_sample_amplitude=0.99)
    assert metrics["has_signal"] is False
    assert metrics["snr"] is None
    assert metrics["noise_assessment"] == "NOT_CALIBRATED"

    flags = qc.build_acoustic_flags(metrics, thresholds={"audio": {"lead_or_trail_silence_review_s": 2.0, "clip_sample_amplitude": 0.99}})
    assert [flag["category"] for flag in flags] == ["SILENT_AUDIO"]
    assert flags[0]["timestamp"] == 0.0
    assert flags[0]["timestamp_end"] == 1.0


def test_acoustic_flags_respect_thresholds() -> None:
    qc = load_qc_module()
    metrics = {
        "has_signal": True,
        "n_clipped": 0,
        "frac_clipped": 0.0,
        "lead_silence": 0.0,
        "trail_silence": 0.0,
        "duration": 1.0,
        "silence_regions": [],
    }

    flags = qc.build_acoustic_flags(
        metrics,
        thresholds={"audio": {"lead_or_trail_silence_review_s": 2.0, "clip_sample_amplitude": 0.9}},
    )
    assert flags == []


def test_monotony_classification_uses_runtime_thresholds() -> None:
    from audiobook_qc.prosody import is_monotonous

    strict = SimpleNamespace(monotony_range_semitones=10.0, monotony_min_voiced_duration_s=30.0)
    permissive = SimpleNamespace(monotony_range_semitones=5.0, monotony_min_voiced_duration_s=60.0)

    assert is_monotonous(9.0, 31.0, strict)
    assert not is_monotonous(9.0, 31.0, permissive)


def test_acoustic_flags_accept_runtime_config(tmp_path: Path) -> None:
    qc = load_qc_module()
    audio = tmp_path / "silence.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16000, 16000, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 16000)

    runtime = load_runtime_config(ROOT / "configs" / "thresholds.yaml", ROOT / "configs" / "policy.yaml")
    flags = qc.build_acoustic_flags(qc.analyze_acoustics(audio), runtime)
    assert [flag["category"] for flag in flags] == ["SILENT_AUDIO"]


def test_export_paths_are_separate_for_triage_and_confirmed(tmp_path: Path) -> None:
    qc = load_qc_module()
    flags = [
        {
            "id": "flag_001", "timestamp": 10.0, "timestamp_end": 11.0,
            "severity": "CRITICAL", "category": "WORD_SUBSTITUTION",
            "description": "Первый кандидат", "action": "Перечитать первую фразу",
        },
        {
            "id": "flag_002", "timestamp": 20.0, "timestamp_end": 21.0,
            "severity": "REVIEW", "category": "NOISE",
            "description": "Второй кандидат", "action": "Проверить шум",
        },
    ]

    triage_csv = qc.write_marker_csv(flags, tmp_path, "chapter", "triage_markers")
    confirmed_csv = qc.write_marker_csv(flags[:1], tmp_path, "chapter", "audition_markers")
    triage_docx = qc.write_triage_docx(flags, tmp_path, "chapter", "speech.wav")
    confirmed_docx = qc.write_confirmed_docx(flags[:1], tmp_path, "chapter", "speech.wav")

    assert triage_csv.name == "triage_markers_chapter.csv"
    assert confirmed_csv.name == "audition_markers_chapter.csv"
    assert triage_docx.name == "triage_chapter.docx"
    assert confirmed_docx.name == "перепись_chapter.docx"
    assert "flag_001" in triage_csv.read_text(encoding="utf-8")
    assert "flag_002" in triage_csv.read_text(encoding="utf-8")
    assert "flag_002" not in confirmed_csv.read_text(encoding="utf-8")
    assert triage_docx.exists() and triage_docx.name == "triage_chapter.docx"
    assert confirmed_docx.exists() and confirmed_docx.name == "перепись_chapter.docx"

    triage_doc = Document(triage_docx)
    triage_text = "\n".join(p.text for p in triage_doc.paragraphs)
    assert "не направлять диктору" in triage_text.casefold()


def test_pdf_report_survives_alignment_failure(tmp_path: Path) -> None:
    qc = load_qc_module()
    report = {
        "status": "PRELIMINARY_TRIAGE_REQUIRED",
        "audio_name": "speech.wav",
        "duration": 1.0,
        "tag": "chapter",
        "matched_pct": None,
        "alignment": {"status": "ALIGNMENT_FAILED", "reason": "No anchors found."},
        "acoustics": {
            "source_sample_rate": 16000, "source_channels": 1,
            "analysis_sample_rate": 16000, "sr": 16000, "duration": 1.0,
            "peak": 0.0, "n_clipped": 0, "frac_clipped": 0.0,
            "rms_all": 0.0, "rms_min": 0.0, "rms_max": 0.0,
            "snr": None, "noise_to_signal": None, "noise_assessment": "NOT_CALIBRATED",
            "amplitude_percentile_ratio_db": None, "has_signal": False,
            "silence_count": 1, "silence_regions": [{"start": 0.0, "end": 1.0, "duration": 1.0}],
            "lead_silence": 1.0, "trail_silence": 1.0,
        },
        "prosody": {
            "status": "NOT_EVALUATED", "pitch_median": 0, "pitch_min": 0, "pitch_max": 0,
            "pitch_range_semitones": 0, "pitch_std": 0, "voiced_pct": 0,
            "monotony_flag": None,
        },
        "n_flags": 1, "n_critical": 0, "n_review": 1, "n_artifact": 0,
        "n_words": 0, "n_seg": 0,
        "flags": [{"id": "alignment_0001", "severity": "REVIEW", "category": "ALIGNMENT_FAILED",
                   "timestamp": 0.0, "timestamp_end": 0.0, "description": "failed", "action": "retry"}],
        "verdict_accuracy": "△ выравнивание не удалось",
        "verdict_noise": "✗ нет полезного сигнала",
        "verdict_clip": "✓ нет",
        "verdict_pause": "✓ норма",
        "verdict_intonation": "не оценено",
    }
    path = qc.write_pdf(report, tmp_path, "chapter")
    assert path.exists()
    assert path.suffix == ".pdf"
