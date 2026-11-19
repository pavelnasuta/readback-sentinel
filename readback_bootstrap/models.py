"""Immutable, JSON-safe models for portable bootstrap receipts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
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
        return _normalise(asdict(self))


def _normalise(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    return value
