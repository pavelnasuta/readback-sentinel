"""Regression tests for audit findings fixed in v0.4.0."""
from __future__ import annotations

import json
from pathlib import Path

from audiobook_qc.alignment import align_fragment
from audiobook_qc.config import load_runtime_config
from audiobook_qc.normalize import normalize_for_alignment
from audiobook_qc.text_analysis import check_glossary, classify_diff

ROOT = Path(__file__).resolve().parents[1]
TEXT_THRESHOLDS = load_runtime_config(
    ROOT / "configs/thresholds.yaml", ROOT / "configs/policy.yaml"
).thresholds.text


def test_alignment_excludes_unread_context_after_end_anchor() -> None:
    spoken = "тихий ветер сегодня качает высокие деревья"
    printed = spoken + ". Затем начинается совсем другая история."

    result = align_fragment(printed, spoken, anchor_attempts=(6, 5, 4, 3))

    assert result.status == "ALIGNED"
    assert result.window_text == normalize_for_alignment(spoken)
    assert result.matched_pct == 100.0


def test_alignment_disambiguates_repeated_boundaries_by_interior_text() -> None:
    start = "утром ветер тихо качал ветви старого дерева"
    end = "вечером солнце скрылось за лесом после работы"
    first = f"{start} он ремонтировал крышу сарая {end}"
    second = f"{start} она читала новую книгу детям {end}"
    printed = first + ". Промежуточная глава. " + second

    result = align_fragment(printed, second, anchor_attempts=(8, 6, 4, 3))

    expected_start = len(normalize_for_alignment(first + ". Промежуточная глава. ").split())
    assert result.status == "ALIGNED"
    assert result.printed_start == expected_start
    assert result.window_text == normalize_for_alignment(second)
    assert result.matched_pct == 100.0


def test_alignment_fails_closed_when_two_candidates_are_equally_good() -> None:
    fragment = "утром ветер тихо качал ветви старого дерева"

    result = align_fragment(
        f"{fragment}. Другая глава. {fragment}.",
        fragment,
        anchor_attempts=(8, 6, 4, 3),
    )

    assert result.status == "ALIGNMENT_FAILED"
    assert "ambiguous" in (result.reason or "").lower()


def test_cardinal_normalization_preserves_enumerations_and_sentence_boundaries() -> None:
    assert normalize_for_alignment("один, два, три") == "1 2 3"
    assert normalize_for_alignment("один. Один.") == "1 1"
    assert normalize_for_alignment("двести двести") == "200 200"
    assert normalize_for_alignment("один, два, три") != normalize_for_alignment("шесть")


def test_cardinal_normalization_still_unifies_valid_compound_numbers() -> None:
    assert normalize_for_alignment("двести один") == "201"
    assert normalize_for_alignment("сорок две") == "42"
    assert normalize_for_alignment("двести") == "200"


def _segment(text: str, start: float, end: float, confidence: float = -0.1) -> dict:
    return {"text": text, "start": start, "end": end, "avg_logprob": confidence}


def test_multi_segment_diff_uses_complete_time_range_and_lowest_confidence() -> None:
    segments = [_segment("старт кот", 10, 12, -0.1), _segment("пес конец", 20, 22, -2.0)]

    flags = classify_diff(
        "старт кот пес конец",
        "старт рыба лиса конец",
        segments,
        TEXT_THRESHOLDS,
    )

    assert len(flags) == 1
    assert flags[0]["timestamp"] == 10
    assert flags[0]["timestamp_end"] == 22
    assert flags[0]["asr_confidence"] == -2.0
    assert flags[0]["severity"] == "REVIEW"


def test_segment_boundary_number_does_not_shift_following_timestamp() -> None:
    segments = [
        _segment("двадцать", 10, 11),
        _segment("один кот", 20, 22),
        _segment("синий дом", 30, 32, -2.0),
    ]

    flags = classify_diff(
        "двадцать\nодин кот\nсиний дом",
        "20 1 кот красный дом",
        segments,
        TEXT_THRESHOLDS,
    )

    assert len(flags) == 1
    assert flags[0]["timestamp"] == 30
    assert flags[0]["timestamp_end"] == 32
    assert flags[0]["asr_confidence"] == -2.0


