"""End-to-end contracts for the dependency-free bootstrap command surface."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from subprocess import CompletedProcess
import sys
from types import ModuleType

import pytest

from readback_bootstrap.apply import apply_plan
from readback_bootstrap.models import (
    ComponentState,
    ComponentStatus,
    OverallStatus,
    PlatformInfo,
    Receipt,
    Step,
    StepStatus,
)


def _ready_receipt() -> Receipt:
    """Return a complete discovery result without touching the host environment."""
    return Receipt(
        command="check",
        status=OverallStatus.READY,
        platform=PlatformInfo("Darwin", "test", "arm64"),
        python={"version": "3.11", "supported": True},
        components=(
            ComponentStatus("project_config", ComponentState.READY),
            ComponentStatus("lockfile", ComponentState.READY),
            ComponentStatus("python", ComponentState.READY),
            ComponentStatus("pip", ComponentState.UNKNOWN),
            ComponentStatus("uv", ComponentState.READY),
            ComponentStatus("ffmpeg", ComponentState.READY),
            ComponentStatus("brew", ComponentState.READY),
        ),
    )


def test_check_json_emits_one_read_only_receipt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Catches check mode reaching the command runner or emitting progress beside JSON."""
    from readback_bootstrap import cli

    monkeypatch.setattr(cli, "discover_environment", lambda _root: _ready_receipt())
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda _argv: pytest.fail("check must not execute a subprocess"),
    )

    assert cli.main(["check", "--json"], root=tmp_path) == 0

    output = capsys.readouterr().out
    assert output.count("\n") == 1
    payload = json.loads(output)
    assert payload["command"] == "check"
    assert payload["smoke_test"]["status"] == "NOT_RUN"
    assert payload["steps"] == []


def test_invalid_or_conflicting_model_usage_returns_64_without_discovery(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Catches invalid model input beginning discovery, planning, or installation."""
    from readback_bootstrap import cli

    monkeypatch.setattr(
        cli,
        "discover_environment",
        lambda _root: pytest.fail("invalid usage must not discover state"),
    )

    assert cli.main(["install", "--yes", "--model", "large-v3"], root=tmp_path) == 64
    assert cli.main(
        ["install", "--yes", "--model", "small", "--skip-model"], root=tmp_path
    ) == 64
    assert capsys.readouterr().out == ""


def test_model_runs_only_after_successful_sync_and_receipt_redacts_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Catches model preparation before sync or command output retained in install receipts."""
    from readback_bootstrap import cli

    calls: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...]) -> CompletedProcess[str]:
        calls.append(argv)
        return CompletedProcess(argv, 0, "TOKEN=do-not-store", "stderr-do-not-store")

    monkeypatch.setattr(cli, "discover_environment", lambda _root: _ready_receipt())
    monkeypatch.setattr(cli, "run_command", fake_run)
    cache = tmp_path / "model-cache"

    assert (
        cli.main(
            [
                "install",
                "--yes",
                "--model",
                "small",
                "--model-cache",
                str(cache),
                "--json",
            ],
            root=tmp_path,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert [step["id"] for step in payload["steps"]] == [
        "sync_dependencies",
        "prepare_model",
    ]
    assert calls == [
        ("uv", "sync", "--dev", "--locked"),
        (
            "uv",
            "run",
            "python",
            "scripts/prepare_model.py",
            "--model",
            "small",
            "--cache-dir",
            str(cache),
        ),
        ("uv", "run", "pytest", "-q"),
        ("uv", "run", "python", "scripts/run_qc.py", "--help"),
    ]
    receipt = json.dumps(payload)
    assert "TOKEN=do-not-store" not in receipt
    assert "stderr-do-not-store" not in receipt
    assert "stdout" not in receipt
    assert "stderr" not in receipt
    assert payload["smoke_test"]["status"] == "PASSED"


def test_failed_sync_does_not_prepare_model_or_run_smoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Catches dependency failure continuing into model download or smoke commands."""
    from readback_bootstrap import cli

    calls: list[tuple[str, ...]] = []

    def failed_sync(argv: tuple[str, ...]) -> CompletedProcess[str]:
        calls.append(argv)
        return CompletedProcess(argv, 1, "", "network failure")

    monkeypatch.setattr(cli, "discover_environment", lambda _root: _ready_receipt())
    monkeypatch.setattr(cli, "run_command", failed_sync)

    assert cli.main(["install", "--yes", "--model", "tiny", "--json"], root=tmp_path) == 1

    payload = json.loads(capsys.readouterr().out)
    assert calls == [("uv", "sync", "--dev", "--locked")]
    assert [step["status"] for step in payload["steps"]] == ["FAILED", "NOT_RUN"]
    assert payload["smoke_test"]["status"] == "SKIPPED"


def test_skip_model_never_plans_prepare_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Catches skip-model mode constructing a model helper command."""
    from readback_bootstrap import cli

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(cli, "discover_environment", lambda _root: _ready_receipt())
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda argv: calls.append(argv) or CompletedProcess(argv, 0, "", ""),
    )

    assert cli.main(["install", "--yes", "--skip-model", "--json"], root=tmp_path) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [step["id"] for step in payload["steps"]] == ["sync_dependencies"]
    assert all("prepare_model.py" not in argv for argv in calls)


def test_interactive_json_install_keeps_prompts_off_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Catches interactive approval prose corrupting the one-receipt JSON contract."""
    from readback_bootstrap import cli

    monkeypatch.setattr(cli, "discover_environment", lambda _root: _ready_receipt())
    monkeypatch.setattr("builtins.input", lambda *_args: "n")
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda _argv: pytest.fail("declined install must not execute commands"),
    )

    assert cli.main(["install", "--json"], root=tmp_path) == 2

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["steps"][0]["status"] == "SKIPPED"
    assert "Bootstrap will run:" not in captured.out
    assert "Continue? [y/N]" not in captured.out
    assert "Bootstrap will run:" in captured.err
    assert "Continue? [y/N]" in captured.err


def test_smoke_test_uses_executable_from_completed_sync_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches smoke checks reverting to a bare uv command outside PATH."""
    from readback_bootstrap import cli

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        cli,
        "run_command",
        lambda argv: calls.append(argv) or CompletedProcess(argv, 0),
    )

    result = cli._smoke_test(
        (
            Step(
                "sync_dependencies",
                ("/fixed/.local/bin/uv", "sync", "--dev", "--locked"),
                status=StepStatus.COMPLETED,
            ),
        )
    )

    assert result == {"status": "PASSED"}
    assert calls == [
        ("/fixed/.local/bin/uv", "run", "pytest", "-q"),
        ("/fixed/.local/bin/uv", "run", "python", "scripts/run_qc.py", "--help"),
    ]


