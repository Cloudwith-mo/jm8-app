"""Injected DynamoDB persistence for canonical semantic-memory chunks."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import Decimal
from time import sleep
from typing import NotRequired, TypedDict

from botocore.exceptions import BotoCoreError, ClientError

from semantic_chunking import (
    CHUNKING_VERSION,
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_OVERLAP_SIZE,
    sha256_digest,
)
from semantic_memory_contract import (
    EntrySemanticChunk,
    SemanticMemoryIdentityError,
    build_entry_semantic_chunks,
)


MANIFEST_ENTITY_TYPE = "SEMANTIC_MEMORY_MANIFEST"
CHUNK_ENTITY_TYPE = "SEMANTIC_CHUNK"
MAX_BATCH_SIZE = 25
MAX_UNPROCESSED_RETRIES = 4
BASE_RETRY_DELAY_SECONDS = 0.01

_AWS_ERRORS = (ClientError, BotoCoreError)
_HEX_DIGITS = frozenset("0123456789abcdef")


class SemanticMemoryStoreError(RuntimeError):
    """Privacy-safe semantic-memory persistence failure."""


class SemanticMemoryIntegrityError(SemanticMemoryStoreError):
    """Stored semantic-memory records do not form a complete generation."""


class SemanticMemoryConflictError(SemanticMemoryStoreError):
    """A concurrent writer published a different semantic-memory generation."""


class _ConditionalPutConflict(Exception):
    """Internal signal for a privacy-safe conditional manifest conflict."""


class SemanticMemoryManifest(TypedDict):
    PK: str
    SK: str
    entityType: str
    userId: str
    entryId: str
    activeContentDigest: str
    chunkCount: int
    chunkingVersion: str
    canonicalTextField: str
    sourceType: str
    entryCreatedAt: NotRequired[object]
    entryUpdatedAt: NotRequired[object]


class StoredSemanticChunk(EntrySemanticChunk):
    PK: str
    SK: str
    entityType: str
    generationId: str
    replayToken: NotRequired[str]


class ReplaceEntryMemoryResult(TypedDict):
    entryId: str
    userId: str
    contentDigest: str | None
    chunkCount: int
    chunkingVersion: str
    status: str


def replace_entry_memory(
    table: object,
    entry: Mapping[str, object],
    *,
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
    overlap_size: int = DEFAULT_OVERLAP_SIZE,
    replay_token: str | None = None,
) -> ReplaceEntryMemoryResult:
    """Atomically publish a complete generation from a reader's perspective."""

    entry_id, user_id = _entry_identity(entry)
    _validate_chunk_configuration(max_chunk_size, overlap_size)
    if (
        replay_token is not None
        and (
            not isinstance(replay_token, str)
            or not replay_token.strip()
            or replay_token != replay_token.strip()
            or len(replay_token) > 128
        )
    ):
        raise SemanticMemoryStoreError(
            "semantic memory replay token is invalid"
        )
    chunks = build_entry_semantic_chunks(
        entry,
        max_chunk_size=max_chunk_size,
        overlap_size=overlap_size,
    )

    if not chunks:
        delete_entry_memory(table, user_id, entry_id)
        return {
            "entryId": entry_id,
            "userId": user_id,
            "contentDigest": None,
            "chunkCount": 0,
            "chunkingVersion": CHUNKING_VERSION,
            "status": "DELETED",
        }

    content_digest = chunks[0]["contentDigest"]
    previous_manifest = _read_manifest(table, user_id, entry_id)
    if previous_manifest is not None:
        _validate_manifest(
            previous_manifest,
            user_id=user_id,
            entry_id=entry_id,
            pk=_user_pk(user_id),
            manifest_sk=_manifest_sk(entry_id),
        )
    existing_items = _query_entry_records(table, user_id, entry_id)
    stored_chunks = [
        _stored_chunk(
            user_id,
            entry_id,
            chunk,
            replay_token=replay_token,
        )
        for chunk in chunks
    ]
    active_keys = {(item["PK"], item["SK"]) for item in stored_chunks}

    for item in stored_chunks:
        _put_item(table, item)

    manifest = _manifest_record(user_id, entry_id, chunks)
    try:
        _put_item(
            table,
            manifest,
            **_manifest_condition(previous_manifest),
        )
    except _ConditionalPutConflict:
        current_manifest = _read_manifest(table, user_id, entry_id)
        if (
            current_manifest is not None
            and current_manifest.get("activeContentDigest") == content_digest
        ):
            active_chunks = get_entry_memory(table, user_id, entry_id)
            return _active_result(
                entry_id,
                user_id,
                content_digest,
                len(active_chunks),
            )
        raise SemanticMemoryConflictError(
            "semantic memory replacement conflicted"
        ) from None

    stale_keys = [
        {"PK": item.get("PK"), "SK": item.get("SK")}
        for item in existing_items
        if _is_string_key(item)
        and item.get("SK") != manifest["SK"]
        and (item["PK"], item["SK"]) not in active_keys
    ]
    _delete_keys(table, stale_keys)

    return _active_result(
        entry_id,
        user_id,
        content_digest,
        len(chunks),
    )