def test_missing_text_after_audio_uses_last_real_segment_end() -> None:
    segments = [_segment("тихий ветер", 100, 110)]

    flags = classify_diff("тихий ветер", "тихий ветер новая глава", segments, TEXT_THRESHOLDS)

    assert flags[0]["category"] == "MISSING_TEXT"
    assert flags[0]["timestamp"] == flags[0]["timestamp_end"] == 110


def test_text_differences_are_review_candidates_not_automatic_verdicts() -> None:
    similar = classify_diff("он платит", "он платил", [_segment("он платит", 1, 2)], TEXT_THRESHOLDS)
    low_confidence = classify_diff("кот пес", "рыба лиса", [_segment("кот пес", 3, 4, -2)], TEXT_THRESHOLDS)

    assert similar[0]["severity"] == "REVIEW"
    assert similar[0]["category"] == "WORD_SUBSTITUTION"
    assert low_confidence[0]["severity"] == "REVIEW"


def test_marker_duration_uses_full_candidate_range(tmp_path: Path) -> None:
    from audiobook_qc.exports import write_marker_csv

    path = write_marker_csv([
        {"id": "flag_x", "timestamp": 10.0, "timestamp_end": 22.0,
         "category": "WORD_SUBSTITUTION", "description": "candidate"}
    ], str(tmp_path), "x", "markers")

    fields = path.read_text(encoding="utf-8").splitlines()[1].split(",")
    assert float(fields[2]) == 12.0


def test_glossary_uses_token_boundaries_variants_and_segment_times(tmp_path: Path) -> None:
    glossary = tmp_path / "glossary.json"
    glossary.write_text(json.dumps([
        {"term": "Семён"},
        {"term": "Анна"},
        {"term": "Рено", "transcription": "рэно"},
        {"term": "Згут"},
        {"term": "Иван Иванов"},
    ], ensure_ascii=False), encoding="utf-8")
    segments = [
        _segment("Семён увидел ванну и рэно.", 100, 110),
        _segment("Згут пришёл, затем Згуд ушёл.", 300, 310, -0.2),
        _segment("Иван Ивонов вошёл.", 400, 410, -0.3),
    ]

    flags = check_glossary(str(glossary), segments)

    assert len(flags) == 2
    assert flags[0]["category"] == "GLOSSARY_REVIEW"
    assert flags[0]["audio_heard"] == "згуд"
    assert flags[0]["timestamp"] == 300
    assert flags[0]["timestamp_end"] == 310
    assert flags[0]["asr_confidence"] == -0.2
    assert flags[1]["audio_heard"] == "иван ивонов"
    assert flags[1]["book_reference"] == "Иван Иванов"
    assert flags[1]["timestamp"] == 400
    assert flags[1]["timestamp_end"] == 410


