"""Immutable, JSON-safe models for portable bootstrap receipts."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
import math
from typing import Any


class OverallStatus(str, Enum):
    READY = "READY"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    FAILED = "FAILED"
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"


class StepStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    SKIPPED = "SKIPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"


class ComponentState(str, Enum):
    READY = "READY"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class PlatformInfo:
    system: str
    release: str
    machine: str


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    status: ComponentState
    detail: str | None = None


@dataclass(frozen=True)
class Step:
    id: str
    argv: tuple[str, ...] | None = None
    requires_network: bool = False
    requires_elevation: bool = False
    status: StepStatus = StepStatus.NOT_RUN
    manual_action: str | None = None


@dataclass(frozen=True)
class Receipt:
    command: str
    status: OverallStatus
    platform: PlatformInfo
    python: dict[str, object]
    components: tuple[ComponentStatus, ...]
    steps: tuple[Step, ...] = ()
    smoke_test: dict[str, object] = field(
        default_factory=lambda: {"status": StepStatus.NOT_RUN}
    )
    next_action: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return a receipt that ``json.dumps`` can serialize without custom hooks."""
        return _normalise(self)


def _normalise(value: object) -> Any:
    if isinstance(value, Enum):
        return _normalise(value.value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is not JSON-safe")
        return value
    if isinstance(value, (Receipt, PlatformInfo, ComponentStatus, Step)):
        return {
            declared_field.name: _normalise(getattr(value, declared_field.name))
            for declared_field in fields(value)
        }
    if isinstance(value, dict):
        normalised: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            normalised[key] = _normalise(item)
        return normalised
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")
