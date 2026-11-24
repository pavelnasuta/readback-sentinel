"""Read-only prerequisite discovery for the portable bootstrap."""
from __future__ import annotations

import platform as host_platform
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import (
    ComponentState,
    ComponentStatus,
    OverallStatus,
    PlatformInfo,
    Receipt,
)


_SUPPORTED_SYSTEMS = frozenset({"Darwin", "Linux", "Windows"})


def discover_environment(
    root: Path,
    *,
    system: str | None = None,
    machine: str | None = None,
    python_version: tuple[int, int] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Callable[..., Any] | None = None,
) -> Receipt:
    """Inspect bootstrap prerequisites without executing commands or changing state."""
    del run
    detected_system = system if system is not None else host_platform.system()
    resolver = which if which is not None else lambda name: _default_which(name, detected_system)
    detected_machine = machine if machine is not None else host_platform.machine()
    detected_python = python_version or (sys.version_info.major, sys.version_info.minor)
    platform = PlatformInfo(
        system=detected_system,
        release=_platform_release(detected_system),
        machine=detected_machine,
    )

    components = (
        _project_component("project_config", root / "pyproject.toml"),
        _project_component("lockfile", root / "uv.lock"),
        _python_component(detected_python),
        ComponentStatus("pip", ComponentState.UNKNOWN),
        _executable_component("uv", resolver),
        _executable_component("ffmpeg", resolver),
        *_package_manager_components(detected_system, resolver),
    )
    status = _overall_status(detected_system, components)
    return Receipt(
        command="check",
        status=status,
        platform=platform,
        python={
            "version": f"{detected_python[0]}.{detected_python[1]}",
            "supported": detected_python >= (3, 11),
        },
        components=components,
        next_action=_next_action(status),
    )


def _platform_release(system: str) -> str:
    """Return only a supported Linux distribution family, never a kernel version."""
    if system != "Linux":
        return host_platform.release()
    if Path("/etc/debian_version").is_file():
        return "debian"
    return ""


def _project_component(name: str, path: Path) -> ComponentStatus:
    return ComponentStatus(
        name=name,
        status=ComponentState.READY if path.is_file() else ComponentState.MISSING,
    )


def _python_component(version: tuple[int, int]) -> ComponentStatus:
    if version >= (3, 11):
        return ComponentStatus("python", ComponentState.READY)
    return ComponentStatus(
        "python",
        ComponentState.UNSUPPORTED,
        "Python 3.11 or newer is required.",
    )


def _default_which(name: str, system: str) -> str | None:
    """Find a known executable without consulting PATH or PATHEXT."""
    suffixes = (".exe", ".cmd", ".bat", "") if system == "Windows" else ("",)
    for directory in _executable_directories(system):
        for suffix in suffixes:
            candidate = directory / f"{name}{suffix}"
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            if system != "Windows" and not candidate.stat().st_mode & 0o111:
                continue
            return str(candidate)
    return None


def _executable_directories(system: str) -> tuple[Path, ...]:
    """Return explicitly approved executable locations for each supported system."""
    if system == "Darwin":
        return (
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            Path("/bin"),
            Path.home() / ".local" / "bin",
        )
    if system == "Linux":
        return (
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            Path("/bin"),
            Path("/snap/bin"),
            Path.home() / ".local" / "bin",
        )
    if system == "Windows":
        return (
            Path("C:/Windows/System32"),
            Path("C:/Program Files/PowerShell/7"),
            Path("C:/Program Files/Git/cmd"),
            Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps",
        )
    return ()


def _executable_component(
    name: str, which: Callable[[str], str | None]
) -> ComponentStatus:
    resolved_path = which(name)
    return ComponentStatus(
        name,
        ComponentState.READY if resolved_path is not None else ComponentState.MISSING,
        resolved_path,
    )


def _package_manager_components(
    system: str, which: Callable[[str], str | None]
) -> tuple[ComponentStatus, ...]:
    manager = {"Darwin": "brew", "Linux": "apt-get", "Windows": "winget"}.get(system)
    if manager is None:
        return ()
    return (_executable_component(manager, which),)


def _overall_status(
    system: str, components: tuple[ComponentStatus, ...]
) -> OverallStatus:
    if system not in _SUPPORTED_SYSTEMS:
        return OverallStatus.UNSUPPORTED_PLATFORM
    if any(
        component.status in {ComponentState.MISSING, ComponentState.UNSUPPORTED}
        for component in components
    ):
        return OverallStatus.ACTION_REQUIRED
    return OverallStatus.READY


def _next_action(status: OverallStatus) -> str:
    if status is OverallStatus.READY:
        return ""
    if status is OverallStatus.UNSUPPORTED_PLATFORM:
        return "Use a supported operating system or complete setup manually."
    return "Resolve the reported prerequisites before installation."
