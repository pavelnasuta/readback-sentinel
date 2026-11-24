"""Contracts for deterministic, command-only bootstrap planning."""
from __future__ import annotations

import inspect
import shutil
import sys

import pytest

from readback_bootstrap.models import (
    ComponentState,
    ComponentStatus,
    OverallStatus,
    PlatformInfo,
    Receipt,
    StepStatus,
)
from readback_bootstrap.plan import build_install_plan
from readback_bootstrap.platforms import platform_ffmpeg_step


def _receipt(*components: ComponentStatus, platform: PlatformInfo | None = None) -> Receipt:
    """Build a minimal discovery receipt for planning-only contracts."""
    return Receipt(
        command="check",
        status=OverallStatus.ACTION_REQUIRED,
        platform=platform or PlatformInfo("Darwin", "", "arm64"),
        python={"version": "3.11", "supported": True},
        components=components,
    )


@pytest.mark.parametrize(
    ("platform", "is_root", "argv"),
    [
        (
            PlatformInfo("Darwin", "", "arm64"),
            False,
            ("brew", "install", "ffmpeg"),
        ),
        (
            PlatformInfo("Windows", "", "AMD64"),
            False,
            (
                "winget",
                "install",
                "--id",
                "Gyan.FFmpeg",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ),
        ),
        (
            PlatformInfo("Linux", "debian", "x86_64"),
            True,
            ("apt-get", "install", "-y", "ffmpeg"),
        ),
        (
            PlatformInfo("Linux", "ubuntu", "x86_64"),
            True,
            ("apt-get", "install", "-y", "ffmpeg"),
        ),
    ],
)
def test_platform_plan_uses_exact_non_shell_ffmpeg_command(
    platform: PlatformInfo,
    is_root: bool,
    argv: tuple[str, ...],
) -> None:
    """Catches platform mappings that alter a supported package-manager command."""
    step = platform_ffmpeg_step(
        platform,
        package_manager_state=ComponentState.READY,
        is_root=is_root,
    )

    assert step.id == "install_ffmpeg"
    assert step.argv == argv
    assert step.status is StepStatus.NOT_RUN
    assert step.requires_network is True
    assert step.requires_elevation is False
    assert step.manual_action is None


def test_non_root_debian_requires_manual_sudo_step_without_sudo_argv() -> None:
    """Catches a planner that represents privilege escalation as an executable step."""
    step = platform_ffmpeg_step(
        PlatformInfo("Linux", "debian", "x86_64"),
        package_manager_state=ComponentState.READY,
        is_root=False,
    )

    assert step.status is StepStatus.MANUAL_ACTION_REQUIRED
    assert step.argv is None
    assert step.requires_network is True
    assert step.requires_elevation is True
    assert step.manual_action == "sudo apt-get update && sudo apt-get install -y ffmpeg"


@pytest.mark.parametrize(
    ("platform", "manager", "manual_fragment"),
    [
        (PlatformInfo("Darwin", "", "arm64"), "brew", "brew.sh"),
        (PlatformInfo("Windows", "", "AMD64"), "winget", "App Installer"),
    ],
)
def test_missing_platform_package_manager_requires_a_manual_action(
    platform: PlatformInfo, manager: str, manual_fragment: str
) -> None:
    """Catches guessing or executing a package-manager bootstrap installer."""
    step = platform_ffmpeg_step(
        platform,
        package_manager_state=ComponentState.MISSING,
        is_root=False,
    )

    assert step.id == "install_ffmpeg"
    assert step.status is StepStatus.MANUAL_ACTION_REQUIRED
    assert step.argv is None
    assert step.manual_action is not None
    assert manual_fragment in step.manual_action
    assert manager not in (step.argv or ())


def test_plan_installs_uv_with_confirmed_pip_then_syncs_dependencies() -> None:
    """Catches a plan that syncs before it can provide uv or changes locked sync arguments."""
    receipt = _receipt(
        ComponentStatus("ffmpeg", ComponentState.MISSING),
        ComponentStatus("uv", ComponentState.MISSING),
        ComponentStatus("pip", ComponentState.READY),
        ComponentStatus("brew", ComponentState.READY),
    )

    steps = build_install_plan(receipt, model=None, skip_model=False, is_root=False)

    assert [(step.id, step.argv) for step in steps] == [
        ("install_ffmpeg", ("brew", "install", "ffmpeg")),
        ("install_uv", (sys.executable, "-m", "pip", "install", "--user", "uv")),
        ("sync_dependencies", ("uv", "sync", "--dev", "--locked")),
    ]
    assert all(step.requires_network for step in steps)
    assert all(step.requires_elevation is False for step in steps)


