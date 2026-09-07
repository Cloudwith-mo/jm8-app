"""Amazon Bedrock adapter for the JournalM8 semantic embedding contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, cast

from semantic_embedding_contract import (
    EMBEDDING_MODEL_ID,
    SemanticEmbeddingContractError,
    StoredSemanticEmbedding,
    build_embedding_request,
    embedding_partition,
    validate_content_digest,
    validate_embedding_response,
)


class SemanticEmbeddingProviderError(RuntimeError):
    """Raised for sanitized Bedrock transport or response failures."""


class ResponseBody(Protocol):
    """Readable body returned by the Bedrock Runtime client."""

    def read(self) -> object:
        """Read the complete provider response body."""


class BedrockRuntimeClient(Protocol):
    """Minimal Bedrock Runtime operation required by the adapter."""

    def invoke_model(
        self,
        *,
        modelId: str,
        body: bytes,
        contentType: str,
        accept: str,
    ) -> Mapping[str, object]:
        """Invoke one embedding model request."""


def embed_canonical_text(
    client: BedrockRuntimeClient,
    *,
    canonical_text: str,
    user_id: str,
    content_digest: str,
) -> StoredSemanticEmbedding:
    """Invoke Titan V2 once and validate its generation-bound embedding."""

    request = build_embedding_request(canonical_text)
    validate_content_digest(content_digest)
    embedding_partition(user_id)
    encoded_request = json.dumps(
        request,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    try:
        response = client.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=encoded_request,
            contentType="application/json",
            accept="application/json",
        )
    except Exception:
        raise SemanticEmbeddingProviderError(
            "embedding provider invocation failed"
        ) from None

    payload = _decode_response(response)
    try:
        return validate_embedding_response(
            payload,
            user_id=user_id,
            content_digest=content_digest,
        )
    except SemanticEmbeddingContractError:
        raise SemanticEmbeddingProviderError(
            "embedding provider response failed validation"
        ) from None


def _decode_response(response: object) -> Mapping[str, object]:
    if not isinstance(response, Mapping):
        raise SemanticEmbeddingProviderError(
            "embedding provider returned an invalid response"
        )
    body = response.get("body")
    reader = getattr(body, "read", None)
    if not callable(reader):
        raise SemanticEmbeddingProviderError(
            "embedding provider response body is unreadable"
        )

    try:
        raw_body = reader()
    except Exception:
        raise SemanticEmbeddingProviderError(
            "embedding provider response body could not be read"
        ) from None

    if isinstance(raw_body, bytes):
        try:
            text = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            raise SemanticEmbeddingProviderError(
                "embedding provider response is not UTF-8"
            ) from None
    elif isinstance(raw_body, str):
        text = raw_body
    else:
        raise SemanticEmbeddingProviderError(
            "embedding provider response body has an invalid type"
        )

    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise SemanticEmbeddingProviderError(
            "embedding provider response is not valid JSON"
        ) from None
    if not isinstance(payload, Mapping):
        raise SemanticEmbeddingProviderError(
            "embedding provider response JSON must be an object"
        )
    return cast(Mapping[str, object], payload)