def get_entry_memory(
    table: object,
    user_id: str,
    entry_id: str,
) -> list[StoredSemanticChunk]:
    """Read and validate the manifest's complete active generation."""

    _validate_identity(user_id, entry_id)
    pk = _user_pk(user_id)
    manifest_sk = _manifest_sk(entry_id)
    manifest = _read_manifest(table, user_id, entry_id)
    if manifest is None:
        return []

    active_digest, expected_count = _validate_manifest(
        manifest,
        user_id=user_id,
        entry_id=entry_id,
        pk=pk,
        manifest_sk=manifest_sk,
    )
    prefix = _generation_prefix(entry_id, active_digest)
    raw_chunks = _query_records(
        table,
        pk=pk,
        sk_prefix=prefix,
        consistent_read=True,
    )
    return _validated_chunks(
        raw_chunks,
        user_id=user_id,
        entry_id=entry_id,
        pk=pk,
        active_digest=active_digest,
        expected_count=expected_count,
    )


def delete_entry_memory(table: object, user_id: str, entry_id: str) -> int:
    """Delete every record under one user's exact opaque entry prefix."""

    _validate_identity(user_id, entry_id)
    items = _query_entry_records(table, user_id, entry_id)
    keys = [
        {"PK": item["PK"], "SK": item["SK"]}
        for item in items
        if _is_string_key(item)
    ]
    _delete_keys(table, keys)
    return len(keys)


def delete_user_memory(table: object, user_id: str) -> int:
    """Delete all semantic-memory records in one exact user partition."""

    _validate_non_empty_string(user_id, "userId")
    pk = _user_pk(user_id)
    items = _query_records(table, pk=pk, consistent_read=True)
    keys = [
        {"PK": item["PK"], "SK": item["SK"]}
        for item in items
        if _is_string_key(item)
    ]
    _delete_keys(table, keys)
    return len(keys)


def semantic_memory_manifest_key(user_id: str, entry_id: str) -> dict[str, str]:
    """Return the exact key for one tenant-owned active-generation manifest."""

    _validate_identity(user_id, entry_id)
    return {"PK": _user_pk(user_id), "SK": _manifest_sk(entry_id)}


def _entry_identity(entry: Mapping[str, object]) -> tuple[str, str]:
    if not isinstance(entry, Mapping):
        raise SemanticMemoryIdentityError("entry must be a mapping")
    entry_id = entry.get("entryId")
    user_id = entry.get("userId")
    _validate_non_empty_string(entry_id, "entryId")
    _validate_non_empty_string(user_id, "userId")
    return entry_id, user_id


def _validate_identity(user_id: object, entry_id: object) -> None:
    _validate_non_empty_string(user_id, "userId")
    _validate_non_empty_string(entry_id, "entryId")


