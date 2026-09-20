"""Bounded, resumable historical semantic-memory backfill."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from time import sleep
from typing import TypedDict

from semantic_embedding_contract import embedding_is_current
from semantic_memory_contract import (
    build_entry_semantic_chunks,
    canonical_entry_text,
)
from semantic_memory_store import get_entry_memory, replace_entry_memory


BACKFILL_VERSION = "jm8-semantic-backfill-v1"


class SemanticBackfillError(RuntimeError):
    """A privacy-safe historical semantic-memory backfill failure."""


class SemanticBackfillResult(TypedDict):
    backfillVersion: str
    eligibleEntryCount: int
    processedEntryCount: int
    alreadyCoveredEntryCount: int
    finalCoveredEntryCount: int


def collect_backfill_entries(table: object) -> list[dict[str, object]]:
    """Retrieve exactly the approved entry fields required for an apply run.

    Unlike the structural audit, an actual backfill must read approved journal
    text to build semantic chunks. Callers must never log the returned values.
    """

    names = {
        "#pk": "PK",
        "#sk": "SK",
        "#entity": "entityType",
        "#user": "userId",
        "#entry": "entryId",
        "#source": "sourceType",
        "#status": "status",
        "#reviewStatus": "reviewStatus",
        "#reviewedAt": "reviewedAt",
        "#cleanText": "cleanText",
        "#rawText": "rawText",
        "#createdAt": "createdAt",
        "#updatedAt": "updatedAt",
    }
    values = {
        ":entryType": "ENTRY",
        ":reviewed": "REVIEWED",
        ":analyzed": "ANALYZED",
        ":completed": "COMPLETED",
        ":typed": "typed",
        ":stringType": "S",
        ":zero": 0,
    }
    reviewed = (
        "(#status = :reviewed OR #status = :analyzed "
        "OR #reviewStatus = :completed OR attribute_exists(#reviewedAt))"
    )
    clean = (
        f"{reviewed} AND attribute_type(#cleanText, :stringType) "
        "AND size(#cleanText) > :zero"
    )
    raw = (
        "#source = :typed "
        "AND (#status = :reviewed OR #status = :analyzed) "
        "AND attribute_type(#rawText, :stringType) "
        "AND size(#rawText) > :zero"
    )
    projection = ", ".join(names)
    entries = _scan(
        table,
        ProjectionExpression=projection,
        FilterExpression=(
            f"#entity = :entryType AND (({clean}) OR ({raw}))"
        ),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ConsistentRead=True,
    )

    by_identity: dict[tuple[str, str], dict[str, object]] = {}
    for entry in entries:
        identity = _validated_entry_identity(entry)
        if identity in by_identity or canonical_entry_text(entry) is None:
            raise SemanticBackfillError(
                "semantic backfill entry inventory is invalid"
            )
        by_identity[identity] = entry

    return sorted(
        by_identity.values(),
        key=lambda entry: _opaque_order(*_validated_entry_identity(entry)),
    )


def execute_semantic_backfill(
    entries: Sequence[Mapping[str, object]],
    entry_chunks_table: object,
    *,
    current_entry_loader: Callable[[str, str], Mapping[str, object] | None],
    expected_entry_count: int,
    maximum_entry_count: int,
    poll_attempts: int = 20,
    poll_delay_seconds: float = 3.0,
    sleeper: Callable[[float], None] = sleep,
) -> SemanticBackfillResult:
    """Backfill eligible entries serially and wait for each current embedding."""

    _validate_bounds(
        entries,
        expected_entry_count=expected_entry_count,
        maximum_entry_count=maximum_entry_count,
        poll_attempts=poll_attempts,
        poll_delay_seconds=poll_delay_seconds,
    )

    ordered = sorted(
        entries,
        key=lambda entry: _opaque_order(*_validated_entry_identity(entry)),
    )
    identities: set[tuple[str, str]] = set()
    processed = 0
    already_covered = 0

    for captured in ordered:
        identity = _validated_entry_identity(captured)
        if identity in identities or canonical_entry_text(captured) is None:
            raise SemanticBackfillError(
                "semantic backfill entry inventory is invalid"
            )
        identities.add(identity)

        pk = captured.get("PK")
        sk = captured.get("SK")
        if not isinstance(pk, str) or not isinstance(sk, str):
            raise SemanticBackfillError(
                "semantic backfill entry inventory is invalid"
            )
        current = _load_current_entry(current_entry_loader, pk, sk)
        if not _same_entry_state(captured, current):
            raise SemanticBackfillError(
                "semantic backfill source changed during execution"
            )

        if entry_is_fully_covered(entry_chunks_table, current):
            already_covered += 1
            continue

        try:
            replacement = replace_entry_memory(
                entry_chunks_table,
                current,
                replay_token=BACKFILL_VERSION,
            )
        except Exception:
            raise SemanticBackfillError(
                "semantic backfill persistence failed"
            ) from None
        if replacement.get("status") != "ACTIVE":
            raise SemanticBackfillError(
                "semantic backfill persistence failed"
            )
        processed += 1

        for attempt in range(poll_attempts):
            latest = _load_current_entry(current_entry_loader, pk, sk)
            if not _same_entry_state(current, latest):
                raise SemanticBackfillError(
                    "semantic backfill source changed during execution"
                )
            if entry_is_fully_covered(entry_chunks_table, latest):
                break
            if attempt + 1 < poll_attempts:
                sleeper(poll_delay_seconds)
        else:
            raise SemanticBackfillError(
                "semantic backfill embedding did not become current"
            )

    final_covered = 0
    for captured in ordered:
        pk = captured["PK"]
        sk = captured["SK"]
        current = _load_current_entry(current_entry_loader, pk, sk)  # type: ignore[arg-type]
        if not _same_entry_state(captured, current):
            raise SemanticBackfillError(
                "semantic backfill source changed during execution"
            )
        if entry_is_fully_covered(entry_chunks_table, current):
            final_covered += 1

    if final_covered != expected_entry_count:
        raise SemanticBackfillError(
            "semantic backfill final coverage is incomplete"
        )

    return {
        "backfillVersion": BACKFILL_VERSION,
        "eligibleEntryCount": expected_entry_count,
        "processedEntryCount": processed,
        "alreadyCoveredEntryCount": already_covered,
        "finalCoveredEntryCount": final_covered,
    }


def entry_is_fully_covered(
    entry_chunks_table: object,
    entry: Mapping[str, object],
) -> bool:
    """Return whether the entry has its exact active chunks and embeddings."""

    canonical = canonical_entry_text(entry)
    if canonical is None:
        raise SemanticBackfillError("semantic backfill entry is ineligible")
    expected = build_entry_semantic_chunks(entry)
    try:
        stored = get_entry_memory(
            entry_chunks_table,
            canonical["userId"],
            canonical["entryId"],
        )
    except Exception:
        raise SemanticBackfillError(
            "semantic backfill coverage read failed"
        ) from None
    if len(stored) != len(expected) or not expected:
        return False

    compared_fields = (
        "entryId",
        "userId",
        "sourceType",
        "canonicalTextField",
        "contentDigest",
        "chunkDigest",
        "chunkId",
        "chunkOrdinal",
        "chunkCount",
        "chunkingVersion",
        "text",
        "entryCreatedAt",
        "entryUpdatedAt",
    )
    for expected_chunk, stored_chunk in zip(expected, stored, strict=True):
        if any(
            expected_chunk.get(field) != stored_chunk.get(field)
            for field in compared_fields
        ):
            return False
        if not embedding_is_current(
            stored_chunk,
            user_id=canonical["userId"],
            content_digest=expected_chunk["contentDigest"],
        ):
            return False
    return True


def validate_backfill_precondition(
    report: Mapping[str, object],
    *,
    expected_entry_count: int,
    maximum_entry_count: int,
) -> None:
    """Require a safe structural audit before approved text may be read."""

    if (
        isinstance(expected_entry_count, bool)
        or not isinstance(expected_entry_count, int)
        or expected_entry_count < 1
        or isinstance(maximum_entry_count, bool)
        or not isinstance(maximum_entry_count, int)
        or maximum_entry_count < expected_entry_count
    ):
        raise SemanticBackfillError("semantic backfill bounds are invalid")

    violation_fields = (
        "duplicateCurrentGenerationCount",
        "invalidEmbeddingCount",
        "orphanedSemanticRecordCount",
        "staleGenerationCount",
        "structuralIntegrityViolationCount",
        "tenantIntegrityViolationCount",
    )
    eligible = report.get("eligibleEntryCount")
    covered = report.get("coveredEntryCount")
    if (
        report.get("auditVersion") != "jm8-semantic-coverage-audit-v1"
        or report.get("status") not in {
            "READY_FOR_BACKFILL",
            "BACKFILL_NOT_REQUIRED",
        }
        or eligible != expected_entry_count
        or isinstance(covered, bool)
        or not isinstance(covered, int)
        or covered < 0
        or covered > expected_entry_count
        or any(report.get(field) != 0 for field in violation_fields)
    ):
        raise SemanticBackfillError(
            "semantic backfill structural precondition failed"
        )


def _validate_bounds(
    entries: object,
    *,
    expected_entry_count: object,
    maximum_entry_count: object,
    poll_attempts: object,
    poll_delay_seconds: object,
) -> None:
    if (
        not isinstance(entries, Sequence)
        or isinstance(entries, (str, bytes, bytearray))
        or isinstance(expected_entry_count, bool)
        or not isinstance(expected_entry_count, int)
        or expected_entry_count < 1
        or isinstance(maximum_entry_count, bool)
        or not isinstance(maximum_entry_count, int)
        or maximum_entry_count < expected_entry_count
        or len(entries) != expected_entry_count
        or len(entries) > maximum_entry_count
        or isinstance(poll_attempts, bool)
        or not isinstance(poll_attempts, int)
        or poll_attempts < 1
        or isinstance(poll_delay_seconds, bool)
        or not isinstance(poll_delay_seconds, (int, float))
        or poll_delay_seconds < 0
    ):
        raise SemanticBackfillError("semantic backfill bounds are invalid")


def _load_current_entry(
    loader: Callable[[str, str], Mapping[str, object] | None],
    pk: str,
    sk: str,
) -> Mapping[str, object]:
    try:
        entry = loader(pk, sk)
    except Exception:
        raise SemanticBackfillError(
            "semantic backfill source read failed"
        ) from None
    if not isinstance(entry, Mapping):
        raise SemanticBackfillError(
            "semantic backfill source changed during execution"
        )
    return entry


def _same_entry_state(
    expected: Mapping[str, object],
    current: Mapping[str, object],
) -> bool:
    fields = (
        "PK",
        "SK",
        "entityType",
        "userId",
        "entryId",
        "sourceType",
        "status",
        "reviewStatus",
        "reviewedAt",
        "cleanText",
        "rawText",
        "createdAt",
        "updatedAt",
    )
    return all(expected.get(field) == current.get(field) for field in fields)


def _validated_entry_identity(
    entry: Mapping[str, object],
) -> tuple[str, str]:
    user_id = entry.get("userId")
    entry_id = entry.get("entryId")
    pk = entry.get("PK")
    sk = entry.get("SK")
    if (
        not isinstance(user_id, str)
        or not user_id.strip()
        or not isinstance(entry_id, str)
        or not entry_id.strip()
        or pk != f"USER#{user_id}"
        or not isinstance(sk, str)
        or not sk.startswith("ENTRY#")
        or not sk.endswith(f"#{entry_id}")
    ):
        raise SemanticBackfillError(
            "semantic backfill entry inventory is invalid"
        )
    return user_id, entry_id


def _opaque_order(user_id: str, entry_id: str) -> str:
    return hashlib.sha256(
        f"{user_id}\0{entry_id}".encode("utf-8")
    ).hexdigest()


def _scan(table: object, **kwargs: object) -> list[dict[str, object]]:
    scan = getattr(table, "scan", None)
    if not callable(scan):
        raise SemanticBackfillError("semantic backfill dependency is invalid")
    items: list[dict[str, object]] = []
    last_key: object = None
    while True:
        request = dict(kwargs)
        if last_key is not None:
            request["ExclusiveStartKey"] = last_key
        try:
            response = scan(**request)
        except Exception:
            raise SemanticBackfillError(
                "semantic backfill source read failed"
            ) from None
        if not isinstance(response, Mapping):
            raise SemanticBackfillError(
                "semantic backfill source response failed"
            )
        page = response.get("Items", [])
        if not isinstance(page, list) or not all(
            isinstance(item, Mapping) for item in page
        ):
            raise SemanticBackfillError(
                "semantic backfill source response failed"
            )
        items.extend(dict(item) for item in page)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items


__all__ = [
    "BACKFILL_VERSION",
    "SemanticBackfillError",
    "SemanticBackfillResult",
    "collect_backfill_entries",
    "entry_is_fully_covered",
    "execute_semantic_backfill",
    "validate_backfill_precondition",
]
