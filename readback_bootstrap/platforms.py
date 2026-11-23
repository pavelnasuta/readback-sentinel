"""Platform-specific, command-only prerequisite planning."""
from __future__ import annotations

from collections.abc import Callable

from .models import PlatformInfo, Step, StepStatus


_DEBIAN_SYSTEMS = frozenset({"debian", "ubuntu"})


def platform_ffmpeg_step(
    platform: PlatformInfo,
    *,
    which: Callable[[str], str | None],
    is_root: bool,
) -> Step:
    """Return the safe ffmpeg prerequisite step for one detected platform."""
    if platform.system == "Darwin":
        if which("brew") is not None:
            return Step(
                id="install_ffmpeg",
                argv=("brew", "install", "ffmpeg"),
                requires_network=True,
            )
        return _manual_step(
            "Install Homebrew from https://brew.sh/ then rerun bootstrap."
        )

    if platform.system == "Windows":
        if which("winget") is not None:
            return Step(
                id="install_ffmpeg",
                argv=(
                    "winget",
                    "install",
                    "--id",
                    "Gyan.FFmpeg",
                    "-e",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ),
                requires_network=True,
            )
        return _manual_step(
            "Install or update App Installer to obtain winget, then rerun bootstrap."
        )

    if platform.system == "Linux" and platform.release.lower() in _DEBIAN_SYSTEMS:
        if which("apt-get") is None:
            return _manual_step(
                "Install apt-get for this Debian/Ubuntu system, then rerun bootstrap."
            )
        if is_root:
            return Step(
                id="install_ffmpeg",
                argv=("apt-get", "install", "-y", "ffmpeg"),
                requires_network=True,
            )
        return Step(
            id="install_ffmpeg",
            requires_network=True,
            requires_elevation=True,
            status=StepStatus.MANUAL_ACTION_REQUIRED,
            manual_action="sudo apt-get update && sudo apt-get install -y ffmpeg",
        )

    return _manual_step(
        "ffmpeg installation is unsupported on this platform; install ffmpeg manually."
    )


def _manual_step(action: str) -> Step:
    return Step(
        id="install_ffmpeg",
        requires_network=True,
        status=StepStatus.MANUAL_ACTION_REQUIRED,
        manual_action=action,
    )