def _validate_non_empty_string(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SemanticMemoryIdentityError(f"{field} must be a non-empty string")


def _validate_chunk_configuration(max_chunk_size: object, overlap_size: object) -> None:
    if (
        not isinstance(max_chunk_size, int)
        or isinstance(max_chunk_size, bool)
        or max_chunk_size < 1
    ):
        raise SemanticMemoryStoreError("invalid semantic memory configuration")
    if (
        not isinstance(overlap_size, int)
        or isinstance(overlap_size, bool)
        or overlap_size < 0
        or overlap_size >= max_chunk_size
    ):
        raise SemanticMemoryStoreError("invalid semantic memory configuration")


def _user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _entry_token(entry_id: str) -> str:
    return hashlib.sha256(entry_id.encode("utf-8")).hexdigest()


def _entry_prefix(entry_id: str) -> str:
    return f"ENTRY#{_entry_token(entry_id)}#"


def _manifest_sk(entry_id: str) -> str:
    return f"{_entry_prefix(entry_id)}MANIFEST"


def _generation_prefix(entry_id: str, content_digest: str) -> str:
    return f"{_entry_prefix(entry_id)}GEN#{content_digest}#CHUNK#"


def _chunk_sk(
    entry_id: str,
    content_digest: str,
    ordinal: int,
    chunk_id: str,
) -> str:
    return (
        f"{_generation_prefix(entry_id, content_digest)}"
        f"{ordinal:08d}#{chunk_id}"
    )


def _stored_chunk(
    user_id: str,
    entry_id: str,
    chunk: EntrySemanticChunk,
    *,
    replay_token: str | None = None,
) -> StoredSemanticChunk:
    content_digest = chunk["contentDigest"]
    stored: StoredSemanticChunk = {
        "PK": _user_pk(user_id),
        "SK": _chunk_sk(
            entry_id,
            content_digest,
            chunk["chunkOrdinal"],
            chunk["chunkId"],
        ),
        "entityType": CHUNK_ENTITY_TYPE,
        **chunk,
        "generationId": content_digest,
    }
    if replay_token is not None:
        stored["replayToken"] = replay_token
    return stored


def _manifest_record(
    user_id: str,
    entry_id: str,
    chunks: list[EntrySemanticChunk],
) -> SemanticMemoryManifest:
    first = chunks[0]
    manifest: SemanticMemoryManifest = {
        "PK": _user_pk(user_id),
        "SK": _manifest_sk(entry_id),
        "entityType": MANIFEST_ENTITY_TYPE,
        "userId": user_id,
        "entryId": entry_id,
        "activeContentDigest": first["contentDigest"],
        "chunkCount": len(chunks),
        "chunkingVersion": first["chunkingVersion"],
        "canonicalTextField": first["canonicalTextField"],
        "sourceType": first["sourceType"],
    }
    if "entryCreatedAt" in first:
        manifest["entryCreatedAt"] = first["entryCreatedAt"]
    if "entryUpdatedAt" in first:
        manifest["entryUpdatedAt"] = first["entryUpdatedAt"]
    return manifest


def _manifest_condition(
    previous_manifest: Mapping[str, object] | None,
) -> dict[str, object]:
    if previous_manifest is None:
        return {
            "ConditionExpression": (
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            )
        }
    return {
        "ConditionExpression": (
            "#activeContentDigest = :previousContentDigest "
            "AND #chunkingVersion = :previousChunkingVersion"
        ),
        "ExpressionAttributeNames": {
            "#activeContentDigest": "activeContentDigest",
            "#chunkingVersion": "chunkingVersion",
        },
        "ExpressionAttributeValues": {
            ":previousContentDigest": previous_manifest["activeContentDigest"],
            ":previousChunkingVersion": previous_manifest["chunkingVersion"],
        },
    }


def _active_result(
    entry_id: str,
    user_id: str,
    content_digest: str,
    chunk_count: int,
) -> ReplaceEntryMemoryResult:
    return {
        "entryId": entry_id,
        "userId": user_id,
        "contentDigest": content_digest,
        "chunkCount": chunk_count,
        "chunkingVersion": CHUNKING_VERSION,
        "status": "ACTIVE",
    }


def _validate_manifest(
    manifest: Mapping[str, object],
    *,
    user_id: str,
    entry_id: str,
    pk: str,
    manifest_sk: str,
) -> tuple[str, int]:
    if (
        manifest.get("entityType") != MANIFEST_ENTITY_TYPE
        or manifest.get("PK") != pk
        or manifest.get("SK") != manifest_sk
        or manifest.get("userId") != user_id
        or manifest.get("entryId") != entry_id
        or manifest.get("chunkingVersion") != CHUNKING_VERSION
    ):
        raise SemanticMemoryIntegrityError("semantic memory manifest is inconsistent")

    active_digest = manifest.get("activeContentDigest")
    if not _is_sha256_digest(active_digest):
        raise SemanticMemoryIntegrityError("semantic memory manifest digest is invalid")
    chunk_count = _integer_value(manifest.get("chunkCount"))
    if chunk_count is None or chunk_count < 1:
        raise SemanticMemoryIntegrityError("semantic memory manifest count is invalid")
    return active_digest, chunk_count


def _validated_chunks(
    items: list[Mapping[str, object]],
    *,
    user_id: str,
    entry_id: str,
    pk: str,
    active_digest: str,
    expected_count: int,
) -> list[StoredSemanticChunk]:
    by_ordinal: dict[int, StoredSemanticChunk] = {}
    chunk_ids: set[str] = set()

    for item in items:
        ordinal = _integer_value(item.get("chunkOrdinal"))
        item_count = _integer_value(item.get("chunkCount"))
        chunk_id = item.get("chunkId")
        text = item.get("text")
        chunk_digest = item.get("chunkDigest")
        character_count = _integer_value(item.get("characterCount"))
        word_count = _integer_value(item.get("wordCount"))
        expected_sk = (
            _chunk_sk(entry_id, active_digest, ordinal, chunk_id)
            if ordinal is not None and isinstance(chunk_id, str)
            else None
        )
        expected_chunk_id = (
            _expected_chunk_id(
                entry_id,
                active_digest,
                ordinal,
                chunk_digest,
            )
            if ordinal is not None and _is_sha256_digest(chunk_digest)
            else None
        )
        if (
            item.get("entityType") != CHUNK_ENTITY_TYPE
            or item.get("PK") != pk
            or item.get("SK") != expected_sk
            or item.get("userId") != user_id
            or item.get("entryId") != entry_id
            or item.get("contentDigest") != active_digest
            or item.get("generationId") != active_digest
            or item.get("chunkingVersion") != CHUNKING_VERSION
            or ordinal is None
            or ordinal < 0
            or ordinal >= expected_count
            or item_count != expected_count
            or not isinstance(chunk_id, str)
            or chunk_id != expected_chunk_id
            or not isinstance(text, str)
            or not _is_sha256_digest(chunk_digest)
            or sha256_digest(text) != chunk_digest
            or character_count != len(text)
            or word_count != len(text.split())
        ):
            raise SemanticMemoryIntegrityError("semantic memory chunk is inconsistent")
        if ordinal in by_ordinal or chunk_id in chunk_ids:
            raise SemanticMemoryIntegrityError("semantic memory chunk is not unique")
        by_ordinal[ordinal] = dict(item)  # type: ignore[assignment]
        chunk_ids.add(chunk_id)

    if len(by_ordinal) != expected_count or set(by_ordinal) != set(range(expected_count)):
        raise SemanticMemoryIntegrityError("semantic memory generation is incomplete")
    return [by_ordinal[ordinal] for ordinal in range(expected_count)]


def _expected_chunk_id(
    entry_id: str,
    content_digest: str,
    ordinal: int,
    chunk_digest: str,
) -> str:
    identity = "\0".join(
        (
            CHUNKING_VERSION,
            entry_id,
            content_digest,
            str(ordinal),
            chunk_digest,
        )
    )
    return f"chunk_{sha256_digest(identity)}"


def _integer_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        integral = value.to_integral_value()
        if value == integral:
            return int(integral)
    return None


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


def _is_string_key(item: Mapping[str, object]) -> bool:
    return isinstance(item.get("PK"), str) and isinstance(item.get("SK"), str)


def _query_entry_records(
    table: object,
    user_id: str,
    entry_id: str,
) -> list[Mapping[str, object]]:
    return _query_records(
        table,
        pk=_user_pk(user_id),
        sk_prefix=_entry_prefix(entry_id),
        consistent_read=True,
    )


def _read_manifest(
    table: object,
    user_id: str,
    entry_id: str,
) -> Mapping[str, object] | None:
    response = _get_item(
        table,
        Key={"PK": _user_pk(user_id), "SK": _manifest_sk(entry_id)},
        ConsistentRead=True,
    )
    manifest = response.get("Item")
    if manifest is None:
        return None
    if not isinstance(manifest, Mapping):
        raise SemanticMemoryIntegrityError("semantic memory manifest is invalid")
    return manifest


def _query_records(
    table: object,
    *,
    pk: str,
    sk_prefix: str | None = None,
    consistent_read: bool = False,
) -> list[Mapping[str, object]]:
    items: list[Mapping[str, object]] = []
    last_key: object = None

    while True:
        values: dict[str, str] = {":pk": pk}
        expression = "PK = :pk"
        if sk_prefix is not None:
            expression += " AND begins_with(SK, :sk_prefix)"
            values[":sk_prefix"] = sk_prefix
        kwargs: dict[str, object] = {
            "KeyConditionExpression": expression,
            "ExpressionAttributeValues": values,
        }
        if consistent_read:
            kwargs["ConsistentRead"] = True
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key

        try:
            response = table.query(**kwargs)  # type: ignore[attr-defined]
        except _AWS_ERRORS:
            raise SemanticMemoryStoreError(
                "semantic memory persistence operation failed"
            ) from None
        page_items = response.get("Items", [])
        if not isinstance(page_items, list):
            raise SemanticMemoryStoreError("semantic memory persistence response failed")
        if not all(isinstance(item, Mapping) for item in page_items):
            raise SemanticMemoryStoreError("semantic memory persistence response failed")
        items.extend(page_items)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items


def _get_item(table: object, **kwargs: object) -> Mapping[str, object]:
    try:
        response = table.get_item(**kwargs)  # type: ignore[attr-defined]
    except _AWS_ERRORS:
        raise SemanticMemoryStoreError(
            "semantic memory persistence operation failed"
        ) from None
    if not isinstance(response, Mapping):
        raise SemanticMemoryStoreError("semantic memory persistence response failed")
    return response


def _put_item(
    table: object,
    item: Mapping[str, object],
    **kwargs: object,
) -> None:
    try:
        table.put_item(Item=dict(item), **kwargs)  # type: ignore[attr-defined]
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if (
            "ConditionExpression" in kwargs
            and code == "ConditionalCheckFailedException"
        ):
            raise _ConditionalPutConflict from None
        raise SemanticMemoryStoreError(
            "semantic memory persistence operation failed"
        ) from None
    except BotoCoreError:
        raise SemanticMemoryStoreError(
            "semantic memory persistence operation failed"
        ) from None


def _delete_keys(table: object, keys: list[dict[str, object]]) -> None:
    if not keys:
        return
    table_name, client = _batch_client(table)

    for offset in range(0, len(keys), MAX_BATCH_SIZE):
        requests = [
            {"DeleteRequest": {"Key": _native_key(key)}}
            for key in keys[offset : offset + MAX_BATCH_SIZE]
        ]
        retries = 0
        while requests:
            try:
                response = client.batch_write_item(  # type: ignore[attr-defined]
                    RequestItems={table_name: requests}
                )
            except _AWS_ERRORS:
                raise SemanticMemoryStoreError(
                    "semantic memory persistence operation failed"
                ) from None
            unprocessed = response.get("UnprocessedItems", {}).get(table_name, [])
            if not unprocessed:
                break
            if not isinstance(unprocessed, list) or retries >= MAX_UNPROCESSED_RETRIES:
                raise SemanticMemoryStoreError("semantic memory deletion retry failed")
            sleep(BASE_RETRY_DELAY_SECONDS * (2**retries))
            requests = unprocessed
            retries += 1


def _batch_client(table: object) -> tuple[str, object]:
    table_name = getattr(table, "name", None) or getattr(table, "table_name", None)
    meta = getattr(table, "meta", None)
    client = getattr(meta, "client", None)
    if not isinstance(table_name, str) or not table_name or client is None:
        raise SemanticMemoryStoreError("semantic memory table dependency is invalid")
    return table_name, client


def _native_key(key: Mapping[str, object]) -> dict[str, str]:
    pk = key.get("PK")
    sk = key.get("SK")
    if not isinstance(pk, str) or not isinstance(sk, str):
        raise SemanticMemoryStoreError(
            "semantic memory persistence key is invalid"
        )
    return {"PK": pk, "SK": sk}


__all__ = [
    "CHUNK_ENTITY_TYPE",
    "MANIFEST_ENTITY_TYPE",
    "MAX_BATCH_SIZE",
    "MAX_UNPROCESSED_RETRIES",
    "ReplaceEntryMemoryResult",
    "SemanticMemoryConflictError",
    "SemanticMemoryIntegrityError",
    "SemanticMemoryManifest",
    "SemanticMemoryStoreError",
    "StoredSemanticChunk",
    "delete_entry_memory",
    "delete_user_memory",
    "get_entry_memory",
    "replace_entry_memory",
    "semantic_memory_manifest_key",
]
