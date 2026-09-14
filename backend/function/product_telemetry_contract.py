"""Privacy-safe product telemetry contract for the JM8 alpha."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from numbers import Real
from typing import Any


TELEMETRY_VERSION = "jm8-product-telemetry-v1"
EVENT_TYPE = "JM8ProductMetric"
ALLOWED_STAGES = frozenset({"dev", "staging", "prod"})

COUNT_METRICS = frozenset({
    "ActivatedUser",
    "WeeklyActiveUser",
    "FirstEntryCreated",
    "OcrStarted",
    "OcrCompleted",
    "OcrFailed",
    "FirstAnalysisCompleted",
    "FirstGroundedAskCompleted",
    "SecondWeekReturned",
    "AccountExportRequested",
    "AccountExportCompleted",
    "AccountExportFailed",
    "AccountDeletionRequested",
    "AccountDeletionCompleted",
    "AccountDeletionFailed",
    "CheckoutStarted",
    "ProEntitlementActivated",
    "CancellationRequested",
    "MeaningfulInsightYes",
    "MeaningfulInsightNo",
    "TelemetryEmissionFailed",
})

DURATION_METRICS = frozenset({
    "TimeToFirstMeaningfulInsightMs",
})

ALL_METRICS = COUNT_METRICS | DURATION_METRICS

ONCE_PER_USER_METRICS = frozenset({
    "ActivatedUser",
    "FirstEntryCreated",
    "FirstAnalysisCompleted",
    "FirstGroundedAskCompleted",
    "SecondWeekReturned",
    "ProEntitlementActivated",
    "TimeToFirstMeaningfulInsightMs",
})

PERIODIC_METRICS = frozenset({"WeeklyActiveUser"})
DEDUPLICATED_METRICS = ONCE_PER_USER_METRICS | PERIODIC_METRICS

_WEEK_PATTERN = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
_MAX_MEANINGFUL_INSIGHT_MS = 90 * 24 * 60 * 60 * 1000


class ProductTelemetryContractError(ValueError):
    """Raised when product telemetry would violate the alpha contract."""


def normalize_stage(value: object) -> str:
    if not isinstance(value, str):
        raise ProductTelemetryContractError("stage must be a string")
    stage = value.strip().casefold()
    if stage not in ALLOWED_STAGES:
        raise ProductTelemetryContractError("stage is not allowed")
    return stage


def metric_namespace(stage: object) -> str:
    return f"JM8/{normalize_stage(stage)}/Product"


def metric_unit(metric_name: object) -> str:
    _validate_metric_name(metric_name)
    return "Milliseconds" if metric_name in DURATION_METRICS else "Count"


def build_metric_event(
    stage: object,
    metric_name: object,
    *,
    value: object = 1,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Build one identifier-free CloudWatch Embedded Metric Format event.

    The function intentionally accepts no dimensions or metadata. Callers
    cannot accidentally attach user, entry, journal, billing, or workflow
    identifiers to a product metric.
    """

    normalized_stage = normalize_stage(stage)
    name = _validate_metric_name(metric_name)
    normalized_value = _validate_metric_value(name, value)
    instant = _normalize_instant(occurred_at)
    unit = metric_unit(name)

    return {
        "_aws": {
            "Timestamp": int(instant.timestamp() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": metric_namespace(normalized_stage),
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        "eventType": EVENT_TYPE,
        "telemetryVersion": TELEMETRY_VERSION,
        "stage": normalized_stage,
        "metricName": name,
        name: normalized_value,
    }


def serialize_metric_event(event: Mapping[str, object]) -> str:
    """Serialize only an event that exactly matches this contract."""

    validate_metric_event(event)
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def validate_metric_event(event: Mapping[str, object]) -> None:
    if not isinstance(event, Mapping):
        raise ProductTelemetryContractError("metric event must be a mapping")

    name = _validate_metric_name(event.get("metricName"))
    expected = {"_aws", "eventType", "telemetryVersion", "stage", "metricName", name}
    if set(event) != expected:
        raise ProductTelemetryContractError("metric event fields are not exact")
    if event.get("eventType") != EVENT_TYPE:
        raise ProductTelemetryContractError("event type is invalid")
    if event.get("telemetryVersion") != TELEMETRY_VERSION:
        raise ProductTelemetryContractError("telemetry version is invalid")

    stage = normalize_stage(event.get("stage"))
    value = _validate_metric_value(name, event.get(name))
    expected_event = build_metric_event(
        stage,
        name,
        value=value,
        occurred_at=_event_instant(event.get("_aws")),
    )
    if dict(event) != expected_event:
        raise ProductTelemetryContractError("metric event structure is invalid")


def milestone_sort_key(metric_name: object, *, period: object = None) -> str:
    """Return the user-partition key used for conditional milestone writes."""

    name = _validate_metric_name(metric_name)
    if name in ONCE_PER_USER_METRICS:
        if period is not None:
            raise ProductTelemetryContractError("one-time milestone cannot have a period")
        return f"PRODUCT_MILESTONE#{name}"
    if name in PERIODIC_METRICS:
        if not isinstance(period, str) or _WEEK_PATTERN.fullmatch(period) is None:
            raise ProductTelemetryContractError("weekly milestone period is invalid")
        return f"PRODUCT_MILESTONE#{name}#{period}"
    raise ProductTelemetryContractError("metric is not a deduplicated milestone")


def utc_week(value: datetime | None = None) -> str:
    instant = _normalize_instant(value)
    year, week, _ = instant.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _validate_metric_name(value: object) -> str:
    if not isinstance(value, str) or value not in ALL_METRICS:
        raise ProductTelemetryContractError("metric name is not allowed")
    return value


def _validate_metric_value(metric_name: str, value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ProductTelemetryContractError("metric value must be numeric")
    if metric_name in COUNT_METRICS:
        if value != 1:
            raise ProductTelemetryContractError("count events must have value one")
        return 1
    numeric = float(value)
    if not 0 < numeric <= _MAX_MEANINGFUL_INSIGHT_MS:
        raise ProductTelemetryContractError("duration is outside the allowed range")
    return int(numeric) if numeric.is_integer() else numeric


def _normalize_instant(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ProductTelemetryContractError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _event_instant(value: object) -> datetime:
    if not isinstance(value, Mapping):
        raise ProductTelemetryContractError("embedded metric directive is invalid")
    timestamp = value.get("Timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise ProductTelemetryContractError("embedded metric timestamp is invalid")
    return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)


__all__ = [
    "ALL_METRICS",
    "COUNT_METRICS",
    "DEDUPLICATED_METRICS",
    "DURATION_METRICS",
    "ONCE_PER_USER_METRICS",
    "PERIODIC_METRICS",
    "ProductTelemetryContractError",
    "TELEMETRY_VERSION",
    "build_metric_event",
    "metric_namespace",
    "metric_unit",
    "milestone_sort_key",
    "normalize_stage",
    "serialize_metric_event",
    "utc_week",
    "validate_metric_event",
]
