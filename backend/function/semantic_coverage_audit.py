"""Privacy-safe structural coverage audit for JM8 semantic memory."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import TypedDict

from semantic_chunking import CHUNKING_VERSION
from semantic_embedding_contract import embedding_is_current
from semantic_memory_store import CHUNK_ENTITY_TYPE, MANIFEST_ENTITY_TYPE


AUDIT_VERSION = "jm8-semantic-coverage-audit-v1"
BACKFILL_NOT_REQUIRED = "BACKFILL_NOT_REQUIRED"
READY_FOR_BACKFILL = "READY_FOR_BACKFILL"
BLOCKED = "BLOCKED"

_HEX = frozenset("0123456789abcdef")


class SemanticCoverageAuditError(RuntimeError):
    """The audit input or read response was malformed."""


class SemanticCoverageReport(TypedDict):
    auditVersion: str
    status: str
    eligibleEntryCount: int
    coveredEntryCount: int
    currentManifestCount: int
    currentChunkCount: int
    embeddedCurrentChunkCount: int
    missingManifestCount: int
    missingEmbeddingCount: int
    invalidEmbeddingCount: int
    staleGenerationCount: int
    duplicateCurrentGenerationCount: int
    tenantIntegrityViolationCount: int
    structuralIntegrityViolationCount: int
    orphanedSemanticRecordCount: int


def collect_eligible_entries(table: object) -> list[dict[str, object]]:
    """Return eligible identities without retrieving either journal text field."""

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
        ":completed": "COMPLETED",
        ":typed": "typed",
        ":stringType": "S",
        ":zero": 0,
    }
    projection = (
        "#pk, #sk, #user, #entry, #source, #status, #reviewStatus, "
        "#reviewedAt, #createdAt, #updatedAt"
    )
    reviewed = (
        "(#status = :reviewed OR #reviewStatus = :completed "
        "OR attribute_exists(#reviewedAt))"
    )
    clean = (
        f"{reviewed} AND attribute_type(#cleanText, :stringType) "
        "AND size(#cleanText) > :zero"
    )
    raw = (
        "#source = :typed AND #status = :reviewed "
        "AND attribute_type(#rawText, :stringType) "
        "AND size(#rawText) > :zero"
    )

    clean_entries = _scan(
        table,
        ProjectionExpression=projection,
        FilterExpression=f"#entity = :entryType AND ({clean})",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ConsistentRead=True,
    )
    raw_entries = _scan(
        table,
        ProjectionExpression=projection,
        FilterExpression=(
            f"#entity = :entryType AND ({raw}) AND NOT ({clean})"
        ),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ConsistentRead=True,
    )

    result: list[dict[str, object]] = []
    for item in clean_entries:
        result.append({**item, "canonicalTextField": "cleanText"})
    for item in raw_entries:
        result.append({**item, "canonicalTextField": "rawText"})
    return result


def collect_semantic_records(table: object) -> list[dict[str, object]]:
    """Return structural semantic fields and vectors, never chunk journal text."""

    attributes = (
        "PK",
        "SK",
        "entityType",
        "userId",
        "entryId",
        "activeContentDigest",
        "chunkCount",
        "chunkingVersion",
        "canonicalTextField",
        "sourceType",
        "entryCreatedAt",
        "entryUpdatedAt",
        "generationId",
        "contentDigest",
        "chunkId",
        "chunkOrdinal",
        "chunkDigest",
        "characterCount",
        "wordCount",
        "embedding",
        "embeddingModelId",
        "embeddingVersion",
        "embeddingDimensions",
        "embeddingNormalized",
        "embeddingContentDigest",
        "embeddingPartition",
        "embeddingInputTokenCount",
    )
    names = {f"#f{index}": name for index, name in enumerate(attributes)}
    projection = ", ".join(names)
    return _scan(
        table,
        ProjectionExpression=projection,
        ExpressionAttributeNames=names,
        ConsistentRead=True,
    )


def audit_semantic_coverage(
    eligible_entries: Sequence[Mapping[str, object]],
    semantic_records: Sequence[Mapping[str, object]],
) -> SemanticCoverageReport:
    """Compare eligible entries with active manifests, chunks, and embeddings."""

    if not _mapping_sequence(eligible_entries) or not _mapping_sequence(
        semantic_records
    ):
        raise SemanticCoverageAuditError("semantic coverage input is invalid")

    entries: dict[tuple[str, str], Mapping[str, object]] = {}
    manifests: defaultdict[
        tuple[str, str], list[Mapping[str, object]]
    ] = defaultdict(list)
    chunks: defaultdict[
        tuple[str, str], list[Mapping[str, object]]
    ] = defaultdict(list)

    structural = 0
    tenant = 0
    duplicates = 0

    for entry in eligible_entries:
        identity = _identity(entry)
        if identity is None:
            structural += 1
            continue
        if identity in entries:
            duplicates += 1
            continue
        if not _entry_identity_is_consistent(entry, *identity):
            structural += 1
            continue
        entries[identity] = entry

    for record in semantic_records:
        entity_type = record.get("entityType")
        if entity_type not in {MANIFEST_ENTITY_TYPE, CHUNK_ENTITY_TYPE}:
            continue
        identity = _identity(record)
        if identity is None:
            structural += 1
            continue
        user_id, entry_id = identity
        if record.get("PK") != f"USER#{user_id}":
            tenant += 1
            continue
        if entity_type == MANIFEST_ENTITY_TYPE:
            manifests[identity].append(record)
        else:
            chunks[identity].append(record)

    current_manifests = 0
    current_chunks = 0
    embedded_chunks = 0
    missing_manifests = 0
    missing_embeddings = 0
    invalid_embeddings = 0
    stale_generations = 0
    covered_entries = 0

    for identity, entry in entries.items():
        user_id, entry_id = identity
        entry_manifests = manifests.get(identity, [])
        entry_chunks = chunks.get(identity, [])

        if len(entry_manifests) != 1:
            if not entry_manifests:
                missing_manifests += 1
                stale_generations += len(entry_chunks)
            else:
                duplicates += len(entry_manifests) - 1
            continue

        manifest = entry_manifests[0]
        manifest_result = _manifest_contract(
            manifest,
            entry,
            user_id=user_id,
            entry_id=entry_id,
        )
        if manifest_result is None:
            structural += 1
            continue
        active_digest, expected_count = manifest_result
        current_manifests += 1

        active = [
            item
            for item in entry_chunks
            if item.get("generationId") == active_digest
        ]
        stale_generations += len(entry_chunks) - len(active)

        ordinals: set[int] = set()
        chunk_ids: set[str] = set()
        active_valid = True
        for item in active:
            ordinal = _integer(item.get("chunkOrdinal"))
            chunk_id = item.get("chunkId")
            chunk_digest = item.get("chunkDigest")
            if (
                ordinal is None
                or ordinal < 0
                or ordinal >= expected_count
                or not isinstance(chunk_id, str)
                or not _digest(chunk_digest)
                or not _chunk_contract(
                    item,
                    user_id=user_id,
                    entry_id=entry_id,
                    active_digest=active_digest,
                    expected_count=expected_count,
                    ordinal=ordinal,
                    chunk_id=chunk_id,
                    chunk_digest=chunk_digest,
                )
            ):
                structural += 1
                active_valid = False
                continue
            if ordinal in ordinals or chunk_id in chunk_ids:
                duplicates += 1
                active_valid = False
                continue
            ordinals.add(ordinal)
            chunk_ids.add(chunk_id)
            current_chunks += 1

            if "embedding" not in item:
                missing_embeddings += 1
            elif embedding_is_current(
                item,
                user_id=user_id,
                content_digest=active_digest,
            ):
                embedded_chunks += 1
            else:
                invalid_embeddings += 1

        complete = (
            active_valid
            and len(active) == expected_count
            and ordinals == set(range(expected_count))
        )
        if not complete:
            structural += 1
            continue
        if all("embedding" in item and embedding_is_current(
            item,
            user_id=user_id,
            content_digest=active_digest,
        ) for item in active):
            covered_entries += 1

    eligible_identities = set(entries)
    orphaned = sum(
        len(records)
        for identity, records in manifests.items()
        if identity not in eligible_identities
    ) + sum(
        len(records)
        for identity, records in chunks.items()
        if identity not in eligible_identities
    )

    blocked = any((
        structural,
        tenant,
        duplicates,
        stale_generations,
        invalid_embeddings,
        orphaned,
    ))
    missing = missing_manifests or missing_embeddings
    status = (
        BLOCKED
        if blocked
        else READY_FOR_BACKFILL
        if missing
        else BACKFILL_NOT_REQUIRED
    )
    return {
        "auditVersion": AUDIT_VERSION,
        "status": status,
        "eligibleEntryCount": len(entries),
        "coveredEntryCount": covered_entries,
        "currentManifestCount": current_manifests,
        "currentChunkCount": current_chunks,
        "embeddedCurrentChunkCount": embedded_chunks,
        "missingManifestCount": missing_manifests,
        "missingEmbeddingCount": missing_embeddings,
        "invalidEmbeddingCount": invalid_embeddings,
        "staleGenerationCount": stale_generations,
        "duplicateCurrentGenerationCount": duplicates,
        "tenantIntegrityViolationCount": tenant,
        "structuralIntegrityViolationCount": structural,
        "orphanedSemanticRecordCount": orphaned,
    }


def _manifest_contract(
    manifest: Mapping[str, object],
    entry: Mapping[str, object],
    *,
    user_id: str,
    entry_id: str,
) -> tuple[str, int] | None:
    active_digest = manifest.get("activeContentDigest")
    chunk_count = _integer(manifest.get("chunkCount"))
    expected_sk = f"ENTRY#{_entry_token(entry_id)}#MANIFEST"
    if (
        manifest.get("SK") != expected_sk
        or manifest.get("chunkingVersion") != CHUNKING_VERSION
        or not _digest(active_digest)
        or chunk_count is None
        or chunk_count < 1
        or manifest.get("canonicalTextField")
        != entry.get("canonicalTextField")
        or manifest.get("sourceType") != entry.get("sourceType")
    ):
        return None
    for source, target in (
        ("createdAt", "entryCreatedAt"),
        ("updatedAt", "entryUpdatedAt"),
    ):
        if source in entry and manifest.get(target) != entry.get(source):
            return None
    return active_digest, chunk_count


def _chunk_contract(
    item: Mapping[str, object],
    *,
    user_id: str,
    entry_id: str,
    active_digest: str,
    expected_count: int,
    ordinal: int,
    chunk_id: str,
    chunk_digest: str,
) -> bool:
    expected_chunk_id = _expected_chunk_id(
        entry_id,
        active_digest,
        ordinal,
        chunk_digest,
    )
    expected_sk = (
        f"ENTRY#{_entry_token(entry_id)}#GEN#{active_digest}#CHUNK#"
        f"{ordinal:08d}#{expected_chunk_id}"
    )
    return (
        item.get("SK") == expected_sk
        and item.get("contentDigest") == active_digest
        and item.get("generationId") == active_digest
        and item.get("chunkingVersion") == CHUNKING_VERSION
        and _integer(item.get("chunkCount")) == expected_count
        and chunk_id == expected_chunk_id
    )


def _entry_identity_is_consistent(
    entry: Mapping[str, object],
    user_id: str,
    entry_id: str,
) -> bool:
    pk = entry.get("PK")
    sk = entry.get("SK")
    return (
        pk == f"USER#{user_id}"
        and isinstance(sk, str)
        and sk.startswith("ENTRY#")
        and sk.endswith(f"#{entry_id}")
        and entry.get("canonicalTextField") in {"cleanText", "rawText"}
    )


def _identity(item: Mapping[str, object]) -> tuple[str, str] | None:
    user_id = item.get("userId")
    entry_id = item.get("entryId")
    if (
        not isinstance(user_id, str)
        or not user_id.strip()
        or not isinstance(entry_id, str)
        or not entry_id.strip()
    ):
        return None
    return user_id, entry_id


def _entry_token(entry_id: str) -> str:
    return hashlib.sha256(entry_id.encode("utf-8")).hexdigest()


def _expected_chunk_id(
    entry_id: str,
    content_digest: str,
    ordinal: int,
    chunk_digest: str,
) -> str:
    identity = "\0".join((
        CHUNKING_VERSION,
        entry_id,
        content_digest,
        str(ordinal),
        chunk_digest,
    ))
    return f"chunk_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        integral = value.to_integral_value()
        if value == integral:
            return int(integral)
    return None


def _mapping_sequence(value: object) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and all(isinstance(item, Mapping) for item in value)
    )


def _scan(table: object, **kwargs: object) -> list[dict[str, object]]:
    scan = getattr(table, "scan", None)
    if not callable(scan):
        raise SemanticCoverageAuditError("semantic coverage dependency is invalid")
    items: list[dict[str, object]] = []
    last_key: object = None
    while True:
        request = dict(kwargs)
        if last_key is not None:
            request["ExclusiveStartKey"] = last_key
        try:
            response = scan(**request)
        except Exception:
            raise SemanticCoverageAuditError(
                "semantic coverage read failed"
            ) from None
        if not isinstance(response, Mapping):
            raise SemanticCoverageAuditError("semantic coverage response failed")
        page = response.get("Items", [])
        if not isinstance(page, list) or not all(
            isinstance(item, Mapping) for item in page
        ):
            raise SemanticCoverageAuditError("semantic coverage response failed")
        items.extend(dict(item) for item in page)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items


__all__ = [
    "AUDIT_VERSION",
    "BACKFILL_NOT_REQUIRED",
    "BLOCKED",
    "READY_FOR_BACKFILL",
    "SemanticCoverageAuditError",
    "SemanticCoverageReport",
    "audit_semantic_coverage",
    "collect_eligible_entries",
    "collect_semantic_records",
]
