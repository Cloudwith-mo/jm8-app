"""Tenant-isolated DynamoDB vector retrieval for active semantic chunks."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from numbers import Real
from time import sleep
from typing import NotRequired, Protocol, TypedDict

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import BotoCoreError, ClientError

from semantic_chunking import CHUNKING_VERSION
from semantic_embedding_contract import (
    EMBEDDING_INDEX_NAME,
    EMBEDDING_PARTITION_ATTRIBUTE,
    SemanticEmbeddingContractError,
    embedding_partition,
    validate_embedding_vector,
    validate_stored_embedding,
)
from semantic_embedding_store import (
    SemanticEmbeddingPersistenceError,
    validate_embedding_chunk_identity,
)
from semantic_memory_store import (
    MANIFEST_ENTITY_TYPE,
    semantic_memory_manifest_key,
)


DEFAULT_TOP_K = 12
MAX_TOP_K = 24
MAX_BATCH_GET_ATTEMPTS = 4
BASE_RETRY_DELAY_SECONDS = 0.01

_AWS_ERRORS = (ClientError, BotoCoreError)
_DESERIALIZER = TypeDeserializer()


class SemanticVectorRetrievalError(RuntimeError):
    """Privacy-safe semantic retrieval failure."""


class SemanticVectorSearchClient(Protocol):
    """DynamoDB operations needed by tenant-isolated retrieval."""

    def search_vectors(self, **kwargs: object) -> Mapping[str, object]:
        """Search one exact vector index."""

    def batch_get_item(self, **kwargs: object) -> Mapping[str, object]:
        """Strongly hydrate exact base-table records."""


class SemanticVectorMatch(TypedDict):
    score: float
    entryId: str
    chunkId: str
    chunkOrdinal: int
    chunkCount: int
    text: str
    contentDigest: str
    canonicalTextField: str
    sourceType: str
    entryCreatedAt: NotRequired[object]
    entryUpdatedAt: NotRequired[object]


def retrieve_semantic_chunks(
    client: SemanticVectorSearchClient,
    *,
    table_name: str,
    user_id: str,
    query_vector: object,
    top_k: int = DEFAULT_TOP_K,
) -> list[SemanticVectorMatch]:
    """Search, hydrate, and revalidate current chunks for one tenant.

    The caller supplies the authenticated user identity. Search partition input
    is derived internally and never accepted as a request parameter.
    """

    if not isinstance(table_name, str) or not table_name.strip():
        raise SemanticVectorRetrievalError("semantic retrieval table is invalid")
    if not isinstance(user_id, str) or not user_id.strip():
        raise SemanticVectorRetrievalError("semantic retrieval identity is invalid")
    if (
        not isinstance(top_k, int)
        or isinstance(top_k, bool)
        or top_k < 1
        or top_k > MAX_TOP_K
    ):
        raise SemanticVectorRetrievalError("semantic retrieval limit is invalid")

    try:
        vector = validate_embedding_vector(query_vector)
        tenant_partition = embedding_partition(user_id)
    except SemanticEmbeddingContractError:
        raise SemanticVectorRetrievalError(
            "semantic retrieval query is invalid"
        ) from None

    try:
        response = client.search_vectors(
            TableName=table_name,
            IndexName=EMBEDDING_INDEX_NAME,
            SearchVector=[{"N": str(component)} for component in vector],
            TopK=top_k,
            ProjectionExpression="PK, SK",
            SearchConditionExpression="#tenant = :tenant",
            ExpressionAttributeNames={
                "#tenant": EMBEDDING_PARTITION_ATTRIBUTE,
            },
            ExpressionAttributeValues={
                ":tenant": {"S": tenant_partition},
            },
            ReturnConsumedCapacity="NONE",
        )
    except _AWS_ERRORS:
        raise SemanticVectorRetrievalError(
            "semantic vector search failed"
        ) from None

    ranked_keys = _validated_search_results(response, user_id, top_k)
    if not ranked_keys:
        return []

    chunk_items = _batch_get(
        client,
        table_name,
        [key for key, _score_value in ranked_keys],
    )
    chunks_by_key = _items_by_key(chunk_items, user_id)

    manifest_keys: list[dict[str, dict[str, str]]] = []
    seen_manifest_keys: set[tuple[str, str]] = set()
    validated_chunks: dict[tuple[str, str], tuple[Mapping[str, object], str, str]] = {}
    for key, _score_value in ranked_keys:
        identity = (key["PK"]["S"], key["SK"]["S"])
        chunk = chunks_by_key.get(identity)
        if chunk is None:
            continue
        try:
            chunk_user, entry_id, content_digest, _chunk_id, _ordinal = (
                validate_embedding_chunk_identity(chunk)
            )
            validate_stored_embedding(
                chunk,
                user_id=user_id,
                content_digest=content_digest,
            )
        except (SemanticEmbeddingPersistenceError, SemanticEmbeddingContractError):
            raise SemanticVectorRetrievalError(
                "semantic retrieval data integrity check failed"
            ) from None
        if chunk_user != user_id:
            raise SemanticVectorRetrievalError(
                "semantic retrieval tenant check failed"
            )
        manifest_key = semantic_memory_manifest_key(user_id, entry_id)
        native_manifest_key = {
            "PK": {"S": manifest_key["PK"]},
            "SK": {"S": manifest_key["SK"]},
        }
        manifest_identity = (manifest_key["PK"], manifest_key["SK"])
        if manifest_identity not in seen_manifest_keys:
            manifest_keys.append(native_manifest_key)
            seen_manifest_keys.add(manifest_identity)
        validated_chunks[identity] = (chunk, entry_id, content_digest)

    manifests = _batch_get(client, table_name, manifest_keys)
    manifests_by_key = _items_by_key(manifests, user_id)

    matches: list[SemanticVectorMatch] = []
    for key, score in ranked_keys:
        identity = (key["PK"]["S"], key["SK"]["S"])
        validated = validated_chunks.get(identity)
        if validated is None:
            continue
        chunk, entry_id, content_digest = validated
        manifest_key = semantic_memory_manifest_key(user_id, entry_id)
        manifest = manifests_by_key.get((manifest_key["PK"], manifest_key["SK"]))
        if manifest is None:
            continue
        if not _manifest_is_current(
            manifest,
            user_id=user_id,
            entry_id=entry_id,
            content_digest=content_digest,
            chunk_count=chunk["chunkCount"],
        ):
            continue
        match: SemanticVectorMatch = {
            "score": score,
            "entryId": entry_id,
            "chunkId": chunk["chunkId"],  # type: ignore[typeddict-item]
            "chunkOrdinal": chunk["chunkOrdinal"],  # type: ignore[typeddict-item]
            "chunkCount": chunk["chunkCount"],  # type: ignore[typeddict-item]
            "text": chunk["text"],  # type: ignore[typeddict-item]
            "contentDigest": content_digest,
            "canonicalTextField": _required_string(
                chunk.get("canonicalTextField")
            ),
            "sourceType": _required_string(chunk.get("sourceType")),
        }
        for field in ("entryCreatedAt", "entryUpdatedAt"):
            if field in chunk:
                match[field] = chunk[field]
        matches.append(match)
    return matches


def _validated_search_results(
    response: object,
    user_id: str,
    top_k: int,
) -> list[tuple[dict[str, dict[str, str]], float]]:
    if not isinstance(response, Mapping):
        raise SemanticVectorRetrievalError("semantic vector search response is invalid")
    results = response.get("SearchResults")
    if not isinstance(results, list) or len(results) > top_k:
        raise SemanticVectorRetrievalError("semantic vector search response is invalid")

    expected_pk = f"USER#{user_id}"
    ranked: list[tuple[dict[str, dict[str, str]], float]] = []
    seen: set[tuple[str, str]] = set()
    previous_score: float | None = None
    for result in results:
        if not isinstance(result, Mapping):
            raise SemanticVectorRetrievalError(
                "semantic vector search response is invalid"
            )
        item = result.get("Item")
        if not isinstance(item, Mapping):
            raise SemanticVectorRetrievalError(
                "semantic vector search response is invalid"
            )
        pk = _string_attribute(item.get("PK"))
        sk = _string_attribute(item.get("SK"))
        score = _score(result.get("Score"))
        identity = (pk, sk)
        if pk != expected_pk:
            raise SemanticVectorRetrievalError(
                "semantic retrieval tenant check failed"
            )
        if identity in seen or (
            previous_score is not None and score < previous_score
        ):
            raise SemanticVectorRetrievalError(
                "semantic vector search response is invalid"
            )
        seen.add(identity)
        previous_score = score
        ranked.append((
            {"PK": {"S": pk}, "SK": {"S": sk}},
            score,
        ))
    return ranked


def _batch_get(
    client: SemanticVectorSearchClient,
    table_name: str,
    keys: list[dict[str, dict[str, str]]],
) -> list[Mapping[str, object]]:
    if not keys:
        return []
    requested_identities = {_native_key_identity(key) for key in keys}
    if len(requested_identities) != len(keys):
        raise SemanticVectorRetrievalError(
            "semantic retrieval hydration request is invalid"
        )
    pending = keys
    items: list[Mapping[str, object]] = []
    for attempt in range(MAX_BATCH_GET_ATTEMPTS):
        try:
            response = client.batch_get_item(RequestItems={
                table_name: {
                    "Keys": pending,
                    "ConsistentRead": True,
                }
            })
        except _AWS_ERRORS:
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration failed"
            ) from None
        if not isinstance(response, Mapping):
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        responses = response.get("Responses")
        if not isinstance(responses, Mapping):
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        page = responses.get(table_name, [])
        if not isinstance(page, list) or not all(
            isinstance(item, Mapping) for item in page
        ):
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        deserialized_page = [_deserialize_item(item) for item in page]
        for item in deserialized_page:
            identity = (item.get("PK"), item.get("SK"))
            if identity not in requested_identities:
                raise SemanticVectorRetrievalError(
                    "semantic retrieval hydration response is invalid"
                )
        items.extend(deserialized_page)
        unprocessed = response.get("UnprocessedKeys") or {}
        if not isinstance(unprocessed, Mapping):
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        table_unprocessed = unprocessed.get(table_name)
        if not table_unprocessed:
            return items
        if not isinstance(table_unprocessed, Mapping):
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        next_keys = table_unprocessed.get("Keys")
        if not isinstance(next_keys, list) or not next_keys:
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        next_identities = [_native_key_identity(key) for key in next_keys]
        pending_identities = {_native_key_identity(key) for key in pending}
        if (
            len(set(next_identities)) != len(next_identities)
            or not set(next_identities).issubset(pending_identities)
        ):
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        pending = next_keys
        if attempt + 1 < MAX_BATCH_GET_ATTEMPTS:
            sleep(BASE_RETRY_DELAY_SECONDS * (2**attempt))
    raise SemanticVectorRetrievalError("semantic retrieval hydration retry failed")


def _deserialize_item(item: Mapping[str, object]) -> Mapping[str, object]:
    try:
        return {
            name: _DESERIALIZER.deserialize(value)
            for name, value in item.items()
        }
    except (TypeError, ValueError):
        raise SemanticVectorRetrievalError(
            "semantic retrieval hydration response is invalid"
        ) from None


def _items_by_key(
    items: Sequence[Mapping[str, object]],
    user_id: str,
) -> dict[tuple[str, str], Mapping[str, object]]:
    expected_pk = f"USER#{user_id}"
    by_key: dict[tuple[str, str], Mapping[str, object]] = {}
    for item in items:
        pk = item.get("PK")
        sk = item.get("SK")
        if not isinstance(pk, str) or not isinstance(sk, str):
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        if pk != expected_pk:
            raise SemanticVectorRetrievalError(
                "semantic retrieval tenant check failed"
            )
        identity = (pk, sk)
        if identity in by_key:
            raise SemanticVectorRetrievalError(
                "semantic retrieval hydration response is invalid"
            )
        by_key[identity] = item
    return by_key


def _manifest_is_current(
    manifest: Mapping[str, object],
    *,
    user_id: str,
    entry_id: str,
    content_digest: str,
    chunk_count: object,
) -> bool:
    expected_key = semantic_memory_manifest_key(user_id, entry_id)
    if (
        manifest.get("PK") != expected_key["PK"]
        or manifest.get("SK") != expected_key["SK"]
        or manifest.get("entityType") != MANIFEST_ENTITY_TYPE
        or manifest.get("userId") != user_id
        or manifest.get("entryId") != entry_id
        or manifest.get("chunkingVersion") != CHUNKING_VERSION
        or _integer_value(manifest.get("chunkCount")) != _integer_value(chunk_count)
    ):
        raise SemanticVectorRetrievalError(
            "semantic retrieval data integrity check failed"
        )
    return manifest.get("activeContentDigest") == content_digest


def _native_key_identity(key: object) -> tuple[str, str]:
    if not isinstance(key, Mapping) or set(key) != {"PK", "SK"}:
        raise SemanticVectorRetrievalError(
            "semantic retrieval hydration response is invalid"
        )
    return _string_attribute(key.get("PK")), _string_attribute(key.get("SK"))


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


def _string_attribute(value: object) -> str:
    if not isinstance(value, Mapping) or set(value) != {"S"}:
        raise SemanticVectorRetrievalError(
            "semantic vector search response is invalid"
        )
    string = value.get("S")
    if not isinstance(string, str) or not string:
        raise SemanticVectorRetrievalError(
            "semantic vector search response is invalid"
        )
    return string


def _score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise SemanticVectorRetrievalError(
            "semantic vector search response is invalid"
        )
    score = float(value)
    if not math.isfinite(score):
        raise SemanticVectorRetrievalError(
            "semantic vector search response is invalid"
        )
    return score


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticVectorRetrievalError(
            "semantic retrieval data integrity check failed"
        )
    return value


__all__ = [
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
    "SemanticVectorMatch",
    "SemanticVectorRetrievalError",
    "retrieve_semantic_chunks",
]
