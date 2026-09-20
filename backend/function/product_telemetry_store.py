"""Concurrency-safe, user-partition-local JM8 product milestones."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from product_telemetry import MetricSink, emit_product_metric
from product_telemetry_contract import (
    DEDUPLICATED_METRICS,
    ProductTelemetryContractError,
    build_metric_event,
    milestone_sort_key,
    utc_week,
)
def record_product_milestone(
    user_id: object,
    stage: object,
    metric_name: object,
    *,
    value: object = 1,
    occurred_at: datetime | None = None,
    period: object = None,
    table_resource: Any = None,
    sink: MetricSink = print,
    failure_sink: MetricSink | None = None,
) -> dict[str, object]:
    """Conditionally store and emit a first-time or weekly milestone.

    Invalid input raises before storage. DynamoDB and metric-sink failures are
    converted to safe results so callers do not fail an otherwise successful
    user action because alpha measurement is unavailable.
    """

    normalized_user_id = _normalize_user_id(user_id)
    event = build_metric_event(
        stage,
        metric_name,
        value=value,
        occurred_at=occurred_at,
    )
    name = str(event["metricName"])
    if name not in DEDUPLICATED_METRICS:
        raise ProductTelemetryContractError(
            "metric is not a deduplicated milestone"
        )

    instant = _normalize_instant(occurred_at)
    normalized_period = _normalized_period(name, period, instant)
    sort_key = milestone_sort_key(name, period=normalized_period)
    item: dict[str, object] = {
        "PK": _user_pk(normalized_user_id),
        "SK": sort_key,
        "entityType": "PRODUCT_MILESTONE",
        "telemetryVersion": event["telemetryVersion"],
        "metricName": name,
        "metricValue": event[name],
        "recordedAt": instant.isoformat().replace("+00:00", "Z"),
    }
    if normalized_period is not None:
        item["period"] = normalized_period

    resource = table_resource or _default_table()
    try:
        resource.put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )
    except Exception as error:
        if _client_error_code(error) == "ConditionalCheckFailedException":
            return {
                "status": "ALREADY_RECORDED",
                "metricName": name,
            }
        emit_product_metric(
            stage,
            "TelemetryEmissionFailed",
            occurred_at=instant,
            sink=failure_sink or sink,
            failure_sink=failure_sink,
        )
        return {
            "status": "STORE_FAILED",
            "metricName": name,
            "errorCode": "ProductMilestoneStoreUnavailable",
        }
    emitted = emit_product_metric(
        stage,
        name,
        value=value,
        occurred_at=instant,
        sink=sink,
        failure_sink=failure_sink,
    )
    if emitted["status"] != "EMITTED":
        return {
            "status": "RECORDED_EMISSION_FAILED",
            "metricName": name,
            "errorCode": emitted["errorCode"],
        }
    return {
        "status": "RECORDED_AND_EMITTED",
        "metricName": name,
    }


def _normalize_user_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductTelemetryContractError("user ID must be a non-empty string")
    return value.strip()


def _normalize_instant(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ProductTelemetryContractError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _normalized_period(
    metric_name: str,
    value: object,
    occurred_at: datetime,
) -> str | None:
    if metric_name == "WeeklyActiveUser":
        expected = utc_week(occurred_at)
        if value is None:
            return expected
        if value != expected:
            raise ProductTelemetryContractError(
                "weekly milestone period does not match the timestamp"
            )
        return expected
    if value is not None:
        raise ProductTelemetryContractError(
            "one-time milestone cannot have a period"
        )
    return None


def _default_table() -> Any:
    from storage import table as application_table

    return application_table


def _user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _client_error_code(error: Exception) -> str:
    candidate = getattr(error, "response", None)
    response = candidate if isinstance(candidate, dict) else {}
    details = response.get("Error")
    if not isinstance(details, dict):
        return ""
    value = details.get("Code")
    return value if isinstance(value, str) else ""


__all__ = ["record_product_milestone"]
