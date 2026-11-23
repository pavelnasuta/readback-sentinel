"""Contracts for approval-gated bootstrap application and redacted receipts."""
from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

from readback_bootstrap.apply import apply_plan
from readback_bootstrap.models import (
    OverallStatus,
    PlatformInfo,
    Receipt,
    Step,
    StepStatus,
)
from readback_bootstrap.receipt import write_receipt


def _receipt_from_steps(steps: tuple[Step, ...]) -> Receipt:
    """Build a minimal receipt for serialization contracts only."""
    return Receipt(
        command="install",
        status=OverallStatus.FAILED,
        platform=PlatformInfo("Darwin", "test", "arm64"),
        python={"version": "3.11", "supported": True},
        components=(),
        steps=steps,
    )


def test_yes_marks_elevated_step_manual_without_runner_or_confirmation() -> None:
    """Catches unattended application that executes or approves elevation-required work."""
    executed: list[tuple[str, ...]] = []
    confirmations: list[Step] = []
    steps = (
        Step(
            "apt_ffmpeg",
            ("apt-get", "install", "-y", "ffmpeg"),
            requires_network=True,
            requires_elevation=True,
        ),
    )

    result = apply_plan(
        steps,
        assume_yes=True,
        confirm=lambda step: confirmations.append(step) or True,
        run=lambda argv: executed.append(argv) or CompletedProcess(argv, 0, "", ""),
    )

    assert result[0].status is StepStatus.MANUAL_ACTION_REQUIRED
    assert executed == []
    assert confirmations == []


def test_failed_command_stops_later_steps_and_redacts_raw_output(tmp_path: Path) -> None:
    """Catches continuing after failure or preserving external command output in a receipt."""
    steps = (
        Step("install_uv", ("python", "-m", "pip", "install", "--user", "uv")),
        Step("sync", ("uv", "sync", "--dev", "--locked")),
    )
    calls: list[tuple[str, ...]] = []

    result = apply_plan(
        steps,
        assume_yes=True,
        confirm=lambda _step: True,
        run=lambda argv: calls.append(argv)
        or CompletedProcess(argv, 1, "TOKEN=never-show", "stderr-never-show"),
    )
    text = write_receipt(_receipt_from_steps(result), tmp_path / "receipt.json")

    assert result[0].status is StepStatus.FAILED
    assert result[0].return_code == 1
    assert result[0].error == "COMMAND_FAILED"
    assert result[1].status is StepStatus.NOT_RUN
    assert calls == [("python", "-m", "pip", "install", "--user", "uv")]
    assert "TOKEN=never-show" not in text
    assert "stderr-never-show" not in text
    assert "stdout" not in text
    assert "stderr" not in text


def test_declined_step_is_skipped_without_runner_and_later_step_runs() -> None:
    """Catches a declined interactive step reaching the runner or halting unrelated work."""
    steps = (
        Step("install_uv", ("python", "-m", "pip", "install", "--user", "uv")),
        Step("sync", ("uv", "sync", "--dev", "--locked")),
    )
    confirmations: list[str] = []
    executed: list[tuple[str, ...]] = []

    result = apply_plan(
        steps,
        assume_yes=False,
        confirm=lambda step: confirmations.append(step.id) or step.id == "sync",
        run=lambda argv: executed.append(argv) or CompletedProcess(argv, 0, "", ""),
    )

    assert [step.status for step in result] == [StepStatus.SKIPPED, StepStatus.COMPLETED]
    assert confirmations == ["install_uv", "sync"]
    assert executed == [("uv", "sync", "--dev", "--locked")]


def test_receipt_file_is_parseable_equals_returned_json_and_leaves_no_temp_file(
    tmp_path: Path,
) -> None:
    """Catches a receipt writer that emits non-JSON or leaves its replacement file behind."""
    path = tmp_path / "receipt.json"
    receipt = _receipt_from_steps((Step("sync", ("uv", "sync", "--dev", "--locked")),))

    text = write_receipt(receipt, path)

    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(text) == receipt.to_dict()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["receipt.json"]


def test_receipt_without_path_writes_no_file(tmp_path: Path) -> None:
    """Catches optional receipt serialization mutating the filesystem without a requested path."""
    text = write_receipt(_receipt_from_steps(()), None)

    assert json.loads(text)["command"] == "install"
    assert list(tmp_path.iterdir()) == []
