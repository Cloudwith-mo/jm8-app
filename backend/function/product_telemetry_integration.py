"""Failure-isolated product telemetry at authoritative API boundaries."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from product_telemetry import emit_product_metric
from product_telemetry_store import record_product_milestone


MilestoneRecorder = Callable[..., dict[str, object]]
MutationGuard = Callable[[str], Any]

_OUTCOME_MILESTONES = {
    "FirstEntryCreated",
    "FirstAnalysisCompleted",
    "FirstGroundedAskCompleted",
}


def record_authenticated_activity(
    user_id: str,
    *,
    stage: str | None = None,
    mutation_guard: MutationGuard | None = None,
    recorder: MilestoneRecorder = record_product_milestone,
) -> dict[str, object]:
    """Record first and weekly activity without changing request behavior."""

    if mutation_guard is not None:
        try:
            mutation_guard(user_id)
        except Exception:
            return {
                "status": "SKIPPED",
                "reason": "MutationGuardUnavailable",
                "metricStatuses": [],
            }

    return _record_milestones(
        user_id,
        ("ActivatedUser", "WeeklyActiveUser"),
        stage=_stage(stage),
        recorder=recorder,
    )


def record_product_outcome(
    user_id: str,
    milestone: str,
    *,
    stage: str | None = None,
    recorder: MilestoneRecorder = record_product_milestone,
) -> dict[str, object]:
    """Record one approved persisted product outcome without raising."""

    if milestone not in _OUTCOME_MILESTONES:
        return {
            "status": "SKIPPED",
            "reason": "UnsupportedOutcome",
            "metricStatuses": [],
        }

    return _record_milestones(
        user_id,
        (milestone,),
        stage=_stage(stage),
        recorder=recorder,
    )


def is_grounded_answer(answer: object) -> bool:
    """Return whether an Ask response is answered with source evidence."""

    if not isinstance(answer, Mapping):
        return False
    evidence = answer.get("evidence")
    return (
        answer.get("status") == "ANSWERED"
        and isinstance(evidence, list)
        and bool(evidence)
    )


def _record_milestones(
    user_id: str,
    names: tuple[str, ...],
    *,
    stage: str,
    recorder: MilestoneRecorder,
) -> dict[str, object]:
    statuses: list[dict[str, object]] = []
    for name in names:
        try:
            statuses.append(recorder(user_id, stage, name))
        except Exception:
            _emit_failure_safely(stage)
            statuses.append({
                "status": "FAILED",
                "metricName": name,
                "errorCode": "TelemetryIntegrationUnavailable",
            })
    return {
        "status": (
            "RECORDED"
            if all(item.get("status") != "FAILED" for item in statuses)
            else "PARTIAL"
        ),
        "metricStatuses": statuses,
    }


def _emit_failure_safely(stage: str) -> None:
    try:
        emit_product_metric(stage, "TelemetryEmissionFailed")
    except Exception:
        return


def _stage(value: str | None) -> str:
    candidate = value if value is not None else os.environ.get("STAGE", "dev")
    normalized = str(candidate).strip()
    return normalized or "dev"


__all__ = [
    "is_grounded_answer",
    "record_authenticated_activity",
    "record_product_outcome",
]
