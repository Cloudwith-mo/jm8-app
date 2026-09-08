"""Versioned, provider-specific contract for JournalM8 semantic embeddings."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from numbers import Real
from typing import NotRequired, TypedDict


EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_VERSION = "jm8-titan-text-embedding-v1"
EMBEDDING_DIMENSIONS = 1024
EMBEDDING_NORMALIZED = True
EMBEDDING_DISTANCE_FUNCTION = "COSINE"
EMBEDDING_MAX_INPUT_CHARACTERS = 50_000
EMBEDDING_MAX_INPUT_TOKENS = 8_192

EMBEDDING_VECTOR_ATTRIBUTE = "embedding"
EMBEDDING_PARTITION_ATTRIBUTE = "embeddingPartition"
EMBEDDING_INDEX_NAME = "SemanticEmbeddingIndex"


class SemanticEmbeddingContractError(ValueError):
    """Raised when embedding input, output, or stored metadata is invalid."""


class TitanEmbeddingRequest(TypedDict):
    """Request accepted by Amazon Titan Text Embeddings V2."""

    inputText: str
    dimensions: int
    normalize: bool


class TitanEmbeddingResponse(TypedDict):
    """Required portion of a Titan Text Embeddings V2 response."""

    embedding: list[float]
    inputTextTokenCount: int
    embeddingsByType: NotRequired[Mapping[str, object]]


class StoredSemanticEmbedding(TypedDict):
    """Embedding attributes stored on one semantic chunk."""

    embedding: list[float]
    embeddingModelId: str
    embeddingVersion: str
    embeddingDimensions: int
    embeddingNormalized: bool
    embeddingContentDigest: str
    embeddingPartition: str
    embeddingInputTokenCount: int


def build_embedding_request(canonical_text: str) -> TitanEmbeddingRequest:
    """Build an exact, normalized Titan V2 request without changing the text."""

    if not isinstance(canonical_text, str):
        raise SemanticEmbeddingContractError(
            "canonical embedding text must be a string"
        )
    if not canonical_text.strip():
        raise SemanticEmbeddingContractError(
            "canonical embedding text must not be empty"
        )
    if len(canonical_text) > EMBEDDING_MAX_INPUT_CHARACTERS:
        raise SemanticEmbeddingContractError(
            "canonical embedding text exceeds the provider limit"
        )
    return {
        "inputText": canonical_text,
        "dimensions": EMBEDDING_DIMENSIONS,
        "normalize": EMBEDDING_NORMALIZED,
    }


def embedding_partition(user_id: str) -> str:
    """Return a stable, non-reversible tenant partition for vector search."""

    if not isinstance(user_id, str) or not user_id.strip():
        raise SemanticEmbeddingContractError(
            "embedding user identity must not be empty"
        )
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def validate_content_digest(content_digest: str) -> str:
    """Require the lower-case SHA-256 digest used by semantic chunks."""

    if (
        not isinstance(content_digest, str)
        or len(content_digest) != 64
        or any(character not in "0123456789abcdef" for character in content_digest)
    ):
        raise SemanticEmbeddingContractError(
            "embedding content digest must be a lower-case SHA-256 digest"
        )
    return content_digest


def validate_embedding_response(
    response: Mapping[str, object],
    *,
    user_id: str,
    content_digest: str,
) -> StoredSemanticEmbedding:
    """Validate Titan output and bind it to one tenant and chunk generation."""

    validate_content_digest(content_digest)
    partition = embedding_partition(user_id)
    if not isinstance(response, Mapping):
        raise SemanticEmbeddingContractError(
            "embedding provider response must be an object"
        )

    vector = _validated_vector(response.get("embedding"))
    token_count = _validated_token_count(response.get("inputTextTokenCount"))

    return {
        "embedding": list(vector),
        "embeddingModelId": EMBEDDING_MODEL_ID,
        "embeddingVersion": EMBEDDING_VERSION,
        "embeddingDimensions": EMBEDDING_DIMENSIONS,
        "embeddingNormalized": EMBEDDING_NORMALIZED,
        "embeddingContentDigest": content_digest,
        "embeddingPartition": partition,
        "embeddingInputTokenCount": token_count,
    }


def validate_stored_embedding(
    item: Mapping[str, object],
    *,
    user_id: str,
    content_digest: str,
) -> StoredSemanticEmbedding:
    """Validate that stored vector attributes match the active generation."""

    if not isinstance(item, Mapping):
        raise SemanticEmbeddingContractError(
            "stored semantic embedding must be an object"
        )
    validate_content_digest(content_digest)
    expected_partition = embedding_partition(user_id)

    if item.get("embeddingModelId") != EMBEDDING_MODEL_ID:
        raise SemanticEmbeddingContractError("stored embedding model is invalid")
    if item.get("embeddingVersion") != EMBEDDING_VERSION:
        raise SemanticEmbeddingContractError("stored embedding version is invalid")
    if item.get("embeddingDimensions") != EMBEDDING_DIMENSIONS:
        raise SemanticEmbeddingContractError(
            "stored embedding dimensions are invalid"
        )
    if item.get("embeddingNormalized") is not EMBEDDING_NORMALIZED:
        raise SemanticEmbeddingContractError(
            "stored embedding normalization is invalid"
        )
    if item.get("embeddingContentDigest") != content_digest:
        raise SemanticEmbeddingContractError(
            "stored embedding content digest is stale"
        )
    if item.get("embeddingPartition") != expected_partition:
        raise SemanticEmbeddingContractError(
            "stored embedding tenant partition is invalid"
        )

    vector = _validated_vector(item.get("embedding"))
    token_count = _validated_token_count(item.get("embeddingInputTokenCount"))
    return {
        "embedding": list(vector),
        "embeddingModelId": EMBEDDING_MODEL_ID,
        "embeddingVersion": EMBEDDING_VERSION,
        "embeddingDimensions": EMBEDDING_DIMENSIONS,
        "embeddingNormalized": EMBEDDING_NORMALIZED,
        "embeddingContentDigest": content_digest,
        "embeddingPartition": expected_partition,
        "embeddingInputTokenCount": token_count,
    }


def embedding_is_current(
    item: Mapping[str, object],
    *,
    user_id: str,
    content_digest: str,
) -> bool:
    """Return whether an item has a complete embedding for this generation."""

    try:
        validate_stored_embedding(
            item,
            user_id=user_id,
            content_digest=content_digest,
        )
    except SemanticEmbeddingContractError:
        return False
    return True


def _validated_vector(value: object) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != EMBEDDING_DIMENSIONS
    ):
        raise SemanticEmbeddingContractError(
            "embedding vector must contain exactly 1024 values"
        )

    vector: list[float] = []
    for component in value:
        if isinstance(component, bool) or not isinstance(
            component,
            (Real, Decimal),
        ):
            raise SemanticEmbeddingContractError(
                "embedding vector values must be numeric"
            )
        converted = float(component)
        if not math.isfinite(converted):
            raise SemanticEmbeddingContractError(
                "embedding vector values must be finite"
            )
        vector.append(converted)

    norm = math.sqrt(sum(component * component for component in vector))
    if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
        raise SemanticEmbeddingContractError(
            "embedding vector must be normalized"
        )
    return vector


def _validated_token_count(value: object) -> int:
    if isinstance(value, bool):
        raise SemanticEmbeddingContractError(
            "embedding token count is invalid"
        )

    if isinstance(value, int):
        token_count = value
    elif isinstance(value, Decimal) and value.is_finite():
        integral = value.to_integral_value()
        if value != integral:
            raise SemanticEmbeddingContractError(
                "embedding token count is invalid"
            )
        token_count = int(integral)
    else:
        raise SemanticEmbeddingContractError(
            "embedding token count is invalid"
        )

    if token_count < 1 or token_count > EMBEDDING_MAX_INPUT_TOKENS:
        raise SemanticEmbeddingContractError(
            "embedding token count is invalid"
        )
    return token_count
