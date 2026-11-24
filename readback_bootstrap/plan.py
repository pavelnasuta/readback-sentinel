"""Immutable, command-only prerequisite plan construction."""
from __future__ import annotations

import sys

from .models import ComponentState, Receipt, Step, StepStatus
from .platforms import platform_ffmpeg_step


_ALLOWED_MODELS = frozenset({"tiny", "base", "small"})


def build_install_plan(
    receipt: Receipt,
    *,
    model: str | None,
    skip_model: bool,
    is_root: bool,
    model_cache: str | None = None,
) -> tuple[Step, ...]:
    """Build a deterministic plan without executing commands or inspecting state."""
    _validate_model(model)
    steps: list[Step] = []

    if _component_state(receipt, "ffmpeg") is not ComponentState.READY:
        steps.append(
            platform_ffmpeg_step(
                receipt.platform,
                package_manager_state=_package_manager_state(receipt),
                package_manager_executable=_component_executable(
                    receipt, _package_manager_name(receipt)
                ),
                is_root=is_root,
            )
        )

    uv_available = _component_state(receipt, "uv") is ComponentState.READY
    uv_executable = _component_executable(receipt, "uv") or "uv"
    if not uv_available:
        if _component_state(receipt, "pip") is ComponentState.READY:
            steps.append(
                Step(
                    id="install_uv",
                    argv=(sys.executable, "-m", "pip", "install", "--user", "uv"),
                    requires_network=True,
                )
            )
            uv_available = True
        else:
            steps.append(
                Step(
                    id="install_uv",
                    requires_network=True,
                    status=StepStatus.MANUAL_ACTION_REQUIRED,
                    manual_action=(
                        "Install pip for this Python interpreter, then rerun bootstrap."
                    ),
                )
            )

    if uv_available:
        steps.append(
            Step(
                id="sync_dependencies",
                argv=(uv_executable, "sync", "--dev", "--locked"),
                requires_network=True,
            )
        )
        if model is not None and not skip_model:
            steps.append(
                Step(
                    id="prepare_model",
                    argv=(
                        uv_executable,
                        "run",
                        "python",
                        "scripts/prepare_model.py",
                        "--model",
                        model,
                        *( ("--cache-dir", model_cache) if model_cache is not None else () ),
                    ),
                    requires_network=True,
                )
            )

    return tuple(steps)


def _component_state(receipt: Receipt, name: str) -> ComponentState | None:
    for component in receipt.components:
        if component.name == name:
            return component.status
    return None


def _component_executable(receipt: Receipt, name: str | None) -> str | None:
    if name is None:
        return None
    for component in receipt.components:
        if component.name == name and component.status is ComponentState.READY:
            return component.detail
    return None


def _package_manager_state(receipt: Receipt) -> ComponentState | None:
    manager = _package_manager_name(receipt)
    return _component_state(receipt, manager) if manager is not None else None


def _package_manager_name(receipt: Receipt) -> str | None:
    return {"Darwin": "brew", "Linux": "apt-get", "Windows": "winget"}.get(
        receipt.platform.system
    )


def _validate_model(model: str | None) -> None:
    if model is not None and model not in _ALLOWED_MODELS:
        raise ValueError("model must be one of: tiny, base, small")
