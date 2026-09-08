"""Conditional DynamoDB persistence for active semantic embeddings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError

from semantic_chunking import CHUNKING_VERSION, sha256_digest
from semantic_embedding_contract import (
    SemanticEmbeddingContractError,
    StoredSemanticEmbedding,
    validate_content_digest,
    validate_stored_embedding,
)
from semantic_memory_store import CHUNK_ENTITY_TYPE, MANIFEST_ENTITY_TYPE


class SemanticEmbeddingPersistenceError(RuntimeError):
    """Privacy-safe embedding persistence failure."""


class SemanticEmbeddingStaleGenerationError(SemanticEmbeddingPersistenceError):
    """The chunk generation stopped being active before persistence."""


class TransactionClient(Protocol):
    """Resource-bound DynamoDB operation required by the store."""

    def transact_write_items(
        self,
        *,
        TransactItems: list[dict[str, object]],
    ) -> Mapping[str, object]:
        """Condition-check the manifest and update its active chunk."""


def persist_active_embedding(
    table: object,
    chunk: Mapping[str, object],
    embedding: Mapping[str, object],
) -> StoredSemanticEmbedding:
    """Persist an embedding only while its exact chunk generation is active."""

    identity = _validated_chunk_identity(chunk)
    user_id, entry_id, content_digest, chunk_id, ordinal = identity
    validated = validate_stored_embedding(
        embedding,
        user_id=user_id,
        content_digest=content_digest,
    )
    table_name, client = _transaction_client(table)
    request = _transaction_request(
        table_name=table_name,
        chunk=chunk,
        embedding=validated,
        user_id=user_id,
        entry_id=entry_id,
        content_digest=content_digest,
        chunk_id=chunk_id,
        ordinal=ordinal,
    )

    try:
        response = client.transact_write_items(TransactItems=request)
    except ClientError as error:
        if _conditional_transaction_failure(error):
            raise SemanticEmbeddingStaleGenerationError(
                "semantic embedding generation is no longer active"
            ) from None
        raise SemanticEmbeddingPersistenceError(
            "semantic embedding persistence operation failed"
        ) from None
    except BotoCoreError:
        raise SemanticEmbeddingPersistenceError(
            "semantic embedding persistence operation failed"
        ) from None

    if not isinstance(response, Mapping):
        raise SemanticEmbeddingPersistenceError(
            "semantic embedding persistence response failed"
        )
    return validated


def _validated_chunk_identity(
    chunk: Mapping[str, object],
) -> tuple[str, str, str, str, int]:
    if not isinstance(chunk, Mapping):
        raise SemanticEmbeddingPersistenceError(
            "semantic embedding chunk is invalid"
        )

    user_id = chunk.get("userId")
    entry_id = chunk.get("entryId")
    content_digest = chunk.get("contentDigest")
    generation_id = chunk.get("generationId")
    chunk_id = chunk.get("chunkId")
    chunk_digest = chunk.get("chunkDigest")
    ordinal = chunk.get("chunkOrdinal")
    chunk_count = chunk.get("chunkCount")
    character_count = chunk.get("characterCount")
    word_count = chunk.get("wordCount")
    text = chunk.get("text")

    if (
        not isinstance(user_id, str)
        or not user_id.strip()
        or not isinstance(entry_id, str)
        or not entry_id.strip()
        or not isinstance(content_digest, str)
        or not isinstance(generation_id, str)
        or not isinstance(chunk_id, str)
        or not isinstance(chunk_digest, str)
        or not isinstance(ordinal, int)
        or isinstance(ordinal, bool)
        or ordinal < 0
        or not isinstance(chunk_count, int)
        or isinstance(chunk_count, bool)
        or chunk_count < 1
        or ordinal >= chunk_count
        or not isinstance(character_count, int)
        or isinstance(character_count, bool)
        or not isinstance(word_count, int)
        or isinstance(word_count, bool)
        or not isinstance(text, str)
    ):
        raise SemanticEmbeddingPersistenceError(
            "semantic embedding chunk is invalid"
        )

    try:
        validate_content_digest(content_digest)
        validate_content_digest(generation_id)
        validate_content_digest(chunk_digest)
    except SemanticEmbeddingContractError:
        raise SemanticEmbeddingPersistenceError(
            "semantic embedding chunk is invalid"
        ) from None

    expected_chunk_id = _expected_chunk_id(
        entry_id,
        content_digest,
        ordinal,
        chunk_digest,
    )
    expected_pk = f"USER#{user_id}"
    expected_sk = _chunk_sk(
        entry_id,
        content_digest,
        ordinal,
        expected_chunk_id,
    )
    if (
        chunk.get("entityType") != CHUNK_ENTITY_TYPE
        or chunk.get("chunkingVersion") != CHUNKING_VERSION
        or generation_id != content_digest
        or sha256_digest(text) != chunk_digest
        or character_count != len(text)
        or word_count != len(text.split())
        or chunk_id != expected_chunk_id
        or chunk.get("PK") != expected_pk
        or chunk.get("SK") != expected_sk
    ):
        raise SemanticEmbeddingPersistenceError(
            "semantic embedding chunk is invalid"
        )
    return user_id, entry_id, content_digest, chunk_id, ordinal


def _transaction_request(
    *,
    table_name: str,
    chunk: Mapping[str, object],
    embedding: StoredSemanticEmbedding,
    user_id: str,
    entry_id: str,
    content_digest: str,
    chunk_id: str,
    ordinal: int,
) -> list[dict[str, object]]:
    manifest_key = {
        "PK": f"USER#{user_id}",
        "SK": _manifest_sk(entry_id),
    }
    chunk_key = {"PK": chunk["PK"], "SK": chunk["SK"]}
    manifest_check = {
        "TableName": table_name,
        "Key": manifest_key,
        "ConditionExpression": (
            "#entityType = :manifestType "
            "AND #userId = :userId "
            "AND #entryId = :entryId "
            "AND #activeContentDigest = :contentDigest "
            "AND #chunkingVersion = :chunkingVersion"
        ),
        "ExpressionAttributeNames": {
            "#entityType": "entityType",
            "#userId": "userId",
            "#entryId": "entryId",
            "#activeContentDigest": "activeContentDigest",
            "#chunkingVersion": "chunkingVersion",
        },
        "ExpressionAttributeValues": {
            ":manifestType": MANIFEST_ENTITY_TYPE,
            ":userId": user_id,
            ":entryId": entry_id,
            ":contentDigest": content_digest,
            ":chunkingVersion": CHUNKING_VERSION,
        },
    }
    update_names = {
        "#embedding": "embedding",
        "#embeddingModelId": "embeddingModelId",
        "#embeddingVersion": "embeddingVersion",
        "#embeddingDimensions": "embeddingDimensions",
        "#embeddingNormalized": "embeddingNormalized",
        "#embeddingContentDigest": "embeddingContentDigest",
        "#embeddingPartition": "embeddingPartition",
        "#embeddingInputTokenCount": "embeddingInputTokenCount",
        "#entityType": "entityType",
        "#userId": "userId",
        "#entryId": "entryId",
        "#contentDigest": "contentDigest",
        "#generationId": "generationId",
        "#chunkId": "chunkId",
        "#chunkOrdinal": "chunkOrdinal",
        "#chunkCount": "chunkCount",
        "#chunkDigest": "chunkDigest",
        "#characterCount": "characterCount",
        "#wordCount": "wordCount",
        "#chunkingVersion": "chunkingVersion",
    }
    update_values: dict[str, object] = {
        f":{name}": _dynamodb_value(value)
        for name, value in embedding.items()
    }
    update_values.update(
        {
            ":chunkType": CHUNK_ENTITY_TYPE,
            ":userId": user_id,
            ":entryId": entry_id,
            ":contentDigest": content_digest,
            ":chunkId": chunk_id,
            ":chunkOrdinal": ordinal,
            ":chunkCount": chunk["chunkCount"],
            ":chunkDigest": chunk["chunkDigest"],
            ":characterCount": chunk["characterCount"],
            ":wordCount": chunk["wordCount"],
            ":chunkingVersion": CHUNKING_VERSION,
        }
    )
    update = {
        "TableName": table_name,
        "Key": chunk_key,
        "UpdateExpression": "SET " + ", ".join(
            f"#{name} = :{name}" for name in embedding
        ),
        "ConditionExpression": (
            "#entityType = :chunkType "
            "AND #userId = :userId "
            "AND #entryId = :entryId "
            "AND #contentDigest = :contentDigest "
            "AND #generationId = :contentDigest "
            "AND #chunkId = :chunkId "
            "AND #chunkOrdinal = :chunkOrdinal "
            "AND #chunkCount = :chunkCount "
            "AND #chunkDigest = :chunkDigest "
            "AND #characterCount = :characterCount "
            "AND #wordCount = :wordCount "
            "AND #chunkingVersion = :chunkingVersion"
        ),
        "ExpressionAttributeNames": update_names,
        "ExpressionAttributeValues": update_values,
    }
    return [
        {"ConditionCheck": manifest_check},
        {"Update": update},
    ]


def _dynamodb_value(value: object) -> object:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [Decimal(str(component)) for component in value]
    return value


def _transaction_client(table: object) -> tuple[str, TransactionClient]:
    table_name = getattr(table, "name", None) or getattr(
        table,
        "table_name",
        None,
    )
    meta = getattr(table, "meta", None)
    client = getattr(meta, "client", None)
    operation = getattr(client, "transact_write_items", None)
    if (
        not isinstance(table_name, str)
        or not table_name.strip()
        or not callable(operation)
    ):
        raise SemanticEmbeddingPersistenceError(
            "semantic embedding table dependency is invalid"
        )
    return table_name, client


def _conditional_transaction_failure(error: ClientError) -> bool:
    response = error.response
    code = response.get("Error", {}).get("Code")
    if code != "TransactionCanceledException":
        return False
    reasons = response.get("CancellationReasons")
    return isinstance(reasons, list) and any(
        isinstance(reason, Mapping)
        and reason.get("Code") == "ConditionalCheckFailed"
        for reason in reasons
    )


def _entry_token(entry_id: str) -> str:
    return hashlib.sha256(entry_id.encode("utf-8")).hexdigest()


def _entry_prefix(entry_id: str) -> str:
    return f"ENTRY#{_entry_token(entry_id)}#"


def _manifest_sk(entry_id: str) -> str:
    return f"{_entry_prefix(entry_id)}MANIFEST"


def _chunk_sk(
    entry_id: str,
    content_digest: str,
    ordinal: int,
    chunk_id: str,
) -> str:
    return (
        f"{_entry_prefix(entry_id)}GEN#{content_digest}#CHUNK#"
        f"{ordinal:08d}#{chunk_id}"
    )


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


__all__ = [
    "SemanticEmbeddingPersistenceError",
    "SemanticEmbeddingStaleGenerationError",
    "persist_active_embedding",
]
