"""DynamoDB Streams orchestration for semantic chunk embeddings.

Deployment must configure the event-source mapping with
``FunctionResponseTypes=["ReportBatchItemFailures"]`` so Lambda honors the
partial-batch response returned by :func:`process_embedding_stream_event`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NotRequired, TypedDict

from boto3.dynamodb.types import TypeDeserializer

from semantic_embedding_contract import (
    SemanticEmbeddingContractError,
    embedding_is_current,
)
from semantic_embedding_provider import (
    SemanticEmbeddingProviderError,
    embed_canonical_text,
)
from semantic_embedding_store import (
    SemanticEmbeddingPersistenceError,
    SemanticEmbeddingStaleGenerationError,
    persist_active_embedding,
)
from semantic_memory_store import (
    CHUNK_ENTITY_TYPE,
    SemanticMemoryStoreError,
    get_entry_memory,
)


EMBEDDED = "EMBEDDED"
CURRENT = "CURRENT"
STALE = "STALE"
IGNORED = "IGNORED"

_SUPPORTED_EVENT_NAMES = frozenset({"INSERT", "MODIFY", "REMOVE"})
_ATTRIBUTE_VALUE_TYPES = frozenset(
    {"B", "BOOL", "BS", "L", "M", "N", "NS", "NULL", "S", "SS"}
)
FAILURE_CATEGORIES = (
    "MemoryReadFailures",
    "ProviderFailures",
    "PersistenceFailures",
    "ContractFailures",
    "UnexpectedFailures",
)
METRIC_NAMESPACE = "JournalM8/SemanticEmbedding"


class SemanticEmbeddingLifecycleError(RuntimeError):
    """A privacy-safe semantic-embedding stream processing failure."""


class EmbeddingStreamRecordResult(TypedDict):
    """Stable result returned after applying one entry-chunks stream record."""

    eventId: NotRequired[str]
    eventName: str
    entryId: NotRequired[str]
    chunkId: NotRequired[str]
    status: str


class BatchItemFailure(TypedDict):
    itemIdentifier: str


class EmbeddingStreamBatchResponse(TypedDict):
    batchItemFailures: list[BatchItemFailure]


class EmbeddingStreamBatchTelemetry(TypedDict):
    """Privacy-safe aggregate telemetry for one stream batch."""

    recordCount: int
    failureCount: int
    failureCounts: dict[str, int]


def apply_embedding_stream_record(
    entry_chunks_table: object,
    bedrock_client: object,
    record: Mapping[str, object],
) -> EmbeddingStreamRecordResult:
    """Embed one active chunk or return a terminal no-op result."""

    if not isinstance(record, Mapping):
        raise SemanticEmbeddingLifecycleError("stream record is malformed")
    event_id = _event_id(record)

    event_source = record.get("eventSource")
    if event_source is not None and event_source != "aws:dynamodb":
        raise SemanticEmbeddingLifecycleError("stream record source is invalid")

    event_name = record.get("eventName")
    if not isinstance(event_name, str) or event_name not in _SUPPORTED_EVENT_NAMES:
        raise SemanticEmbeddingLifecycleError(
            "stream record event name is invalid"
        )
    dynamodb = record.get("dynamodb")
    if not isinstance(dynamodb, Mapping):
        raise SemanticEmbeddingLifecycleError("stream record image is malformed")

    if event_name == "REMOVE":
        return _result(event_id=event_id, event_name=event_name, status=IGNORED)

    image = _deserialize_image(dynamodb.get("NewImage"))
    if image.get("entityType") != CHUNK_ENTITY_TYPE:
        return _result(event_id=event_id, event_name=event_name, status=IGNORED)

    user_id, entry_id, pk, sk = _chunk_lookup_identity(image)
    active_chunks = get_entry_memory(entry_chunks_table, user_id, entry_id)
    if not isinstance(active_chunks, list):
        raise SemanticEmbeddingLifecycleError(
            "active semantic generation response is invalid"
        )
    matches = [
        item
        for item in active_chunks
        if isinstance(item, Mapping)
        and item.get("PK") == pk
        and item.get("SK") == sk
    ]
    if not matches:
        return _result(
            event_id=event_id,
            event_name=event_name,
            entry_id=entry_id,
            chunk_id=_optional_chunk_id(image),
            status=STALE,
        )
    if len(matches) != 1:
        raise SemanticEmbeddingLifecycleError(
            "active semantic chunk lookup is inconsistent"
        )

    active_chunk = matches[0]
    content_digest = active_chunk.get("contentDigest")
    chunk_id = active_chunk.get("chunkId")
    canonical_text = active_chunk.get("text")
    if (
        not isinstance(content_digest, str)
        or not isinstance(chunk_id, str)
        or not chunk_id.strip()
        or not isinstance(canonical_text, str)
    ):
        raise SemanticEmbeddingLifecycleError(
            "active semantic chunk is invalid"
        )

    if embedding_is_current(
        active_chunk,
        user_id=user_id,
        content_digest=content_digest,
    ):
        return _result(
            event_id=event_id,
            event_name=event_name,
            entry_id=entry_id,
            chunk_id=chunk_id,
            status=CURRENT,
        )

    embedding = embed_canonical_text(
        bedrock_client,
        canonical_text=canonical_text,
        user_id=user_id,
        content_digest=content_digest,
    )
    try:
        persist_active_embedding(entry_chunks_table, active_chunk, embedding)
    except SemanticEmbeddingStaleGenerationError:
        status = STALE
    else:
        status = EMBEDDED

    return _result(
        event_id=event_id,
        event_name=event_name,
        entry_id=entry_id,
        chunk_id=chunk_id,
        status=status,
    )


def process_embedding_stream_event(
    entry_chunks_table: object,
    bedrock_client: object,
    event: Mapping[str, object],
) -> EmbeddingStreamBatchResponse:
    """Process records independently and return Lambda partial failures."""

    response, _ = process_embedding_stream_event_with_telemetry(
        entry_chunks_table,
        bedrock_client,
        event,
    )
    return response


def process_embedding_stream_event_with_telemetry(
    entry_chunks_table: object,
    bedrock_client: object,
    event: Mapping[str, object],
) -> tuple[EmbeddingStreamBatchResponse, EmbeddingStreamBatchTelemetry]:
    """Process records and return privacy-safe failure classifications."""

    if not isinstance(event, Mapping):
        raise SemanticEmbeddingLifecycleError("stream batch event is malformed")
    records = event.get("Records")
    if not isinstance(records, list):
        raise SemanticEmbeddingLifecycleError("stream batch event is malformed")

    failures: list[BatchItemFailure] = []
    failure_counts = {category: 0 for category in FAILURE_CATEGORIES}
    failed_sequence_numbers: set[str] = set()
    for record in records:
        try:
            apply_embedding_stream_record(
                entry_chunks_table,
                bedrock_client,
                record,  # type: ignore[arg-type]
            )
        except Exception as error:
            failure_counts[_failure_category(error)] += 1
            sequence_number = _sequence_number(record)
            if sequence_number is None:
                raise SemanticEmbeddingLifecycleError(
                    "stream batch checkpoint cannot be established safely"
                ) from None
            if sequence_number not in failed_sequence_numbers:
                failures.append({"itemIdentifier": sequence_number})
                failed_sequence_numbers.add(sequence_number)
    response: EmbeddingStreamBatchResponse = {
        "batchItemFailures": failures,
    }
    telemetry: EmbeddingStreamBatchTelemetry = {
        "recordCount": len(records),
        "failureCount": sum(failure_counts.values()),
        "failureCounts": failure_counts,
    }
    return response, telemetry


def _failure_category(error: Exception) -> str:
    if isinstance(error, SemanticMemoryStoreError):
        return "MemoryReadFailures"
    if isinstance(error, SemanticEmbeddingProviderError):
        return "ProviderFailures"
    if isinstance(error, SemanticEmbeddingPersistenceError):
        return "PersistenceFailures"
    if isinstance(
        error,
        (SemanticEmbeddingContractError, SemanticEmbeddingLifecycleError),
    ):
        return "ContractFailures"
    return "UnexpectedFailures"


def build_embedding_telemetry_document(
    telemetry: EmbeddingStreamBatchTelemetry,
    *,
    function_name: str,
    timestamp_ms: int,
) -> dict[str, object]:
    """Build one bounded CloudWatch EMF document without record content."""

    metric_names = ["RecordCount", "RecordFailures", *FAILURE_CATEGORIES]
    document: dict[str, object] = {
        "_aws": {
            "Timestamp": timestamp_ms,
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [["FunctionName"]],
                "Metrics": [
                    {"Name": name, "Unit": "Count"}
                    for name in metric_names
                ],
            }],
        },
        "event": "SemanticEmbeddingLifecycleBatch",
        "FunctionName": function_name,
        "RecordCount": telemetry["recordCount"],
        "RecordFailures": telemetry["failureCount"],
    }
    document.update(telemetry["failureCounts"])
    return document


def _result(
    *,
    event_id: str | None,
    event_name: str,
    status: str,
    entry_id: str | None = None,
    chunk_id: str | None = None,
) -> EmbeddingStreamRecordResult:
    result: EmbeddingStreamRecordResult = {
        "eventName": event_name,
        "status": status,
    }
    if event_id is not None:
        result["eventId"] = event_id
    if entry_id is not None:
        result["entryId"] = entry_id
    if chunk_id is not None:
        result["chunkId"] = chunk_id
    return result


def _chunk_lookup_identity(
    image: Mapping[str, object],
) -> tuple[str, str, str, str]:
    user_id = image.get("userId")
    entry_id = image.get("entryId")
    pk = image.get("PK")
    sk = image.get("SK")
    if (
        not isinstance(user_id, str)
        or not user_id.strip()
        or not isinstance(entry_id, str)
        or not entry_id.strip()
        or not isinstance(pk, str)
        or pk != f"USER#{user_id}"
        or not isinstance(sk, str)
        or not sk.strip()
    ):
        raise SemanticEmbeddingLifecycleError(
            "semantic chunk stream identity is malformed"
        )
    return user_id, entry_id, pk, sk


def _optional_chunk_id(image: Mapping[str, object]) -> str | None:
    chunk_id = image.get("chunkId")
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        return None
    return chunk_id


def _event_id(record: object) -> str | None:
    if not isinstance(record, Mapping):
        return None
    event_id = record.get("eventID")
    if not isinstance(event_id, str) or not event_id.strip():
        return None
    return event_id


def _sequence_number(record: object) -> str | None:
    if not isinstance(record, Mapping):
        return None
    dynamodb = record.get("dynamodb")
    if not isinstance(dynamodb, Mapping):
        return None
    sequence_number = dynamodb.get("SequenceNumber")
    if not isinstance(sequence_number, str) or not sequence_number.strip():
        return None
    return sequence_number


def _deserialize_image(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticEmbeddingLifecycleError("stream record image is malformed")

    deserializer = TypeDeserializer()
    result: dict[str, object] = {}
    try:
        for attribute_name, attribute_value in value.items():
            if not isinstance(attribute_name, str):
                raise TypeError
            _validate_attribute_value(attribute_value)
            result[attribute_name] = deserializer.deserialize(attribute_value)
    except Exception:
        raise SemanticEmbeddingLifecycleError(
            "stream record image is malformed"
        ) from None
    return result


def _validate_attribute_value(value: object) -> None:
    if not isinstance(value, Mapping) or len(value) != 1:
        raise TypeError
    attribute_type, payload = next(iter(value.items()))
    if attribute_type not in _ATTRIBUTE_VALUE_TYPES:
        raise TypeError

    if attribute_type in {"S", "N"}:
        if not isinstance(payload, str):
            raise TypeError
    elif attribute_type == "B":
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError
    elif attribute_type == "BOOL":
        if not isinstance(payload, bool):
            raise TypeError
    elif attribute_type == "NULL":
        if payload is not True:
            raise TypeError
    elif attribute_type in {"SS", "NS", "BS"}:
        if not isinstance(payload, list):
            raise TypeError
        expected_type = (
            str if attribute_type in {"SS", "NS"} else (bytes, bytearray)
        )
        if not all(isinstance(item, expected_type) for item in payload):
            raise TypeError
    elif attribute_type == "L":
        if not isinstance(payload, list):
            raise TypeError
        for item in payload:
            _validate_attribute_value(item)
    elif attribute_type == "M":
        if not isinstance(payload, Mapping):
            raise TypeError
        for attribute_name, item in payload.items():
            if not isinstance(attribute_name, str):
                raise TypeError
            _validate_attribute_value(item)


__all__ = [
    "CURRENT",
    "EMBEDDED",
    "IGNORED",
    "STALE",
    "BatchItemFailure",
    "EmbeddingStreamBatchResponse",
    "EmbeddingStreamRecordResult",
    "EmbeddingStreamBatchTelemetry",
    "FAILURE_CATEGORIES",
    "METRIC_NAMESPACE",
    "SemanticEmbeddingLifecycleError",
    "apply_embedding_stream_record",
    "build_embedding_telemetry_document",
    "process_embedding_stream_event",
    "process_embedding_stream_event_with_telemetry",
]
