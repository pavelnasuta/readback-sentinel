"""Approval-gated execution of immutable bootstrap plans."""
from __future__ import annotations

from dataclasses import replace
from subprocess import CompletedProcess
from typing import Callable

from .models import Step, StepStatus


def apply_plan(
    steps: tuple[Step, ...],
    *,
    assume_yes: bool,
    confirm: Callable[[Step], bool],
    run: Callable[[tuple[str, ...]], CompletedProcess[str]],
) -> tuple[Step, ...]:
    """Apply approved non-elevated commands and retain only safe result metadata."""
    results = list(steps)

    for index, step in enumerate(results):
        if step.requires_elevation:
            results[index] = replace(step, status=StepStatus.MANUAL_ACTION_REQUIRED)
            continue
        if step.argv is None or step.status is not StepStatus.NOT_RUN:
            continue
        if not assume_yes and not confirm(step):
            results[index] = replace(step, status=StepStatus.SKIPPED)
            continue

        completed = run(step.argv)
        if completed.returncode != 0:
            results[index] = replace(
                step,
                status=StepStatus.FAILED,
                return_code=completed.returncode,
                error="COMMAND_FAILED",
            )
            return tuple(results)
        results[index] = replace(
            step, status=StepStatus.COMPLETED, return_code=completed.returncode
        )

    return tuple(results)
