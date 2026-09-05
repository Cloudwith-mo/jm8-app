"""Durable, privacy-safe deletion guards for semantic memory."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import TypedDict

from botocore.exceptions import BotoCoreError, ClientError


GUARD_ENTITY_TYPE = "SEMANTIC_MEMORY_DELETION_GUARD"
GUARD_STATUS = "DELETED"
GUARD_VERSION = "jm8-semantic-deletion-guard-v1"
GUARD_SK = "SEMANTIC_MEMORY_GUARD"

_AWS_ERRORS = (ClientError, BotoCoreError)


class SemanticMemoryDeletionGuardError(RuntimeError):
    """A privacy-safe deletion-guard persistence failure."""


class SemanticMemoryDeletionGuardIntegrityError(SemanticMemoryDeletionGuardError):
    """A persisted deletion guard does not match the canonical contract."""


class SemanticMemoryDeletionGuard(TypedDict):
    PK: str
    SK: str
    entityType: str
    subjectDigest: str
    status: str
    guardVersion: str


def subject_digest(subject: str) -> str:
    """Return the full SHA-256 digest for a validated Cognito subject."""

    _validate_subject(subject)
    return sha256(subject.encode("utf-8")).hexdigest()


def build_semantic_deletion_guard(subject: str) -> SemanticMemoryDeletionGuard:
    """Build the canonical guard without retaining the raw subject."""

    digest = subject_digest(subject)
    return {
        "PK": f"DELETED_SUBJECT#{digest}",
        "SK": GUARD_SK,
        "entityType": GUARD_ENTITY_TYPE,
        "subjectDigest": digest,
        "status": GUARD_STATUS,
        "guardVersion": GUARD_VERSION,
    }


def put_semantic_deletion_guard(
    table: object,
    subject: str,
) -> SemanticMemoryDeletionGuard:
    """Idempotently persist and verify the canonical guard."""

    guard = build_semantic_deletion_guard(subject)
    try:
        table.put_item(Item=dict(guard))  # type: ignore[attr-defined]
    except _AWS_ERRORS:
        raise SemanticMemoryDeletionGuardError(
            "semantic memory deletion guard operation failed"
        ) from None
    if not semantic_deletion_guard_exists(table, subject):
        raise SemanticMemoryDeletionGuardIntegrityError(
            "semantic memory deletion guard verification failed"
        )
    return guard


def semantic_deletion_guard_exists(table: object, subject: str) -> bool:
    """Consistently read and validate the exact guard for a subject."""

    expected = build_semantic_deletion_guard(subject)
    try:
        response = table.get_item(  # type: ignore[attr-defined]
            Key={"PK": expected["PK"], "SK": expected["SK"]},
            ConsistentRead=True,
        )
    except _AWS_ERRORS:
        raise SemanticMemoryDeletionGuardError(
            "semantic memory deletion guard operation failed"
        ) from None
    if not isinstance(response, Mapping):
        raise SemanticMemoryDeletionGuardError(
            "semantic memory deletion guard response failed"
        )
    item = response.get("Item")
    if item is None:
        return False
    if not isinstance(item, Mapping) or dict(item) != expected:
        raise SemanticMemoryDeletionGuardIntegrityError(
            "semantic memory deletion guard verification failed"
        )
    return True


def _validate_subject(subject: object) -> None:
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("subject must be a non-empty string")


__all__ = [
    "GUARD_ENTITY_TYPE",
    "GUARD_SK",
    "GUARD_STATUS",
    "GUARD_VERSION",
    "SemanticMemoryDeletionGuard",
    "SemanticMemoryDeletionGuardError",
    "SemanticMemoryDeletionGuardIntegrityError",
    "build_semantic_deletion_guard",
    "put_semantic_deletion_guard",
    "semantic_deletion_guard_exists",
    "subject_digest",
]