def test_missing_executable_becomes_generic_failed_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a missing planned executable escaping as a traceback."""
    from readback_bootstrap import cli

    def missing(_argv: tuple[str, ...], **_kwargs: object) -> CompletedProcess[str]:
        raise FileNotFoundError("private executable location")

    monkeypatch.setattr(cli.subprocess, "run", missing)

    result = apply_plan(
        (Step("sync_dependencies", ("/fixed/.local/bin/uv", "sync")),),
        assume_yes=True,
        confirm=lambda _step: True,
        run=cli.run_command,
    )

    assert result[0].status is StepStatus.FAILED
    assert result[0].return_code != 0
    assert result[0].error == "COMMAND_FAILED"


def _load_prepare_model(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("prepare_model_for_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_model_validates_before_importing_dependency(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Catches an invalid helper request importing faster-whisper before parser validation."""
    root = Path(__file__).resolve().parents[1]
    helper = _load_prepare_model(root / "scripts" / "prepare_model.py")
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)

    assert helper.main(["--model", "large-v3", "--cache-dir", "ignored", "--json"]) == 64
    assert "faster_whisper" not in sys.modules
    assert capsys.readouterr().out == ""


def test_prepare_model_uses_only_allowed_model_and_requested_cache(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Catches model setup ignoring the allowed name or operator-selected cache directory."""
    root = Path(__file__).resolve().parents[1]
    helper = _load_prepare_model(root / "scripts" / "prepare_model.py")
    requests: list[tuple[str, str]] = []
    dependency = ModuleType("faster_whisper")

    class FakeWhisperModel:
        def __init__(self, model: str, *, download_root: str) -> None:
            requests.append((model, download_root))

    dependency.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", dependency)
    cache = tmp_path / "selected-cache"

    assert helper.main(["--model", "small", "--cache-dir", str(cache), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert requests == [("small", str(cache))]
    assert payload == {"model": "small", "status": "READY"}
    assert cache.is_dir()
    if sys.platform != "win32":
        assert cache.stat().st_mode & 0o777 == 0o700
