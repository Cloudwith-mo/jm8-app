#!/usr/bin/env python3
"""Build the stage-isolated, content-free JM8 alpha telemetry dashboard."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from product_telemetry_contract import ALL_METRICS, metric_namespace  # noqa: E402


DASHBOARD_VERSION = "jm8-alpha-telemetry-dashboard-v1"
ALLOWED_STAGES = frozenset({"dev", "staging", "prod"})
_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")
_ACCOUNT = re.compile(r"^\d{12}$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class ProductTelemetryDashboardError(ValueError):
    """Raised when the dashboard would violate the release contract."""


def dashboard_name(app_name: str, stage: str) -> str:
    _validate_context("us-east-1", "000000000000", app_name, stage)
    return f"{app_name}-{stage}-alpha-telemetry"


def alarm_name(app_name: str, stage: str) -> str:
    _validate_context("us-east-1", "000000000000", app_name, stage)
    return f"{app_name}-{stage}-product-telemetry-emission-failures"


def alarm_arn(region: str, account_id: str, app_name: str, stage: str) -> str:
    _validate_context(region, account_id, app_name, stage)
    return f"arn:aws:cloudwatch:{region}:{account_id}:alarm:{alarm_name(app_name, stage)}"


def _metric(namespace: str, name: str, label: str, *, stat: str = "Sum") -> list[Any]:
    return [namespace, name, {"label": label, "stat": stat}]


def _metric_widget(title: str, metrics: list[list[Any]], *, x: int, y: int) -> dict[str, Any]:
    return {
        "type": "metric",
        "x": x,
        "y": y,
        "width": 12,
        "height": 6,
        "properties": {
            "title": title,
            "region": "__REGION__",
            "view": "timeSeries",
            "stacked": False,
            "period": 300,
            "metrics": metrics,
        },
    }


def build_product_telemetry_dashboard(
    *, region: str, account_id: str, app_name: str, stage: str
) -> dict[str, Any]:
    _validate_context(region, account_id, app_name, stage)
    namespace = metric_namespace(stage)
    widgets: list[dict[str, Any]] = [
        {
            "type": "text", "x": 0, "y": 0, "width": 24, "height": 4,
            "properties": {"markdown": (
                f"# JM8 Alpha Telemetry — {stage}\n\n"
                f"Contract: `{DASHBOARD_VERSION}`. Aggregate counts only; no dimensions, "
                "identifiers, journal content, prompts, answers, or evidence. "
                "For a five-person alpha, interpret trends directionally and never infer "
                "individual behavior. Synthetic canaries belong in staging."
            )},
        },
        {
            "type": "alarm", "x": 0, "y": 4, "width": 24, "height": 2,
            "properties": {
                "title": "Alpha telemetry safety alarm",
                "alarms": [alarm_arn(region, account_id, app_name, stage)],
            },
        },
        _metric_widget("Activation and retention", [
            _metric(namespace, "ActivatedUser", "Activated users"),
            _metric(namespace, "FirstEntryCreated", "First entries"),
            _metric(namespace, "WeeklyActiveUser", "Weekly active users"),
            _metric(namespace, "SecondWeekReturned", "Second-week returns"),
        ], x=0, y=6),
        _metric_widget("Intelligence value funnel", [
            _metric(namespace, "FirstAnalysisCompleted", "First analyses"),
            _metric(namespace, "FirstGroundedAskCompleted", "First grounded Ask answers"),
            _metric(namespace, "MeaningfulInsightYes", "Meaningful: yes"),
            _metric(namespace, "MeaningfulInsightNo", "Meaningful: no"),
            _metric(namespace, "TimeToFirstMeaningfulInsightMs", "Time to first insight", stat="Average"),
        ], x=12, y=6),
        _metric_widget("OCR journey", [
            _metric(namespace, "OcrStarted", "Started"),
            _metric(namespace, "OcrCompleted", "Completed"),
            _metric(namespace, "OcrFailed", "Failed"),
        ], x=0, y=12),
        _metric_widget("Data-rights workflows", [
            _metric(namespace, "AccountExportRequested", "Export requested"),
            _metric(namespace, "AccountExportCompleted", "Export completed"),
            _metric(namespace, "AccountExportFailed", "Export failed"),
            _metric(namespace, "AccountDeletionRequested", "Deletion requested"),
            _metric(namespace, "AccountDeletionCompleted", "Deletion completed"),
            _metric(namespace, "AccountDeletionFailed", "Deletion failed"),
        ], x=12, y=12),
        _metric_widget("Billing journey", [
            _metric(namespace, "CheckoutStarted", "Checkout started"),
            _metric(namespace, "ProEntitlementActivated", "Pro activated"),
            _metric(namespace, "CancellationRequested", "Cancellation requested"),
        ], x=0, y=18),
        _metric_widget("Telemetry delivery", [
            _metric(namespace, "TelemetryEmissionFailed", "Emission failures"),
        ], x=12, y=18),
    ]
    for widget in widgets:
        if widget["type"] == "metric":
            widget["properties"]["region"] = region
    document = {"start": "-P14D", "periodOverride": "inherit", "widgets": widgets}
    validate_dashboard(document, expected_namespace=namespace,
                       expected_alarm_arn=alarm_arn(region, account_id, app_name, stage))
    return document


def validate_dashboard(document: object, *, expected_namespace: str,
                       expected_alarm_arn: str) -> None:
    if not isinstance(document, dict) or set(document) != {"start", "periodOverride", "widgets"}:
        raise ProductTelemetryDashboardError("dashboard document is not exact")
    widgets = document.get("widgets")
    if not isinstance(widgets, list) or len(widgets) != 8:
        raise ProductTelemetryDashboardError("dashboard must contain exactly eight widgets")
    if [item.get("type") for item in widgets].count("alarm") != 1:
        raise ProductTelemetryDashboardError("dashboard must contain exactly one alarm widget")
    if any(item.get("type") not in {"text", "metric", "alarm"} for item in widgets):
        raise ProductTelemetryDashboardError("dashboard widget type is forbidden")
    alarms = [value for item in widgets if item.get("type") == "alarm"
              for value in item.get("properties", {}).get("alarms", [])]
    if alarms != [expected_alarm_arn]:
        raise ProductTelemetryDashboardError("dashboard alarm identity is invalid")
    names: list[str] = []
    for widget in widgets:
        if widget.get("type") != "metric":
            continue
        for metric in widget.get("properties", {}).get("metrics", []):
            if not isinstance(metric, list) or len(metric) != 3:
                raise ProductTelemetryDashboardError("metric definition is not exact")
            namespace, name, options = metric
            if namespace != expected_namespace or not isinstance(options, dict):
                raise ProductTelemetryDashboardError("metric namespace is not exact")
            if "dimensions" in {key.casefold() for key in options}:
                raise ProductTelemetryDashboardError("metric dimensions are forbidden")
            names.append(name)
    if len(names) != len(set(names)) or set(names) != set(ALL_METRICS):
        raise ProductTelemetryDashboardError("dashboard metric registry is not exact")
    serialized = json.dumps(document, sort_keys=True).casefold()
    for forbidden in (
        "userid", "entryid", "email", "journaltext", "rawtext", "cleantext",
        "@message", "loggroupnames", "source '", "questiontext", "answertext",
    ):
        if forbidden in serialized:
            raise ProductTelemetryDashboardError("dashboard contains private or log data")


def _validate_context(region: object, account_id: object,
                      app_name: object, stage: object) -> None:
    if not isinstance(region, str) or _REGION.fullmatch(region) is None:
        raise ProductTelemetryDashboardError("region is invalid")
    if not isinstance(account_id, str) or _ACCOUNT.fullmatch(account_id) is None:
        raise ProductTelemetryDashboardError("account is invalid")
    if not isinstance(app_name, str) or _NAME.fullmatch(app_name) is None:
        raise ProductTelemetryDashboardError("application name is invalid")
    if not isinstance(stage, str) or stage not in ALLOWED_STAGES:
        raise ProductTelemetryDashboardError("stage is invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--stage", required=True)
    arguments = parser.parse_args()
    try:
        document = build_product_telemetry_dashboard(
            region=arguments.region, account_id=arguments.account_id,
            app_name=arguments.app_name, stage=arguments.stage,
        )
    except ProductTelemetryDashboardError as error:
        parser.error(str(error))
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
