"""Failure-isolated emission for privacy-safe JM8 product metrics."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from product_telemetry_contract import (
    ProductTelemetryContractError,
    build_metric_event,
    serialize_metric_event,
)


MetricSink = Callable[[str], Any]


def emit_product_metric(
    stage: object,
    metric_name: object,
    *,
    value: object = 1,
    occurred_at: datetime | None = None,
    sink: MetricSink = print,
    failure_sink: MetricSink | None = None,
) -> dict[str, object]:
    """Emit one exact product metric without exposing caller metadata.

    Contract errors are programming errors and fail closed. Sink errors are
    operational measurement failures and are returned as safe status values so
    the user's successful product action remains successful.
    """

    event = build_metric_event(
        stage,
        metric_name,
        value=value,
        occurred_at=occurred_at,
    )
    serialized = serialize_metric_event(event)

    try:
        sink(serialized)
    except Exception:
        _emit_failure_metric(
            stage,
            occurred_at=occurred_at,
            sink=failure_sink or sink,
        )
        return {
            "status": "FAILED",
            "metricName": event["metricName"],
            "errorCode": "TelemetryEmissionUnavailable",
        }

    return {
        "status": "EMITTED",
        "metricName": event["metricName"],
    }


def _emit_failure_metric(
    stage: object,
    *,
    occurred_at: datetime | None,
    sink: MetricSink,
) -> None:
    try:
        failure = build_metric_event(
            stage,
            "TelemetryEmissionFailed",
            occurred_at=occurred_at,
        )
        sink(serialize_metric_event(failure))
    except (Exception, ProductTelemetryContractError):
        # No exception from observability may escape into the product action.
        return


__all__ = ["MetricSink", "emit_product_metric"]
