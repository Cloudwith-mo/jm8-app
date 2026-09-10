"""Lambda entry point for EntryChunks semantic-embedding lifecycle events."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any

import boto3

from semantic_embedding_lifecycle import (
    build_embedding_telemetry_document,
    process_embedding_stream_event_with_telemetry,
)


class SemanticEmbeddingWorkerConfigurationError(RuntimeError):
    """Raised when the worker's cold-start configuration is invalid."""


def _required_table_name() -> str:
    table_name = os.environ.get("ENTRY_CHUNKS_TABLE_NAME")
    if not isinstance(table_name, str) or not table_name.strip():
        raise SemanticEmbeddingWorkerConfigurationError(
            "semantic embedding worker configuration is invalid"
        )
    return table_name


ENTRY_CHUNKS_TABLE_NAME = _required_table_name()
entry_chunks_table = boto3.resource("dynamodb").Table(ENTRY_CHUNKS_TABLE_NAME)
bedrock_client = boto3.client("bedrock-runtime")


def lambda_handler(event: Mapping[str, object], context: object) -> dict[str, Any]:
    """Delegate the batch contract and emit privacy-safe aggregate telemetry."""

    response, telemetry = process_embedding_stream_event_with_telemetry(
        entry_chunks_table,
        bedrock_client,
        event,
    )
    function_name = os.environ.get(
        "AWS_LAMBDA_FUNCTION_NAME",
        "semantic-embedding-worker",
    )
    print(json.dumps(
        build_embedding_telemetry_document(
            telemetry,
            function_name=function_name,
            timestamp_ms=int(time.time() * 1000),
        ),
        sort_keys=True,
        separators=(",", ":"),
    ))
    return response


__all__ = [
    "SemanticEmbeddingWorkerConfigurationError",
    "lambda_handler",
]