def test_plan_uses_receipt_package_manager_state_without_host_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches planning that repeats executable discovery after receipt creation."""
    def sentinel_resolver(_name: str) -> str:
        raise AssertionError("planning must not inspect host executables")

    monkeypatch.setattr(shutil, "which", sentinel_resolver)
    ready_receipt = _receipt(
        ComponentStatus("ffmpeg", ComponentState.MISSING),
        ComponentStatus("uv", ComponentState.READY),
        ComponentStatus("brew", ComponentState.READY),
    )
    missing_receipt = _receipt(
        ComponentStatus("ffmpeg", ComponentState.MISSING),
        ComponentStatus("uv", ComponentState.READY),
        ComponentStatus("brew", ComponentState.MISSING),
    )

    assert "which" not in inspect.signature(build_install_plan).parameters
    assert "which" not in inspect.signature(platform_ffmpeg_step).parameters
    assert build_install_plan(
        ready_receipt, model=None, skip_model=False, is_root=False
    )[0].argv == ("brew", "install", "ffmpeg")
    missing_step = build_install_plan(
        missing_receipt, model=None, skip_model=False, is_root=False
    )[0]
    assert missing_step.status is StepStatus.MANUAL_ACTION_REQUIRED
    assert missing_step.argv is None


@pytest.mark.parametrize(
    ("platform", "manager", "manager_path", "is_root", "ffmpeg_argv"),
    [
        (
            PlatformInfo("Darwin", "", "arm64"),
            "brew",
            "/fixed/homebrew/bin/brew",
            False,
            ("/fixed/homebrew/bin/brew", "install", "ffmpeg"),
        ),
        (
            PlatformInfo("Windows", "", "AMD64"),
            "winget",
            "C:/fixed/WindowsApps/winget.exe",
            False,
            (
                "C:/fixed/WindowsApps/winget.exe",
                "install",
                "--id",
                "Gyan.FFmpeg",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ),
        ),
        (
            PlatformInfo("Linux", "debian", "x86_64"),
            "apt-get",
            "/fixed/usr/bin/apt-get",
            True,
            ("/fixed/usr/bin/apt-get", "install", "-y", "ffmpeg"),
        ),
    ],
)
def test_plan_executes_discovered_executables_outside_path(
    platform: PlatformInfo,
    manager: str,
    manager_path: str,
    is_root: bool,
    ffmpeg_argv: tuple[str, ...],
) -> None:
    """Catches discarding fixed-location discovery and falling back to bare commands."""
    receipt = _receipt(
        ComponentStatus("ffmpeg", ComponentState.MISSING),
        ComponentStatus("uv", ComponentState.READY, "/fixed/.local/bin/uv"),
        ComponentStatus(manager, ComponentState.READY, manager_path),
        platform=platform,
    )

    steps = build_install_plan(receipt, model=None, skip_model=False, is_root=is_root)

    assert steps[0].argv == ffmpeg_argv
    assert steps[1].argv == ("/fixed/.local/bin/uv", "sync", "--dev", "--locked")


def test_missing_pip_requires_manual_action_and_does_not_plan_uv_or_sync() -> None:
    """Catches a planner that guesses a pip installer or schedules unavailable uv."""
    receipt = _receipt(
        ComponentStatus("ffmpeg", ComponentState.READY),
        ComponentStatus("uv", ComponentState.MISSING),
        ComponentStatus("pip", ComponentState.UNKNOWN),
    )

    steps = build_install_plan(receipt, model=None, skip_model=False, is_root=False)

    assert len(steps) == 1
    assert steps[0].id == "install_uv"
    assert steps[0].status is StepStatus.MANUAL_ACTION_REQUIRED
    assert steps[0].argv is None
    assert steps[0].manual_action is not None
    assert "pip" in steps[0].manual_action.lower()


@pytest.mark.parametrize(
    ("model", "skip_model", "expected_ids"),
    [
        (None, False, ["sync_dependencies"]),
        ("tiny", False, ["sync_dependencies", "prepare_model"]),
        ("base", False, ["sync_dependencies", "prepare_model"]),
        ("small", False, ["sync_dependencies", "prepare_model"]),
        ("small", True, ["sync_dependencies"]),
    ],
)
def test_model_planning_allows_only_explicit_model_request_after_sync(
    model: str | None, skip_model: bool, expected_ids: list[str]
) -> None:
    """Catches model work being planned without an allowed opt-in after dependencies."""
    receipt = _receipt(
        ComponentStatus("ffmpeg", ComponentState.READY),
        ComponentStatus("uv", ComponentState.READY),
        ComponentStatus("pip", ComponentState.UNKNOWN),
    )

    steps = build_install_plan(receipt, model=model, skip_model=skip_model, is_root=False)

    assert [step.id for step in steps] == expected_ids
    if model is not None and not skip_model:
        assert steps[-1].argv == (
            "uv",
            "run",
            "python",
            "scripts/prepare_model.py",
            "--model",
            model,
        )
        assert steps[-1].requires_network is True


@pytest.mark.parametrize("model", ["latest", "large-v3", "", " base"])
def test_invalid_model_is_rejected_by_pure_planning_layer(model: str) -> None:
    """Catches accepting an unreviewed model name even when model work is skipped."""
    receipt = _receipt(ComponentStatus("uv", ComponentState.READY))

    with pytest.raises(ValueError, match="model"):
        build_install_plan(receipt, model=model, skip_model=True, is_root=False)


def test_model_plan_passes_operator_cache_to_helper() -> None:
    """Catches discarding an explicit model cache before the model helper executes."""
    receipt = _receipt(
        ComponentStatus("ffmpeg", ComponentState.READY),
        ComponentStatus("uv", ComponentState.READY),
    )

    steps = build_install_plan(
        receipt,
        model="small",
        skip_model=False,
        is_root=False,
        model_cache="/operator/chosen/cache",
    )

    assert steps[-1].argv == (
        "uv",
        "run",
        "python",
        "scripts/prepare_model.py",
        "--model",
        "small",
        "--cache-dir",
        "/operator/chosen/cache",
    )
