"""Dependency-free command-line orchestration for the portable bootstrap."""
from __future__ import annotations

import argparse
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence

from .apply import apply_plan
from .checks import discover_environment
from .models import ComponentState, ComponentStatus, OverallStatus, Receipt, Step, StepStatus
from .plan import build_install_plan
from .receipt import write_receipt


_EXIT_CODES = {
    OverallStatus.READY: 0,
    OverallStatus.FAILED: 1,
    OverallStatus.ACTION_REQUIRED: 2,
    OverallStatus.UNSUPPORTED_PLATFORM: 2,
}


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    """Run a read-only check or an approval-gated installation plan."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit:
        return 64

    if arguments.command == "install" and arguments.model and arguments.skip_model:
        print("bootstrap.py: error: --model and --skip-model cannot be used together", file=sys.stderr)
        return 64

    project_root = root or Path.cwd()
    receipt_path = Path(arguments.receipt) if arguments.receipt else None
    discovered = discover_environment(project_root)
    if arguments.command == "check":
        _emit(discovered, json_mode=arguments.json, receipt_path=receipt_path)
        return _EXIT_CODES[discovered.status]

    available = importlib.util.find_spec("pip") is not None
    receipt = _with_pip_availability(discovered, available)
    model_cache = _model_cache(arguments.model_cache) if arguments.model else None
    steps = build_install_plan(
        receipt,
        model=arguments.model,
        skip_model=arguments.skip_model,
        is_root=_is_root(),
        model_cache=str(model_cache) if model_cache else None,
    )
    applied_steps = _apply_dependencies_then_model(steps, assume_yes=arguments.yes)
    smoke_test = _smoke_test(applied_steps)
    status = _install_status(discovered.status, applied_steps, smoke_test)
    completed = replace(
        receipt,
        command="install",
        status=status,
        steps=applied_steps,
        smoke_test=smoke_test,
        next_action=_next_action(status, applied_steps),
    )
    _emit(completed, json_mode=arguments.json, receipt_path=receipt_path)
    return _EXIT_CODES[completed.status]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bootstrap.py")
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser("check")
    check.add_argument("--json", action="store_true")
    check.add_argument("--receipt")
    install = subcommands.add_parser("install")
    install.add_argument("--yes", action="store_true")
    install.add_argument("--model", choices=("tiny", "base", "small"))
    install.add_argument("--skip-model", action="store_true")
    install.add_argument("--model-cache")
    install.add_argument("--json", action="store_true")
    install.add_argument("--receipt")
    return parser


def _with_pip_availability(receipt: Receipt, available: bool) -> Receipt:
    components = tuple(
        ComponentStatus("pip", ComponentState.READY if available else ComponentState.MISSING)
        if component.name == "pip"
        else component
        for component in receipt.components
    )
    return replace(receipt, components=components)


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid is not None and geteuid() == 0)


def _model_cache(value: str | None) -> Path:
    if value:
        return Path(value)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "readback-sentinel" / "models"
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "readback-sentinel" / "models"
    return Path.home() / ".cache" / "readback-sentinel" / "models"


def _apply_dependencies_then_model(steps: tuple[Step, ...], *, assume_yes: bool) -> tuple[Step, ...]:
    sync_index = next((index for index, step in enumerate(steps) if step.id == "sync_dependencies"), None)
    if sync_index is None:
        return apply_plan(steps, assume_yes=assume_yes, confirm=_confirm, run=run_command)

    dependency_steps = apply_plan(
        steps[: sync_index + 1],
        assume_yes=assume_yes,
        confirm=_confirm,
        run=run_command,
    )
    if dependency_steps[-1].status is not StepStatus.COMPLETED:
        return dependency_steps + steps[sync_index + 1 :]
    model_steps = apply_plan(
        steps[sync_index + 1 :],
        assume_yes=assume_yes,
        confirm=_confirm,
        run=run_command,
    )
    return dependency_steps + model_steps


def _confirm(step: Step) -> bool:
    assert step.argv is not None
    print(f"Bootstrap will run: {' '.join(step.argv)}", file=sys.stderr)
    print("Continue? [y/N] ", end="", file=sys.stderr, flush=True)
    return input().strip().lower() in {"y", "yes"}


def run_command(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    """Run a planned tuple argv without a shell or receipt-visible command output."""
    if not isinstance(argv, tuple):
        raise TypeError("bootstrap commands must use tuple argv")
    return subprocess.run(
        argv,
        check=False,
        shell=False,
        capture_output=True,
        text=True,
    )


def _smoke_test(steps: tuple[Step, ...]) -> dict[str, object]:
    sync = next((step for step in steps if step.id == "sync_dependencies"), None)
    if sync is None or sync.status is not StepStatus.COMPLETED:
        return {"status": StepStatus.SKIPPED}
    commands = (
        ("uv", "run", "pytest", "-q"),
        ("uv", "run", "python", "scripts/run_qc.py", "--help"),
    )
    for command in commands:
        if run_command(command).returncode != 0:
            return {"status": "FAILED"}
    return {"status": "PASSED"}


def _install_status(
    discovery_status: OverallStatus,
    steps: tuple[Step, ...],
    smoke_test: dict[str, object],
) -> OverallStatus:
    if discovery_status is OverallStatus.UNSUPPORTED_PLATFORM:
        return OverallStatus.UNSUPPORTED_PLATFORM
    if any(step.status is StepStatus.FAILED for step in steps):
        return OverallStatus.FAILED
    if smoke_test["status"] == "FAILED":
        return OverallStatus.FAILED
    if any(
        step.status in {StepStatus.MANUAL_ACTION_REQUIRED, StepStatus.SKIPPED, StepStatus.NOT_RUN}
        for step in steps
    ):
        return OverallStatus.ACTION_REQUIRED
    return OverallStatus.READY


def _next_action(status: OverallStatus, steps: tuple[Step, ...]) -> str:
    if status is OverallStatus.READY:
        return ""
    if any(step.status is StepStatus.MANUAL_ACTION_REQUIRED for step in steps):
        return "Complete the manual action in the receipt, then rerun bootstrap."
    if any(step.status is StepStatus.SKIPPED for step in steps):
        return "Approve the skipped step, then rerun bootstrap."
    if status is OverallStatus.FAILED:
        return "Resolve the failed command before rerunning bootstrap."
    return "Resolve the reported prerequisites before installation."


def _emit(receipt: Receipt, *, json_mode: bool, receipt_path: Path | None) -> None:
    text = write_receipt(receipt, receipt_path)
    if json_mode:
        print(text)
        return
    print(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2))
