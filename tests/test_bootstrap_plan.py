"""Contracts for deterministic, command-only bootstrap planning."""
from __future__ import annotations

import sys
from collections.abc import Callable

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
    ("platform", "which", "is_root", "argv"),
    [
        (
            PlatformInfo("Darwin", "", "arm64"),
            lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None,
            False,
            ("brew", "install", "ffmpeg"),
        ),
        (
            PlatformInfo("Windows", "", "AMD64"),
            lambda name: "C:/Windows/winget.exe" if name == "winget" else None,
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
            lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
            True,
            ("apt-get", "install", "-y", "ffmpeg"),
        ),
        (
            PlatformInfo("Linux", "ubuntu", "x86_64"),
            lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
            True,
            ("apt-get", "install", "-y", "ffmpeg"),
        ),
    ],
)
def test_platform_plan_uses_exact_non_shell_ffmpeg_command(
    platform: PlatformInfo,
    which: Callable[[str], str | None],
    is_root: bool,
    argv: tuple[str, ...],
) -> None:
    """Catches platform mappings that alter a supported package-manager command."""
    step = platform_ffmpeg_step(platform, which=which, is_root=is_root)

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
        which=lambda _name: "/usr/bin/apt-get",
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
    step = platform_ffmpeg_step(platform, which=lambda _name: None, is_root=False)

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
    )

    steps = build_install_plan(
        receipt,
        model=None,
        skip_model=False,
        is_root=False,
        which=lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None,
    )

    assert [(step.id, step.argv) for step in steps] == [
        ("install_ffmpeg", ("brew", "install", "ffmpeg")),
        ("install_uv", (sys.executable, "-m", "pip", "install", "--user", "uv")),
        ("sync_dependencies", ("uv", "sync", "--dev", "--locked")),
    ]
    assert all(step.requires_network for step in steps)
    assert all(step.requires_elevation is False for step in steps)


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
