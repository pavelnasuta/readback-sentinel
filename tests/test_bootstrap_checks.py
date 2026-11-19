"""Contracts for dependency-free, read-only bootstrap discovery."""
from __future__ import annotations

import json
from pathlib import Path

from readback_bootstrap.checks import discover_environment
from readback_bootstrap.models import OverallStatus


def _project_root(tmp_path: Path) -> Path:
    """Create the two metadata files discovery is permitted to inspect."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return tmp_path


def test_discovery_reports_ready_macos_without_executing_processes(tmp_path: Path) -> None:
    """Catches a check implementation that executes its injected process runner."""
    called: list[object] = []

    receipt = discover_environment(
        _project_root(tmp_path),
        system="Darwin",
        machine="arm64",
        python_version=(3, 11),
        which=lambda name: {
            "uv": "/bin/uv",
            "ffmpeg": "/bin/ffmpeg",
            "brew": "/opt/homebrew/bin/brew",
        }.get(name),
        run=lambda argv: called.append(argv),
    )

    assert receipt.command == "check"
    assert receipt.status is OverallStatus.READY
    assert receipt.platform.system == "Darwin"
    assert called == []
    payload = receipt.to_dict()
    assert payload["status"] == "READY"
    assert json.loads(json.dumps(payload))["platform"]["machine"] == "arm64"


def test_discovery_reports_action_required_for_old_python_and_missing_tools(tmp_path: Path) -> None:
    """Catches missing prerequisites being reported as ready or raised as errors."""
    receipt = discover_environment(
        _project_root(tmp_path),
        system="Windows",
        machine="AMD64",
        python_version=(3, 10),
        which=lambda _name: None,
    )

    assert receipt.status is OverallStatus.ACTION_REQUIRED
    assert {
        item.name for item in receipt.components if item.status.value != "READY"
    } >= {"python", "pip", "uv", "ffmpeg", "winget"}
