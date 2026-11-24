"""Focused contracts for safe standard CUE listening-marker exports."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from audiobook_qc.exports import write_cue

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_qc.py"


def _flag(
    identifier: str,
    timestamp: float,
    *,
    severity: str = "REVIEW",
    category: str = "WORD_SUBSTITUTION",
    description: str = "private transcript and book content",
) -> dict[str, object]:
    return {
        "id": identifier,
        "timestamp": timestamp,
        "severity": severity,
        "category": category,
        "description": description,
        "audio_heard": "private ASR transcript",
        "book_reference": "private printed-book content",
    }


def _load_qc_module():
    spec = importlib.util.spec_from_file_location("run_qc_cue_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_preliminary_cue_has_safe_header_stable_order_and_no_private_body_text(tmp_path: Path) -> None:
    cue = write_cue(
        [
            _flag("flag_0002", 12.0, severity="CRITICAL", category="CLIPPING"),
            _flag("flag_0003", 5.0, severity="STT_ARTIFACT", category="ASR_GUESS"),
            _flag("flag_0001", 12.0),
        ],
        tmp_path,
        "chapter_01",
        "triage_markers",
        "/private/recording\"\nsecret.wav",
    )

    assert cue.name == "triage_markers_chapter_01.cue"
    assert cue.read_text(encoding="utf-8") == "\n".join([
        "REM READBACK_SENTINEL PRELIMINARY_TRIAGE_REQUIRES_HUMAN_REVIEW",
        'PERFORMER "Readback Sentinel"',
        'TITLE "Review markers — recording\' secret.wav"',
        'FILE "recording\' secret.wav" WAVE',
        "  TRACK 01 AUDIO",
        '    TITLE "[REVIEW] WORD_SUBSTITUTION — flag_0001"',
        "    REM HUMAN_REVIEW_REQUIRED",
        "    INDEX 01 00:12:00",
        "  TRACK 02 AUDIO",
        '    TITLE "[CRITICAL] CLIPPING — flag_0002"',
        "    REM HUMAN_REVIEW_REQUIRED",
        "    INDEX 01 00:12:00",
        "",
    ])
    content = cue.read_text(encoding="utf-8")
    for forbidden in (
        "private transcript", "private printed-book", "ASR_GUESS", "flag_0003", 'recording"\nsecret.wav',
    ):
        assert forbidden not in content


def test_cue_uses_75_fps_flooring_at_second_and_minute_boundaries(tmp_path: Path) -> None:
    cue = write_cue(
        [
            _flag("fifth", 60.0),
            _flag("fourth", 59.999_999),
            _flag("third", 1.0),
            _flag("second", 0.999_999),
            _flag("first", 0.0),
        ],
        tmp_path,
        "fps",
        "triage_markers",
        "recording.wav",
    )

    assert [line for line in cue.read_text(encoding="utf-8").splitlines() if line.startswith("    INDEX")] == [
        "    INDEX 01 00:00:00",
        "    INDEX 01 00:00:74",
        "    INDEX 01 00:01:00",
        "    INDEX 01 00:59:74",
        "    INDEX 01 01:00:00",
    ]


@pytest.mark.parametrize("timestamp", [None, "12", -0.01, math.nan, math.inf, -math.inf])
def test_cue_rejects_invalid_timestamp_without_creating_final_artifact(tmp_path: Path, timestamp: object) -> None:
    with pytest.raises(ValueError, match="timestamp"):
        write_cue(
            [_flag("bad", timestamp)],  # type: ignore[arg-type]
            tmp_path,
            "invalid",
            "triage_markers",
            "recording.wav",
        )

    assert not (tmp_path / "triage_markers_invalid.cue").exists()


def test_cue_rejects_more_than_99_emitted_markers_without_creating_artifact(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="99"):
        write_cue(
            [_flag(f"flag_{number:04d}", float(number)) for number in range(100)],
            tmp_path,
            "overflow",
            "triage_markers",
            "recording.wav",
        )

    assert not (tmp_path / "triage_markers_overflow.cue").exists()


def test_empty_confirmed_cue_writes_only_the_safe_header(tmp_path: Path) -> None:
    cue = write_cue([], tmp_path, "empty", "audition_markers", "recording.wav")

    assert cue.read_text(encoding="utf-8") == "\n".join([
        "REM READBACK_SENTINEL HUMAN_CONFIRMED_ONLY",
        'PERFORMER "Readback Sentinel"',
        'TITLE "Review markers — recording.wav"',
        'FILE "recording.wav" WAVE',
        "",
    ])


def test_preliminary_run_exports_triage_cue_and_records_it_in_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    qc = _load_qc_module()
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16_000, 16_000, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 16_000)
    text = tmp_path / "source.txt"
    text.write_text("synthetic source", encoding="utf-8")

    def save_transcript(_segments, output: str, tag: str):
        transcript = Path(output) / f"transcript_{tag}.txt"
        segment_data = Path(output) / f"transcript_segments_{tag}.json"
        transcript.write_text("synthetic transcript", encoding="utf-8")
        segment_data.write_text("[]", encoding="utf-8")
        return transcript, segment_data

    def write_named(name: str):
        def write(*_args, **_kwargs):
            path = Path(_args[1]) / name
            path.write_text("synthetic", encoding="utf-8")
            return path
        return write

    def save_figure(_audio: str, output: str, **_kwargs) -> Path:
        path = Path(output)
        path.write_text("synthetic", encoding="utf-8")
        return path

    monkeypatch.setattr(qc, "transcribe", lambda *_args, **_kwargs: ([], ["synthetic transcript"]))
    monkeypatch.setattr(qc, "save_transcript", save_transcript)
    monkeypatch.setattr(qc, "align_fragment", lambda *_args, **_kwargs: SimpleNamespace(
        status="ALIGNMENT_FAILED", level=None, confidence=0.0, anchor_tokens=0,
        printed_start=None, printed_end=None, reason="synthetic", matched_pct=None,
    ))
    monkeypatch.setattr(qc, "analyze_glossary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(qc, "analyze_acoustics", lambda *_args, **_kwargs: {
        "duration": 1.0, "n_clipped": 0, "lead_silence": 0.0, "trail_silence": 0.0,
    })
    monkeypatch.setattr(qc, "build_acoustic_flags", lambda *_args, **_kwargs: [
        _flag("ac_0001", 0.5, severity="CRITICAL", category="CLIPPING"),
        _flag("asr_0001", 0.75, severity="STT_ARTIFACT", category="ASR_GUESS"),
    ])
    monkeypatch.setattr(qc, "analyze_prosody", lambda *_args, **_kwargs: {"monotony_flag": False})
    monkeypatch.setattr(qc, "save_prosody_figure", save_figure)
    monkeypatch.setattr(qc, "write_triage_docx", write_named("triage_chapter.docx"))
    monkeypatch.setattr(qc, "write_pdf", write_named("report_chapter.pdf"))

    assert qc.main([
        "--audio", str(audio), "--text", str(text), "--output", str(tmp_path / "runs"), "--tag", "chapter",
    ]) == 0

    run_dir = next((tmp_path / "runs").iterdir())
    manifest = json.loads(next(run_dir.glob("run_manifest_*.json")).read_text(encoding="utf-8"))
    cue = run_dir / "triage_markers_chapter.cue"
    assert cue.is_file()
    assert "ac_0001" in cue.read_text(encoding="utf-8")
    assert "asr_0001" not in cue.read_text(encoding="utf-8")
    assert "triage_markers_chapter.cue" in manifest["artifacts"]
