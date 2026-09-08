"""Query embedding and tenant-safe semantic evidence orchestration."""

from __future__ import annotations

from typing import Literal, TypedDict

from insights_ask_context import AskContextInputError, normalize_question
from semantic_chunking import sha256_digest
from semantic_embedding_contract import (
    SemanticEmbeddingContractError,
    embedding_partition,
    validate_stored_embedding,
)
from semantic_embedding_provider import (
    BedrockRuntimeClient,
    SemanticEmbeddingProviderError,
    embed_canonical_text,
)
from semantic_vector_retrieval import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    SemanticVectorMatch,
    SemanticVectorRetrievalError,
    SemanticVectorSearchClient,
    retrieve_semantic_chunks,
)


SEMANTIC_QUERY_RETRIEVAL_VERSION = "1.0"


class SemanticQueryInputError(ValueError):
    """Raised before any provider call when query inputs are invalid."""


class SemanticQueryUnavailableError(RuntimeError):
    """Raised when embedding or retrieval fails without exposing private data."""


class QueryEmbeddingMetadata(TypedDict):
    modelId: str
    version: str
    dimensions: int
    normalized: bool
    inputTokenCount: int


class SemanticQueryEvidence(TypedDict):
    semanticRetrievalVersion: str
    semanticRetrievalStatus: Literal["EMPTY", "READY"]
    evidenceCount: int
    retrievalLimit: int
    queryEmbedding: QueryEmbeddingMetadata
    evidence: list[SemanticVectorMatch]


def retrieve_semantic_query_evidence(
    bedrock_client: BedrockRuntimeClient,
    dynamodb_client: SemanticVectorSearchClient,
    *,
    table_name: str,
    user_id: str,
    question: object,
    top_k: int = DEFAULT_TOP_K,
) -> SemanticQueryEvidence:
    """Embed one normalized question and retrieve current evidence for its user."""

    if not isinstance(table_name, str) or not table_name.strip():
        raise SemanticQueryInputError("semantic query table is invalid")
    if (
        not isinstance(top_k, int)
        or isinstance(top_k, bool)
        or top_k < 1
        or top_k > MAX_TOP_K
    ):
        raise SemanticQueryInputError("semantic query limit is invalid")

    try:
        embedding_partition(user_id)
        normalized_question = normalize_question(question)
    except (AskContextInputError, SemanticEmbeddingContractError):
        raise SemanticQueryInputError("semantic query input is invalid") from None

    question_digest = sha256_digest(normalized_question)
    try:
        embedded = embed_canonical_text(
            bedrock_client,
            canonical_text=normalized_question,
            user_id=user_id,
            content_digest=question_digest,
        )
    except SemanticEmbeddingContractError:
        raise SemanticQueryInputError("semantic query input is invalid") from None
    except SemanticEmbeddingProviderError:
        raise SemanticQueryUnavailableError(
            "semantic query embedding is unavailable"
        ) from None

    try:
        validated_embedding = validate_stored_embedding(
            embedded,
            user_id=user_id,
            content_digest=question_digest,
        )
    except SemanticEmbeddingContractError:
        raise SemanticQueryUnavailableError(
            "semantic query embedding failed validation"
        ) from None

    try:
        matches = retrieve_semantic_chunks(
            dynamodb_client,
            table_name=table_name,
            user_id=user_id,
            query_vector=validated_embedding["embedding"],
            top_k=top_k,
        )
    except SemanticVectorRetrievalError:
        raise SemanticQueryUnavailableError(
            "semantic query retrieval is unavailable"
        ) from None

    evidence = list(matches)
    return {
        "semanticRetrievalVersion": SEMANTIC_QUERY_RETRIEVAL_VERSION,
        "semanticRetrievalStatus": "READY" if evidence else "EMPTY",
        "evidenceCount": len(evidence),
        "retrievalLimit": top_k,
        "queryEmbedding": {
            "modelId": validated_embedding["embeddingModelId"],
            "version": validated_embedding["embeddingVersion"],
            "dimensions": validated_embedding["embeddingDimensions"],
            "normalized": validated_embedding["embeddingNormalized"],
            "inputTokenCount": validated_embedding["embeddingInputTokenCount"],
        },
        "evidence": evidence,
    }


__all__ = [
    "QueryEmbeddingMetadata",
    "SEMANTIC_QUERY_RETRIEVAL_VERSION",
    "SemanticQueryEvidence",
    "SemanticQueryInputError",
    "SemanticQueryUnavailableError",
    "retrieve_semantic_query_evidence",
]
