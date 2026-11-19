"""Contracts for the v0.3.0 configurable QC core."""
from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_qc.alignment import align_fragment
from audiobook_qc.config import ConfigError, load_runtime_config
from audiobook_qc.manifest import RunManifest, input_record
from audiobook_qc.normalize import normalize_for_alignment

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_config_loads_project_policy_and_thresholds() -> None:
    config = load_runtime_config(
        ROOT / "configs" / "thresholds.yaml",
        ROOT / "configs" / "policy.yaml",
    )

    assert config.thresholds.text.anchor_token_attempts == (15, 12, 10, 8, 6)
    assert config.policy.safety.candidate_not_verdict is True
    assert config.policy.routing.narrator_docx == "confirmed_only"


def test_runtime_thresholds_only_expose_active_detection_controls() -> None:
    config = load_runtime_config(
        ROOT / "configs" / "thresholds.yaml",
        ROOT / "configs" / "policy.yaml",
    )

    assert not hasattr(config.thresholds.text, "phonetic_similarity_artifact")
    assert not hasattr(config.thresholds.text, "confident_asr_logprob")
    assert not hasattr(config.thresholds.text, "critical_min_tokens_for_add_or_missing")
    assert not hasattr(config.thresholds.audio, "snr_review_db")


def test_runtime_config_rejects_unsafe_narrator_routing(tmp_path: Path) -> None:
    thresholds = tmp_path / "thresholds.yaml"
    policy = tmp_path / "policy.yaml"
    thresholds.write_text((ROOT / "configs" / "thresholds.yaml").read_text(), encoding="utf-8")
    policy.write_text((ROOT / "configs" / "policy.yaml").read_text().replace(
        "narrator_docx: confirmed_only", "narrator_docx: all_candidates"
    ), encoding="utf-8")

    with pytest.raises(ConfigError, match="narrator_docx"):
        load_runtime_config(thresholds, policy)


def test_normalization_matches_yo_hyphen_and_cardinal_number() -> None:
    assert normalize_for_alignment("Рёв — двести  ёлок") == "рев 200 елок"
    assert normalize_for_alignment("Рев 200-елок") == "рев 200 елок"


def test_hierarchical_alignment_uses_normalized_sentence_anchor() -> None:
    printed = "Вступление. Рёв двести ёлок наполнил площадь. Эпилог."
    recognized = "рев 200 елок наполнил площадь"

    result = align_fragment(printed, recognized, anchor_attempts=(6, 5, 4, 3))

    assert result.status == "ALIGNED"
    assert result.level == "sentence"
    assert result.matched_pct == 100.0
    assert "рев 200 елок" in result.window_text


def test_hierarchical_alignment_refuses_unrelated_text() -> None:
    result = align_fragment(
        "В книге говорится о погоде и путешествии.",
        "распознанный фрагмент никак не связан с источником",
        anchor_attempts=(6, 5, 4, 3),
    )

    assert result.status == "ALIGNMENT_FAILED"
    assert result.window_tokens == ()


def test_run_manifest_contains_hashed_input_and_terminal_status(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("секретный текст", encoding="utf-8")
    manifest = RunManifest.start(
        tag="chapter_01",
        tool_version="0.3.0",
        model="small",
        config_versions={"thresholds": 1, "policy": 1},
        inputs=[input_record(source, "printed_text")],
    )
    manifest.finish("COMPLETED", artifacts=["report_chapter_01.json"])

    data = manifest.to_dict()
    assert data["status"] == "COMPLETED"
    assert data["inputs"][0]["sha256"]
    assert data["artifacts"] == ["report_chapter_01.json"]
