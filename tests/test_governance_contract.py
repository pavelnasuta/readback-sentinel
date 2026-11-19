"""Regression contract for the project's development-control layer."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_GOVERNANCE_FILES = (
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "SECURITY.md",
    "pyproject.toml",
    ".gitignore",
    "docs/ARCHITECTURE.md",
    "docs/DEVELOPMENT.md",
    "docs/PUBLIC_RELEASE.md",
    "docs/RELEASE_TEMPLATE.md",
    "docs/TUNING_PROTOCOL.md",
    "docs/releases/v0.4.1.md",
    "configs/policy.yaml",
    "configs/thresholds.yaml",
    "eval/README.md",
    "eval/manifest.example.csv",
    "eval/labels.example.jsonl",
)


def test_governance_files_are_present() -> None:
    missing = [path for path in REQUIRED_GOVERNANCE_FILES if not (ROOT / path).is_file()]
    assert not missing, f"Missing governance files: {', '.join(missing)}"


def test_current_pipeline_is_syntax_valid() -> None:
    source = (ROOT / "scripts" / "run_qc.py").read_text(encoding="utf-8")
    ast.parse(source)


def test_public_release_note_declares_candidate_only_boundary() -> None:
    note = (ROOT / "docs" / "releases" / "v0.4.1.md").read_text(encoding="utf-8")
    assert "not establish" in note.lower()
    assert "synthetic suite" in note.lower()


def test_public_release_boundary_requires_human_confirmation() -> None:
    boundary = (ROOT / "docs" / "PUBLIC_RELEASE.md").read_text(encoding="utf-8")
    assert "human confirmation" in boundary.lower()
    assert "customer" in boundary.lower()
