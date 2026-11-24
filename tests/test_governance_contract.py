"""Regression contract for the project's development-control layer."""
from __future__ import annotations

import ast
from pathlib import Path
import tomllib

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
    "docs/releases/v0.5.0.md",
    "INSTALL.md",
    "scripts/bootstrap.py",
    "scripts/prepare_model.py",
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


def test_bootstrap_documents_safe_setup_and_explicit_model_consent() -> None:
    """Catches public setup guidance bypassing check or silently requesting a model."""
    install = (ROOT / "INSTALL.md").read_text(encoding="utf-8").lower()
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8").lower()

    assert "bootstrap.py check --json" in install
    assert "action_required" in install
    assert "sudo" in install
    assert "--model small" in install
    assert "bootstrap.py check" in readme
    assert "install.md" in readme
    assert "bootstrap.py check --json" in skill
    assert "separate" in skill
    assert "--skip-model" in skill


def test_v050_release_note_limits_bootstrap_claims() -> None:
    """Catches release notes overstating the bootstrap as an accuracy or server release."""
    note = (ROOT / "docs" / "releases" / "v0.5.0.md").read_text(encoding="utf-8").lower()

    assert "bootstrap" in note
    assert "does not improve audio-quality accuracy" in note
    assert "does not add a production model or server" in note


def test_ci_runs_only_locked_synthetic_matrix() -> None:
    """Catches a CI change that drops an OS or adds bootstrap package-manager work."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").lower()

    assert "macos-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert "python-version: \"3.11\"" in workflow
    assert "uv sync --dev --locked" in workflow
    assert "uv run pytest -q" in workflow
    assert "brew" not in workflow
    assert "apt-get" not in workflow
    assert "winget" not in workflow


def test_v050_public_metadata_and_bootstrap_artifacts_are_configured() -> None:
    """Catches a public version bump without its locked package metadata or ignored local state."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lockfile = (ROOT / "uv.lock").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert project["project"]["version"] == "0.5.0"
    assert 'name = "readback-sentinel"\nversion = "0.5.0"' in lockfile
    assert "bootstrap-receipt*.json" in gitignore
    assert ".bootstrap-model-cache/" in gitignore