def test_missing_explicit_glossary_is_an_error(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        check_glossary(str(tmp_path / "missing.json"), [])


def test_acoustics_preserves_native_channels_and_clipping_before_downmix(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    from audiobook_qc.acoustics import analyze_acoustics, build_acoustic_flags

    sr = 16_000
    t = np.arange(sr, dtype=np.float32) / sr
    clipped = np.clip(2 * np.sin(2 * np.pi * 220 * t), -1, 1)
    path = tmp_path / "stereo.wav"
    sf.write(path, np.column_stack([clipped, -clipped]), sr, subtype="FLOAT")

    metrics = analyze_acoustics(str(path), clip_sample_amplitude=0.5)
    flags = build_acoustic_flags(metrics, load_runtime_config(
        ROOT / "configs/thresholds.yaml", ROOT / "configs/policy.yaml"
    ))

    assert metrics["source_sample_rate"] == sr
    assert metrics["source_channels"] == 2
    assert metrics["clip_sample_amplitude"] == 0.5
    assert metrics["n_clipped"] > 0
    assert any(flag["category"] == "CLIPPING" for flag in flags)


def test_acoustics_includes_last_window_and_localizes_internal_silence(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    from audiobook_qc.acoustics import analyze_acoustics

    sr = 16_000
    tone = 0.5 * np.sin(2 * np.pi * 220 * np.arange(sr // 2) / sr)
    last_path = tmp_path / "last.wav"
    sf.write(last_path, np.r_[np.zeros(sr // 2), tone], sr, subtype="FLOAT")
    last = analyze_acoustics(str(last_path))
    assert last["rms_max"] > 0.2

    pause_path = tmp_path / "pause.wav"
    sf.write(pause_path, np.r_[tone, np.zeros(int(2.5 * sr)), tone], sr, subtype="FLOAT")
    pause = analyze_acoustics(str(pause_path))
    assert any(region["duration"] >= 2.0 for region in pause["silence_regions"])


def test_short_nonzero_signal_is_not_called_silent(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    from audiobook_qc.acoustics import analyze_acoustics

    sr = 16_000
    signal = np.zeros(sr, dtype=np.float32)
    signal[:320] = 0.5 * np.sin(2 * np.pi * 220 * np.arange(320) / sr)
    path = tmp_path / "sparse.wav"
    sf.write(path, signal, sr, subtype="FLOAT")

    assert analyze_acoustics(str(path))["has_signal"] is True


def test_percentile_ratio_is_diagnostic_not_an_snr_or_noise_verdict(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    from audiobook_qc.acoustics import analyze_acoustics, build_acoustic_flags

    sr = 16_000
    rng = np.random.default_rng(7)
    signal = 0.5 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr) + 0.1 * rng.standard_normal(sr)
    path = tmp_path / "signal.wav"
    sf.write(path, signal, sr, subtype="FLOAT")

    metrics = analyze_acoustics(str(path))
    flags = build_acoustic_flags(metrics, load_runtime_config(
        ROOT / "configs/thresholds.yaml", ROOT / "configs/policy.yaml"
    ))

    assert metrics["snr"] is None
    assert metrics["noise_to_signal"] is None
    assert metrics["noise_assessment"] == "NOT_CALIBRATED"
    assert "amplitude_percentile_ratio_db" in metrics
    assert not any(flag["category"] == "NOISE" for flag in flags)


def test_silent_prosody_is_not_evaluated(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    from audiobook_qc.prosody import analyze_prosody

    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(16_000), 16_000)
    thresholds = load_runtime_config(
        ROOT / "configs/thresholds.yaml", ROOT / "configs/policy.yaml"
    ).thresholds.prosody

    result = analyze_prosody(str(path), thresholds)

    assert result["status"] == "NOT_EVALUATED"
    assert result["monotony_flag"] is None


def test_short_voiced_prosody_is_insufficient_evidence() -> None:
    import numpy as np

    from audiobook_qc.prosody import summarize_prosody

    thresholds = load_runtime_config(
        ROOT / "configs/thresholds.yaml", ROOT / "configs/policy.yaml"
    ).thresholds.prosody
    voiced = np.ones(100, dtype=bool)
    f0 = np.full(100, 220.0)

    result = summarize_prosody(f0, voiced, sample_rate=100, hop_length=1, thresholds=thresholds)

    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["monotony_flag"] is None


def test_prosody_uses_robust_range_and_longest_voiced_run() -> None:
    import numpy as np

    from audiobook_qc.prosody import summarize_prosody

    voiced = np.r_[np.ones(100, dtype=bool), np.zeros(10, dtype=bool)]
    f0 = np.r_[np.full(99, 220.0), 880.0, np.nan * np.ones(10)]
    result = summarize_prosody(f0, voiced, sample_rate=100, hop_length=1)

    assert result["pitch_max"] < 300
    assert result["longest_voiced_duration_s"] == 1.0


def test_prosody_plot_reuses_analysis_and_keeps_440_hz_visible(tmp_path: Path, monkeypatch) -> None:
    import numpy as np

    from audiobook_qc import exports

    captured = []
    original_subplots = exports.plt.subplots

    def capture(*args, **kwargs):
        figure, axes = original_subplots(*args, **kwargs)
        captured.extend(axes)
        return figure, axes

    monkeypatch.setattr(exports.plt, "subplots", capture)
    monkeypatch.setattr(exports.librosa, "pyin", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pYIN recalculated")))
    analysis = {
        "_f0": np.array([440.0, 440.0]),
        "_f0_times": np.array([0.0, 1.0]),
        "_rms": np.array([0.2, 0.2]),
        "_rms_times": np.array([0.0, 1.0]),
    }

    exports.save_prosody_figure("unused.wav", str(tmp_path / "plot.png"), analysis=analysis)

    assert captured[0].get_ylim()[1] > 440
    assert (tmp_path / "plot.png").is_file()


def test_pdf_uses_neutral_statuses_and_escapes_user_text(tmp_path: Path) -> None:
    import pymupdf

    from audiobook_qc.exports import write_pdf

    report = {
        "audio_name": "recording_<b>.wav", "duration": 0.0, "tag": "x&y",
        "n_words": 0, "n_seg": 0, "matched_pct": None,
        "alignment": {"status": "ALIGNMENT_FAILED"},
        "acoustics": {
            "source_sample_rate": 48_000, "source_channels": 2,
            "analysis_sample_rate": 16_000, "peak": 0.0, "n_clipped": 0,
            "frac_clipped": 0.0, "rms_all": 0.0, "silence_count": 1,
            "lead_silence": 0.0, "trail_silence": 0.0,
            "noise_assessment": "NOT_CALIBRATED", "amplitude_percentile_ratio_db": None,
        },
        "prosody": {"status": "NOT_EVALUATED", "pitch_median": 0, "pitch_min": 0,
                     "pitch_max": 0, "pitch_range_semitones": 0, "pitch_std": 0,
                     "voiced_pct": 0, "monotony_flag": None},
        "n_flags": 0, "n_critical": 0, "n_review": 0, "n_artifact": 0,
        "flags": [], "verdict_accuracy": "не оценено", "verdict_noise": "не оценено",
        "verdict_clip": "кандидатов нет", "verdict_pause": "кандидатов нет",
        "verdict_intonation": "не оценено",
    }

    path = write_pdf(report, str(tmp_path), "x")
    with pymupdf.open(path) as document:
        text = "\n".join(page.get_text() for page in document)

    assert "recording_<b>.wav" in text
    for forbidden in ("без брака", "начитка чистая", "комфортный", "живой интонацией", "✓ выразительно", "✓ чисто"):
        assert forbidden not in text.lower()
    assert "не оценено" in text.lower()


def test_docx_extraction_reads_table_text(tmp_path: Path) -> None:
    import importlib.util

    from docx import Document

    spec = importlib.util.spec_from_file_location("qc_runner", ROOT / "scripts/run_qc.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    document = Document()
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Текст записи в таблице."
    path = tmp_path / "source.docx"
    document.save(path)

    assert "Текст записи в таблице." in module.extract_text(str(path))


def test_confirmation_requires_binding_to_exact_review_snapshot(tmp_path: Path) -> None:
    import pytest

    from audiobook_qc.manifest import (
        build_review_binding,
        ensure_unique_flag_ids,
        load_confirmed_flag_ids,
    )

    flags = [{"id": "ac_5000", "category": "SILENT_AUDIO", "timestamp": 0.0}]
    with pytest.raises(ValueError, match="Duplicate flag IDs"):
        ensure_unique_flag_ids([{"id": "ac_6000"}, {"id": "ac_6000"}])
    binding = build_review_binding(
        inputs=[{"role": "audio", "sha256": "a"}],
        config_hashes={"thresholds": "b"}, model="small", language="ru", flags=flags,
    )
    old = tmp_path / "old.json"
    old.write_text('{"confirmed_flag_ids":["ac_5000"]}')
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"review_binding":"other","confirmed_flag_ids":["ac_5000"]}')
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"review_binding": binding, "confirmed_flag_ids": ["ac_5000"]}))

    with pytest.raises(ValueError, match="review_binding"):
        load_confirmed_flag_ids(old, flags, binding)
    with pytest.raises(ValueError, match="different review"):
        load_confirmed_flag_ids(wrong, flags, binding)
    assert load_confirmed_flag_ids(valid, flags, binding) == {"ac_5000"}


def test_failed_run_gets_unique_directory_and_failed_manifest(tmp_path: Path, monkeypatch) -> None:
    import importlib.util
    import wave

    spec = importlib.util.spec_from_file_location("qc_runner_failed", ROOT / "scripts/run_qc.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16_000, 16_000, "NONE", "raw"))
        wav.writeframes(b"\0\0" * 16_000)
    text = tmp_path / "text.txt"
    text.write_text("проверочный текст")
    output = tmp_path / "runs"
    monkeypatch.setattr(module, "transcribe", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected failure")))

    exit_code = module.main(["--audio", str(audio), "--text", str(text), "--output", str(output), "--tag", "same"])

    run_dirs = [path for path in output.iterdir() if path.is_dir()]
    assert exit_code == 1
    assert len(run_dirs) == 1
    manifest = json.loads(next(run_dirs[0].glob("run_manifest_*.json")).read_text())
    assert manifest["status"] == "FAILED"
    assert manifest["error"]["type"] == "RuntimeError"
    assert manifest["inputs"][0]["filename"] == "audio.wav"


def test_each_analysis_run_uses_a_new_directory(tmp_path: Path, monkeypatch) -> None:
    import importlib.util
    import wave

    spec = importlib.util.spec_from_file_location("qc_runner_unique", ROOT / "scripts/run_qc.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16_000, 16_000, "NONE", "raw"))
        wav.writeframes(b"\0\0" * 16_000)
    text = tmp_path / "text.txt"
    text.write_text("проверочный текст")
    output = tmp_path / "runs"
    monkeypatch.setattr(module, "transcribe", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("stop")))

    assert module.main(["--audio", str(audio), "--text", str(text), "--output", str(output), "--tag", "same"]) == 1
    assert module.main(["--audio", str(audio), "--text", str(text), "--output", str(output), "--tag", "same"]) == 1

    assert len([path for path in output.iterdir() if path.is_dir()]) == 2


def test_confirmation_exports_from_review_report_without_rerunning_asr(tmp_path: Path, monkeypatch) -> None:
    import importlib.util
    import wave

    from audiobook_qc.manifest import build_review_binding, input_record

    spec = importlib.util.spec_from_file_location("qc_runner_confirmation", ROOT / "scripts/run_qc.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16_000, 16_000, "NONE", "raw"))
        wav.writeframes(b"\0\0" * 16_000)
    text = tmp_path / "text.txt"
    text.write_text("проверочный текст")
    flags = [{"id": "ac_5000", "timestamp": 0.0, "timestamp_end": 1.0,
              "severity": "CRITICAL", "category": "SILENT_AUDIO",
              "description": "кандидат", "action": "проверить"}]
    inputs = [input_record(audio, "audio"), input_record(text, "printed_text")]
    binding = build_review_binding(inputs=inputs, config_hashes={"thresholds": "t", "policy": "p"},
                                   model="medium", language="uk", flags=flags)
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "status": "PRELIMINARY_TRIAGE_REQUIRED", "review_binding": binding,
        "inputs": inputs, "config_hashes": {"thresholds": "t", "policy": "p"},
        "config_versions": {"thresholds": 7, "policy": 8},
        "model": "medium", "language": "uk", "flags": flags,
        "audio_name": "audio.wav", "tag": "same",
    }))
    confirmation = tmp_path / "confirmation.json"
    confirmation.write_text(json.dumps({"review_binding": binding, "confirmed_flag_ids": ["ac_5000"]}))
    monkeypatch.setattr(module, "transcribe", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ASR must not run")))

    exit_code = module.main([
        "--audio", str(audio), "--text", str(text), "--output", str(tmp_path / "runs"), "--tag", "same",
        "--confirmed-flags", str(confirmation), "--review-report", str(report),
    ])

    run_dir = next(path for path in (tmp_path / "runs").iterdir() if path.is_dir())
    manifest = json.loads(next(run_dir.glob("run_manifest_*.json")).read_text())
    assert exit_code == 0
    assert (run_dir / "перепись_same.docx").is_file()
    assert (run_dir / "audition_markers_same.csv").is_file()
    assert set(manifest["artifacts"]) == {"перепись_same.docx", "audition_markers_same.csv"}
    assert manifest["model"] == "medium"
    assert manifest["language"] == "uk"
    assert manifest["config_versions"] == {"thresholds": 7, "policy": 8}
    assert manifest["config_hashes"] == {"thresholds": "t", "policy": "p"}
