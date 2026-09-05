"""DynamoDB Streams adapter for semantic-memory entry lifecycle changes.

Deployment must configure the event-source mapping with
``FunctionResponseTypes=["ReportBatchItemFailures"]`` so Lambda honors the
partial-batch response returned by :func:`process_entry_stream_event`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NotRequired, TypedDict

from boto3.dynamodb.types import TypeDeserializer

from semantic_memory_deletion_guard import semantic_deletion_guard_exists
from semantic_memory_store import delete_entry_memory, replace_entry_memory


ACTIVE = "ACTIVE"
DELETED = "DELETED"
IGNORED = "IGNORED"
DELETION_GUARDED = "DELETION_GUARDED"

_SUPPORTED_EVENT_NAMES = frozenset({"INSERT", "MODIFY", "REMOVE"})
_ATTRIBUTE_VALUE_TYPES = frozenset(
    {"B", "BOOL", "BS", "L", "M", "N", "NS", "NULL", "S", "SS"}
)


class SemanticMemoryLifecycleError(RuntimeError):
    """A privacy-safe semantic-memory stream processing failure."""


class EntryStreamRecordResult(TypedDict):
    """Stable result returned after applying one stream record."""

    eventId: NotRequired[str]
    eventName: str
    entryId: NotRequired[str]
    status: str


class BatchItemFailure(TypedDict):
    itemIdentifier: str


class EntryStreamBatchResponse(TypedDict):
    batchItemFailures: list[BatchItemFailure]


def apply_entry_stream_record(
    entry_chunks_table: object,
    record: Mapping[str, object],
) -> EntryStreamRecordResult:
    """Apply one main-table DynamoDB Streams record in isolation."""

    if not isinstance(record, Mapping):
        raise SemanticMemoryLifecycleError("stream record is malformed")

    event_id = _event_id(record)

    event_source = record.get("eventSource")
    if event_source is not None and event_source != "aws:dynamodb":
        raise SemanticMemoryLifecycleError("stream record source is invalid")

    event_name = record.get("eventName")
    if not isinstance(event_name, str) or event_name not in _SUPPORTED_EVENT_NAMES:
        raise SemanticMemoryLifecycleError("stream record event name is invalid")

    dynamodb = record.get("dynamodb")
    if not isinstance(dynamodb, Mapping):
        raise SemanticMemoryLifecycleError("stream record image is malformed")

    image_name = "OldImage" if event_name == "REMOVE" else "NewImage"
    image = _deserialize_image(dynamodb.get(image_name))
    if image.get("entityType") != "ENTRY":
        result: EntryStreamRecordResult = {
            "eventName": event_name,
            "status": IGNORED,
        }
        if event_id is not None:
            result["eventId"] = event_id
        return result

    entry_id, user_id = _entry_identity(image)
    if event_name == "REMOVE":
        delete_entry_memory(entry_chunks_table, user_id, entry_id)
        status = DELETED
    elif semantic_deletion_guard_exists(entry_chunks_table, user_id):
        delete_entry_memory(entry_chunks_table, user_id, entry_id)
        status = DELETION_GUARDED
    else:
        replacement = replace_entry_memory(entry_chunks_table, image)
        if not isinstance(replacement, Mapping):
            raise SemanticMemoryLifecycleError(
                "semantic memory replacement result is invalid"
            )
        status = replacement.get("status")
        if status not in {ACTIVE, DELETED}:
            raise SemanticMemoryLifecycleError(
                "semantic memory replacement result is invalid"
            )
        if semantic_deletion_guard_exists(entry_chunks_table, user_id):
            delete_entry_memory(entry_chunks_table, user_id, entry_id)
            status = DELETION_GUARDED

    result = {
        "eventName": event_name,
        "entryId": entry_id,
        "status": status,
    }
    if event_id is not None:
        result["eventId"] = event_id
    return result


def process_entry_stream_event(
    entry_chunks_table: object,
    event: Mapping[str, object],
) -> EntryStreamBatchResponse:
    """Process records in order and return Lambda's partial-batch response."""

    if not isinstance(event, Mapping):
        raise SemanticMemoryLifecycleError("stream batch event is malformed")
    records = event.get("Records")
    if not isinstance(records, list):
        raise SemanticMemoryLifecycleError("stream batch event is malformed")

    failures: list[BatchItemFailure] = []
    failed_sequence_numbers: set[str] = set()
    for record in records:
        try:
            apply_entry_stream_record(entry_chunks_table, record)  # type: ignore[arg-type]
        except Exception:
            sequence_number = _sequence_number(record)
            if sequence_number is None:
                raise SemanticMemoryLifecycleError(
                    "stream batch checkpoint cannot be established safely"
                ) from None
            if sequence_number not in failed_sequence_numbers:
                failures.append({"itemIdentifier": sequence_number})
                failed_sequence_numbers.add(sequence_number)

    return {"batchItemFailures": failures}


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


def _entry_identity(entry: Mapping[str, object]) -> tuple[str, str]:
    entry_id = entry.get("entryId")
    user_id = entry.get("userId")
    if (
        not isinstance(entry_id, str)
        or not entry_id.strip()
        or not isinstance(user_id, str)
        or not user_id.strip()
    ):
        raise SemanticMemoryLifecycleError("ENTRY identity is malformed")
    return entry_id, user_id


def _deserialize_image(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticMemoryLifecycleError("stream record image is malformed")

    deserializer = TypeDeserializer()
    result: dict[str, object] = {}
    try:
        for attribute_name, attribute_value in value.items():
            if not isinstance(attribute_name, str):
                raise TypeError
            _validate_attribute_value(attribute_value)
            result[attribute_name] = deserializer.deserialize(attribute_value)
    except Exception:
        raise SemanticMemoryLifecycleError(
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
    elif attribute_type in {"B"}:
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
    "ACTIVE",
    "DELETED",
    "DELETION_GUARDED",
    "IGNORED",
    "BatchItemFailure",
    "EntryStreamBatchResponse",
    "EntryStreamRecordResult",
    "SemanticMemoryLifecycleError",
    "apply_entry_stream_record",
    "process_entry_stream_event",
]
