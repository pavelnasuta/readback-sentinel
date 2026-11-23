"""Contracts for dependency-free, read-only bootstrap discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from readback_bootstrap.checks import discover_environment
from readback_bootstrap.models import OverallStatus, PlatformInfo, Receipt


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


class _EnvironmentPathAccessForbidden:
    """Fail immediately if safe discovery touches PATH-style environment keys."""

    def __init__(self, environment: object) -> None:
        self._environment = environment

    def __getitem__(self, key: str) -> str:
        self._reject_path_key(key)
        return self._environment[key]  # type: ignore[index]

    def get(self, key: str, default: object = None) -> object:
        self._reject_path_key(key)
        return self._environment.get(key, default)  # type: ignore[union-attr]

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            self._reject_path_key(key)
        return key in self._environment  # type: ignore[operator]

    def __setitem__(self, key: str, value: str) -> None:
        self._reject_path_key(key)
        self._environment[key] = value  # type: ignore[index]

    def __delitem__(self, key: str) -> None:
        self._reject_path_key(key)
        del self._environment[key]  # type: ignore[index]

    @staticmethod
    def _reject_path_key(key: str) -> None:
        if key in {"PATH", "PATHEXT"}:
            raise AssertionError("safe discovery must not inspect PATH-style variables")


def test_default_discovery_does_not_read_path_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a default resolver that delegates executable lookup to PATH."""
    monkeypatch.setattr(os, "environ", _EnvironmentPathAccessForbidden(os.environ))

    receipt = discover_environment(
        _project_root(tmp_path),
        system="Darwin",
        machine="arm64",
        python_version=(3, 11),
    )

    assert receipt.command == "check"


@pytest.mark.parametrize(
    ("unsafe_value", "expected_error"),
    [
        (Path("/private/receipt-path"), TypeError),
        ({"not-json-safe"}, TypeError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
    ],
    ids=["path", "set", "nan", "infinity"],
)
def test_receipt_to_dict_rejects_non_json_safe_values(
    unsafe_value: object, expected_error: type[Exception]
) -> None:
    """Catches receipt normalization that leaks unsupported values to JSON encoding."""
    receipt = Receipt(
        command="check",
        status=OverallStatus.READY,
        platform=PlatformInfo(system="Darwin", release="test", machine="arm64"),
        python={"unsafe": unsafe_value},
        components=(),
    )

    with pytest.raises(expected_error):
        receipt.to_dict()


def test_receipt_to_dict_rejects_non_string_dictionary_keys() -> None:
    """Catches normalization that silently stringifies unsupported mapping keys."""
    receipt = Receipt(
        command="check",
        status=OverallStatus.READY,
        platform=PlatformInfo(system="Darwin", release="test", machine="arm64"),
        python={1: "not-json-safe"},  # type: ignore[dict-item]
        components=(),
    )

    with pytest.raises(TypeError):
        receipt.to_dict()
