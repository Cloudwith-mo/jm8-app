#!/usr/bin/env python3
"""Build the privacy-safe JM8 semantic pipeline CloudWatch dashboard."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


DASHBOARD_VERSION = "jm8-semantic-pipeline-dashboard-v1"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_METRIC_NAMESPACE = "JournalM8/SemanticEmbedding"
VECTOR_INDEX_NAME = "SemanticEmbeddingIndex"

_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")
_ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")


class SemanticPipelineDashboardError(ValueError):
    """Raised when dashboard input or output violates the exact contract."""


def extract_inventory(
    table_document: object,
    *,
    table_name: str,
    table_arn: str,
) -> tuple[int | None, int | None]:
    """Return privacy-safe DynamoDB item-count estimates after identity checks."""

    if not isinstance(table_document, Mapping):
        raise SemanticPipelineDashboardError("table description is malformed")
    table = table_document.get("Table")
    if not isinstance(table, Mapping):
        raise SemanticPipelineDashboardError("table description is malformed")
    if (
        table.get("TableName") != table_name
        or table.get("TableArn") != table_arn
        or table.get("TableStatus") != "ACTIVE"
    ):
        raise SemanticPipelineDashboardError("table identity or state is invalid")

    indexes = table.get("VectorIndexes")
    if (
        not isinstance(indexes, list)
        or len(indexes) != 1
        or not isinstance(indexes[0], Mapping)
        or indexes[0].get("IndexName") != VECTOR_INDEX_NAME
        or indexes[0].get("IndexStatus") != "ACTIVE"
    ):
        raise SemanticPipelineDashboardError("vector index is not ready")

    return (
        _optional_count(table.get("ItemCount"), "table item estimate"),
        _optional_count(indexes[0].get("ItemCount"), "index item estimate"),
    )


def build_semantic_pipeline_dashboard(
    *,
    region: str,
    account_id: str,
    app_name: str,
    stage: str,
    entry_chunks_item_count: int | None,
    vector_index_item_count: int | None,
) -> dict[str, Any]:
    """Build one deterministic dashboard from privacy-safe aggregate metrics."""

    _validate_context(region, account_id, app_name, stage)
    table_count = _count_label(entry_chunks_item_count)
    index_count = _count_label(vector_index_item_count)

    prefix = f"{app_name}-{stage}"
    api_function = f"{prefix}-api"
    memory_function = f"{prefix}-semantic-memory-worker"
    embedding_function = f"{prefix}-semantic-embedding-worker"
    main_table_name = f"{prefix}-main"
    memory_dlq = f"{prefix}-semantic-memory-dlq"
    embedding_dlq = f"{prefix}-semantic-embedding-dlq"
    table_name = f"{prefix}-entry-chunks"

    alarm_names = [
        f"{memory_function}-errors",
        f"{memory_function}-throttles",
        f"{memory_function}-iterator-age",
        f"{memory_dlq}-visible-messages",
        f"{embedding_function}-errors",
        f"{embedding_function}-throttles",
        f"{embedding_function}-iterator-age",
        f"{embedding_function}-record-failures",
        f"{embedding_dlq}-visible-messages",
    ]
    alarm_arns = [
        f"arn:aws:cloudwatch:{region}:{account_id}:alarm:{name}"
        for name in alarm_names
    ]

    dashboard: dict[str, Any] = {
        "start": "-PT24H",
        "periodOverride": "inherit",
        "widgets": [
            {
                "type": "text",
                "x": 0,
                "y": 0,
                "width": 24,
                "height": 4,
                "properties": {
                    "markdown": (
                        f"# JM8 Semantic Pipeline — {stage}\n"
                        f"Contract: `{DASHBOARD_VERSION}`. "
                        "Green means work is flowing without failure signals. "
                        "Empty graphs mean no work occurred in the selected period.\n\n"
                        "**Flow:** New or edited entry → main table → memory "
                        "worker → EntryChunks → embedding worker → Bedrock → "
                        "vector index → Ask JM8.\n\n"
                        f"**Inventory snapshot at dashboard deployment:** EntryChunks "
                        f"item estimate `{table_count}`; vector-index item estimate "
                        f"`{index_count}`. DynamoDB item counts are approximate "
                        "and the "
                        "table also contains non-vector control records, so these two "
                        "numbers are not presented as an exact coverage percentage."
                    ),
                    "background": "solid",
                },
            },
            {
                "type": "alarm",
                "x": 0,
                "y": 4,
                "width": 24,
                "height": 6,
                "properties": {
                    "title": "Semantic pipeline safety alarms",
                    "alarms": alarm_arns,
                },
            },
            {
                "type": "metric",
                "x": 0,
                "y": 10,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "Pipeline throughput",
                    "region": region,
                    "view": "timeSeries",
                    "period": 300,
                    "metrics": [
                        ["AWS/Lambda", "Invocations", "FunctionName", api_function,
                         {"stat": "Sum", "label": "API invocations (all routes)"}],
                        ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName",
                         main_table_name,
                         {"stat": "Sum", "label": "Main-table writes"}],
                        ["AWS/Lambda", "Invocations", "FunctionName", memory_function,
                         {"stat": "Sum", "label": "Memory batches"}],
                        ["AWS/Lambda", "Invocations", "FunctionName",
                         embedding_function,
                         {"stat": "Sum", "label": "Embedding batches"}],
                        [EMBEDDING_METRIC_NAMESPACE, "RecordCount", "FunctionName",
                         embedding_function,
                         {"stat": "Sum", "label": "Embedding records"}],
                    ],
                },
            },
            {
                "type": "metric",
                "x": 12,
                "y": 10,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "Embedding record success rate",
                    "region": region,
                    "view": "timeSeries",
                    "period": 300,
                    "yAxis": {"left": {"min": 0, "max": 100}},
                    "metrics": [
                        [{
                            "expression": "100*(records-failures)/records",
                            "label": "Successful records (%)",
                            "id": "success",
                        }],
                        [EMBEDDING_METRIC_NAMESPACE, "RecordCount", "FunctionName",
                         embedding_function,
                         {"id": "records", "stat": "Sum", "visible": False}],
                        [EMBEDDING_METRIC_NAMESPACE, "RecordFailures", "FunctionName",
                         embedding_function,
                         {"id": "failures", "stat": "Sum", "visible": False}],
                    ],
                },
            },
            {
                "type": "metric",
                "x": 0,
                "y": 16,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "Worker processing time",
                    "region": region,
                    "view": "timeSeries",
                    "period": 300,
                    "metrics": [
                        ["AWS/Lambda", "Duration", "FunctionName", memory_function,
                         {"stat": "Average", "label": "Memory average (ms)"}],
                        ["AWS/Lambda", "Duration", "FunctionName", memory_function,
                         {"stat": "p95", "label": "Memory p95 (ms)"}],
                        ["AWS/Lambda", "Duration", "FunctionName", embedding_function,
                         {"stat": "Average", "label": "Embedding average (ms)"}],
                        ["AWS/Lambda", "Duration", "FunctionName", embedding_function,
                         {"stat": "p95", "label": "Embedding p95 (ms)"}],
                    ],
                },
            },
            {
                "type": "metric",
                "x": 12,
                "y": 16,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "Retry, backlog, and DLQ pressure",
                    "region": region,
                    "view": "timeSeries",
                    "period": 300,
                    "metrics": [
                        ["AWS/Lambda", "IteratorAge", "FunctionName", memory_function,
                         {"stat": "Maximum", "label": "Memory iterator age (ms)"}],
                        ["AWS/Lambda", "IteratorAge", "FunctionName",
                         embedding_function,
                         {"stat": "Maximum", "label": "Embedding iterator age (ms)"}],
                        [EMBEDDING_METRIC_NAMESPACE, "RecordFailures", "FunctionName",
                         embedding_function,
                         {"stat": "Sum", "label": "Failed records"}],
                        ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName",
                         memory_dlq, {"stat": "Maximum", "label": "Memory DLQ"}],
                        ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName",
                         embedding_dlq,
                         {"stat": "Maximum", "label": "Embedding DLQ"}],
                    ],
                },
            },
            {
                "type": "metric",
                "x": 0,
                "y": 22,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "Embedding failure boundary",
                    "region": region,
                    "view": "timeSeries",
                    "period": 300,
                    "metrics": [
                        [EMBEDDING_METRIC_NAMESPACE, name, "FunctionName",
                         embedding_function, {"stat": "Sum", "label": label}]
                        for name, label in (
                            ("MemoryReadFailures", "Memory read"),
                            ("ProviderFailures", "Bedrock provider"),
                            ("PersistenceFailures", "DynamoDB persistence"),
                            ("ContractFailures", "Record contract"),
                            ("UnexpectedFailures", "Unexpected"),
                        )
                    ],
                },
            },
            {
                "type": "metric",
                "x": 12,
                "y": 22,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "Bedrock request health",
                    "region": region,
                    "view": "timeSeries",
                    "period": 300,
                    "metrics": [
                        ["AWS/Bedrock", name, "ModelId", EMBEDDING_MODEL_ID,
                         {"stat": "Sum", "label": label}]
                        for name, label in (
                            ("Invocations", "Invocations"),
                            ("InvocationClientErrors", "Client errors"),
                            ("InvocationServerErrors", "Server errors"),
                            ("InvocationThrottles", "Throttles"),
                        )
                    ],
                },
            },
            {
                "type": "metric",
                "x": 0,
                "y": 28,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "Bedrock token volume and latency",
                    "region": region,
                    "view": "timeSeries",
                    "period": 300,
                    "metrics": [
                        ["AWS/Bedrock", "InputTokenCount", "ModelId",
                         EMBEDDING_MODEL_ID,
                         {"stat": "Sum", "label": "Input tokens"}],
                        ["AWS/Bedrock", "InvocationLatency", "ModelId",
                         EMBEDDING_MODEL_ID,
                         {"stat": "Average", "label": "Average latency (ms)"}],
                        ["AWS/Bedrock", "InvocationLatency", "ModelId",
                         EMBEDDING_MODEL_ID,
                         {"stat": "p95", "label": "p95 latency (ms)"}],
                    ],
                },
            },
            {
                "type": "metric",
                "x": 12,
                "y": 28,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "Bedrock account estimated charges",
                    "region": "us-east-1",
                    "view": "singleValue",
                    "period": 21600,
                    "metrics": [[
                        "AWS/Billing",
                        "EstimatedCharges",
                        "Currency",
                        "USD",
                        "ServiceName",
                        "AmazonBedrock",
                        {
                            "stat": "Maximum",
                            "label": (
                                "Account-wide Bedrock estimate (USD; delayed)"
                            ),
                        },
                    ]],
                },
            },
            {
                "type": "metric",
                "x": 0,
                "y": 34,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": "EntryChunks DynamoDB activity",
                    "region": region,
                    "view": "timeSeries",
                    "period": 300,
                    "metrics": [
                        ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName",
                         table_name, {"stat": "Sum", "label": "Reads"}],
                        ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName",
                         table_name, {"stat": "Sum", "label": "Writes"}],
                        ["AWS/DynamoDB", "TransactionConflict", "TableName",
                         table_name,
                         {"stat": "Sum", "label": "Transaction conflicts"}],
                        ["AWS/DynamoDB", "ConditionalCheckFailedRequests",
                         "TableName", table_name,
                         {"stat": "Sum", "label": "Conditional rejections"}],
                        ["AWS/DynamoDB", "WriteThrottleEvents", "TableName",
                         table_name,
                         {"stat": "Sum", "label": "Write throttles"}],
                    ],
                },
            },
            {
                "type": "text",
                "x": 12,
                "y": 34,
                "width": 12,
                "height": 6,
                "properties": {
                    "markdown": (
                        "## Reading coverage correctly\n"
                        "- **Record success %** is the live processing coverage: "
                        "records completed without a reported failure.\n"
                        "- **DLQ = 0** means retries did not end in quarantine.\n"
                        "- **Healthy:** all alarms green, 100% record success, "
                        "near-zero "
                        "iterator age, and empty DLQs.\n"
                        "- **Needs attention:** any red alarm, falling success, "
                        "growing "
                        "iterator age, throttles, or DLQ depth.\n"
                        "- **Vector index item estimate** is an approximate inventory "
                        "snapshot, not an exact percentage.\n"
                        "- Exact historical coverage requires the separate controlled "
                        "backfill/audit step; this dashboard never scans journal text."
                    ),
                },
            },
        ],
    }
    validate_dashboard(dashboard, expected_alarm_arns=set(alarm_arns))
    return dashboard


def validate_dashboard(
    dashboard: object,
    *,
    expected_alarm_arns: set[str],
) -> None:
    """Reject malformed, content-reading, or incomplete dashboard definitions."""

    if not isinstance(dashboard, Mapping):
        raise SemanticPipelineDashboardError("dashboard is malformed")
    widgets = dashboard.get("widgets")
    if not isinstance(widgets, list) or len(widgets) != 12:
        raise SemanticPipelineDashboardError("dashboard widgets are incomplete")
    if any(
        not isinstance(widget, Mapping)
        or widget.get("type") not in {"text", "metric", "alarm"}
        for widget in widgets
    ):
        raise SemanticPipelineDashboardError(
            "dashboard contains an unsupported widget"
        )
    alarm_widgets = [widget for widget in widgets if widget.get("type") == "alarm"]
    if len(alarm_widgets) != 1:
        raise SemanticPipelineDashboardError("dashboard alarm widget is invalid")
    alarms = alarm_widgets[0].get("properties", {}).get("alarms")
    if not isinstance(alarms, list) or set(alarms) != expected_alarm_arns:
        raise SemanticPipelineDashboardError("dashboard alarms are incomplete")
    serialized = json.dumps(dashboard, sort_keys=True)
    for forbidden in ("SOURCE '", "@message", "logGroupNames", "journalText"):
        if forbidden in serialized:
            raise SemanticPipelineDashboardError(
                "dashboard could expose journal or log content"
            )
    required = (
        "RecordCount",
        "RecordFailures",
        "PersistenceFailures",
        "ApproximateNumberOfMessagesVisible",
        "InvocationLatency",
        "InputTokenCount",
        "EstimatedCharges",
        "ConsumedWriteCapacityUnits",
        "TransactionConflict",
    )
    if any(name not in serialized for name in required):
        raise SemanticPipelineDashboardError(
            "dashboard metrics are incomplete"
        )


def dashboard_name(app_name: str, stage: str) -> str:
    _name(app_name, "application name")
    _name(stage, "stage")
    return f"{app_name}-{stage}-semantic-pipeline"


def _validate_context(
    region: str,
    account_id: str,
    app_name: str,
    stage: str,
) -> None:
    if not _REGION_PATTERN.fullmatch(region):
        raise SemanticPipelineDashboardError("region is malformed")
    if not _ACCOUNT_PATTERN.fullmatch(account_id):
        raise SemanticPipelineDashboardError("account ID is malformed")
    _name(app_name, "application name")
    if stage not in {"dev", "staging", "prod"}:
        raise SemanticPipelineDashboardError("stage is invalid")


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not _NAME_PATTERN.fullmatch(value):
        raise SemanticPipelineDashboardError(f"{label} is malformed")
    return value


def _optional_count(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SemanticPipelineDashboardError(f"{label} is invalid")
    return value


def _count_label(value: int | None) -> str:
    checked = _optional_count(value, "inventory estimate")
    return "unavailable" if checked is None else str(checked)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--table-document", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        table_document = json.loads(
            args.table_document.read_text(encoding="utf-8")
        )
        table_name = f"{args.app_name}-{args.stage}-entry-chunks"
        table_arn = (
            f"arn:aws:dynamodb:{args.region}:{args.account_id}:table/{table_name}"
        )
        table_count, index_count = extract_inventory(
            table_document,
            table_name=table_name,
            table_arn=table_arn,
        )
        dashboard = build_semantic_pipeline_dashboard(
            region=args.region,
            account_id=args.account_id,
            app_name=args.app_name,
            stage=args.stage,
            entry_chunks_item_count=table_count,
            vector_index_item_count=index_count,
        )
    except (OSError, json.JSONDecodeError, SemanticPipelineDashboardError):
        print("Semantic pipeline dashboard input is invalid.", file=sys.stderr)
        return 1
    print(json.dumps(dashboard, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
