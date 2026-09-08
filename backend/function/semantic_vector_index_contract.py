"""Exact DynamoDB vector-index contract for JournalM8 semantic memory."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from semantic_embedding_contract import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_DISTANCE_FUNCTION,
    EMBEDDING_INDEX_NAME,
    EMBEDDING_PARTITION_ATTRIBUTE,
    EMBEDDING_VECTOR_ATTRIBUTE,
)


class SemanticVectorIndexContractError(ValueError):
    """Raised when a vector-index description violates the exact contract."""


class VectorIndexCreateDefinition(TypedDict):
    IndexName: str
    VectorAttribute: dict[str, str]
    SearchSchema: list[dict[str, str]]
    Projection: dict[str, str]
    Dimensions: int
    DistanceFunction: str


class VectorIndexState(TypedDict):
    action: Literal["CREATE", "WAIT", "READY"]
    indexArn: str | None


def vector_index_create_definition() -> VectorIndexCreateDefinition:
    """Return the exact create payload accepted by DynamoDB UpdateTable."""

    return {
        "IndexName": EMBEDDING_INDEX_NAME,
        "VectorAttribute": {"AttributeName": EMBEDDING_VECTOR_ATTRIBUTE},
        "SearchSchema": [
            {
                "AttributeName": EMBEDDING_PARTITION_ATTRIBUTE,
                "SearchSchemaElementType": "HASH",
            }
        ],
        "Projection": {"ProjectionType": "KEYS_ONLY"},
        "Dimensions": EMBEDDING_DIMENSIONS,
        "DistanceFunction": EMBEDDING_DISTANCE_FUNCTION,
    }


def validate_vector_index_state(
    document: Mapping[str, Any],
    *,
    table_arn: str,
) -> VectorIndexState:
    """Classify an absent, creating, or ready exact vector index.

    Any extra or incompatible vector index fails closed. This contract never
    authorizes deletion or replacement of an existing index.
    """

    table = document.get("Table") if isinstance(document, Mapping) else None
    if not isinstance(table, Mapping):
        raise SemanticVectorIndexContractError(
            "semantic vector index description is malformed"
        )
    if table.get("TableArn") != table_arn:
        raise SemanticVectorIndexContractError(
            "semantic vector index table identity is invalid"
        )

    indexes = table.get("VectorIndexes", [])
    if indexes is None:
        indexes = []
    if not isinstance(indexes, list):
        raise SemanticVectorIndexContractError(
            "semantic vector index description is malformed"
        )
    if not indexes:
        if table.get("TableStatus") != "ACTIVE":
            raise SemanticVectorIndexContractError(
                "semantic vector index cannot be created while the table is not active"
            )
        return {"action": "CREATE", "indexArn": None}
    if len(indexes) != 1 or not isinstance(indexes[0], Mapping):
        raise SemanticVectorIndexContractError(
            "unexpected vector indexes exist on the semantic-memory table"
        )

    index = indexes[0]
    expected = vector_index_create_definition()
    comparable = {
        key: index.get(key)
        for key in expected
    }
    if comparable != expected:
        raise SemanticVectorIndexContractError(
            "semantic vector index does not match the required contract"
        )

    expected_arn = f"{table_arn}/index/{EMBEDDING_INDEX_NAME}"
    index_arn = index.get("IndexArn")
    if index_arn is not None and index_arn != expected_arn:
        raise SemanticVectorIndexContractError(
            "semantic vector index identity is invalid"
        )

    status = index.get("IndexStatus")
    backfilling = index.get("Backfilling")
    if (
        status == "ACTIVE"
        and backfilling in {False, None}
        and index_arn == expected_arn
    ):
        if table.get("TableStatus") != "ACTIVE":
            raise SemanticVectorIndexContractError(
                "semantic vector index table is not active"
            )
        return {"action": "READY", "indexArn": expected_arn}
    if status in {"CREATING", "UPDATING"} and backfilling in {True, False, None}:
        if table.get("TableStatus") not in {"ACTIVE", "UPDATING"}:
            raise SemanticVectorIndexContractError(
                "semantic vector index table state is invalid"
            )
        return {"action": "WAIT", "indexArn": expected_arn}
    raise SemanticVectorIndexContractError(
        "semantic vector index is not in a recoverable state"
    )


__all__ = [
    "SemanticVectorIndexContractError",
    "VectorIndexCreateDefinition",
    "VectorIndexState",
    "validate_vector_index_state",
    "vector_index_create_definition",
]
