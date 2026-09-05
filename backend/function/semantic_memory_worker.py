"""Lambda entry point for main-table semantic-memory lifecycle events."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import boto3

from semantic_memory_lifecycle import process_entry_stream_event


class SemanticMemoryWorkerConfigurationError(RuntimeError):
    """Raised when the worker's cold-start configuration is invalid."""


def _required_table_name() -> str:
    table_name = os.environ.get("ENTRY_CHUNKS_TABLE_NAME")
    if not isinstance(table_name, str) or not table_name.strip():
        raise SemanticMemoryWorkerConfigurationError(
            "semantic memory worker configuration is invalid"
        )
    return table_name


ENTRY_CHUNKS_TABLE_NAME = _required_table_name()
entry_chunks_table = boto3.resource("dynamodb").Table(ENTRY_CHUNKS_TABLE_NAME)


def lambda_handler(event: Mapping[str, object], context: object) -> dict[str, Any]:
    """Delegate the complete batch contract and emit aggregate safe telemetry."""

    response = process_entry_stream_event(entry_chunks_table, event)
    failures = response["batchItemFailures"]
    records = event.get("Records") if isinstance(event, Mapping) else None
    processed_count = len(records) if isinstance(records, list) else 0
    print(json.dumps({
        "event": "SemanticMemoryLifecycleBatch",
        "processedCount": processed_count,
        "failedCount": len(failures),
    }, sort_keys=True, separators=(",", ":")))
    return response


__all__ = [
    "SemanticMemoryWorkerConfigurationError",
    "lambda_handler",
]
