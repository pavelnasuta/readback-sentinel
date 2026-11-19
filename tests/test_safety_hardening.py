"""Safety regression tests for the v0.2.0 hardening release."""
from __future__ import annotations

import importlib.util
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_qc.py"


def load_qc_module():
    spec = importlib.util.spec_from_file_location("run_qc_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_text_reads_docx(tmp_path: Path) -> None:
    qc = load_qc_module()
    source = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("Первая фраза.")
    doc.add_paragraph("Вторая фраза.")
    doc.save(source)

    assert qc.extract_text(source) == "Первая фраза.\nВторая фраза."


def test_unmatched_fragment_has_no_fallback_to_book_start() -> None:
    qc = load_qc_module()
    result = qc.locate_fragment(
        printed_tokens=["книга", "начинается", "совсем", "иначе"],
        rec_tokens=["аудио", "не", "совпадает", "с", "книгой"],
    )
    assert result is None


def test_confirmed_flag_file_rejects_unknown_ids(tmp_path: Path) -> None:
    qc = load_qc_module()
    confirmation = tmp_path / "confirmed.json"
    confirmation.write_text(
        json.dumps({"review_binding": "binding", "confirmed_flag_ids": ["flag_001", "unknown"]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown"):
        qc.load_confirmed_flag_ids(confirmation, {"flag_001"}, "binding")


def test_confirmed_flag_file_returns_only_explicit_candidates(tmp_path: Path) -> None:
    qc = load_qc_module()
    confirmation = tmp_path / "confirmed.json"
    confirmation.write_text(
        json.dumps({"review_binding": "binding", "confirmed_flag_ids": ["flag_002"]}),
        encoding="utf-8",
    )

    assert qc.load_confirmed_flag_ids(
        confirmation,
        {"flag_001", "flag_002"},
        "binding",
    ) == {"flag_002"}


def test_numpy_boolean_stays_a_json_boolean() -> None:
    qc = load_qc_module()
    payload = json.loads(json.dumps({"monotony_flag": np.bool_(False)}, default=qc.json_default))
    assert payload == {"monotony_flag": False}


def test_triage_and_confirmed_marker_exports_are_separate(tmp_path: Path) -> None:
    qc = load_qc_module()
    flags = [
        {
            "id": "flag_001",
            "timestamp": 12.5,
            "timestamp_end": 13.0,
            "severity": "CRITICAL",
            "category": "WORD_SUBSTITUTION",
            "description": "Первый кандидат",
            "action": "Перечитать первую фразу",
        },
        {
            "id": "flag_002",
            "timestamp": 22.5,
            "timestamp_end": 23.0,
            "severity": "REVIEW",
            "category": "NOISE",
            "description": "Второй кандидат",
            "action": "Проверить шум",
        },
    ]

    triage_path = qc.write_marker_csv(flags, tmp_path, "chapter", "triage_markers")
    confirmed_path = qc.write_marker_csv(flags[:1], tmp_path, "chapter", "audition_markers")

    assert triage_path.name == "triage_markers_chapter.csv"
    assert confirmed_path.name == "audition_markers_chapter.csv"
    assert "flag_001" in triage_path.read_text(encoding="utf-8")
    assert "flag_002" in triage_path.read_text(encoding="utf-8")
    assert "flag_001" in confirmed_path.read_text(encoding="utf-8")
    assert "flag_002" not in confirmed_path.read_text(encoding="utf-8")


def test_silent_audio_has_finite_acoustic_metrics(tmp_path: Path) -> None:
    qc = load_qc_module()
    audio = tmp_path / "silence.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16_000, 16_000, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 16_000)

    metrics = qc.analyze_acoustics(audio)

    assert metrics["has_signal"] is False
    assert metrics["snr"] is None
    assert metrics["noise_to_signal"] is None
    assert metrics["noise_assessment"] == "NOT_CALIBRATED"
