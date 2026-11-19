"""Dependency-free primitives for safe local bootstrap discovery."""

from .checks import discover_environment
from .models import (
    ComponentState,
    ComponentStatus,
    OverallStatus,
    PlatformInfo,
    Receipt,
    Step,
    StepStatus,
)

__all__ = [
    "ComponentState",
    "ComponentStatus",
    "OverallStatus",
    "PlatformInfo",
    "Receipt",
    "Step",
    "StepStatus",
    "discover_environment",
]
