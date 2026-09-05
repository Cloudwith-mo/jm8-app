"""Canonical journal-entry adapter for deterministic semantic memory chunks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NotRequired, TypeGuard, TypedDict

from semantic_chunking import (
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_OVERLAP_SIZE,
    SemanticChunk,
    chunk_text,
)


class CanonicalEntryText(TypedDict):
    """Eligible source text and identity selected from a journal entry."""

    entryId: str
    userId: str
    text: str
    textField: str
    sourceType: str


class EntrySemanticChunk(SemanticChunk):
    """Semantic chunk augmented with inert source-entry metadata."""

    userId: str
    sourceType: str
    canonicalTextField: str
    entryCreatedAt: NotRequired[object]
    entryUpdatedAt: NotRequired[object]


class SemanticMemoryIdentityError(ValueError):
    """Raised when an entry is missing a valid persistence identity."""


def canonical_entry_text(
    entry: Mapping[str, object],
) -> CanonicalEntryText | None:
    """Select the user-approved canonical text for an entry, if eligible.

    Identity errors are malformed input and therefore raise.  An entry with a
    valid identity but no approved source text is valid input and returns
    ``None``.
    """

    entry_id, user_id = _validated_identity(entry)
    source_type = _normalized_label(entry.get("sourceType"), lowercase=True)
    status = _normalized_label(entry.get("status"))
    review_status = _normalized_label(entry.get("reviewStatus"))
    reviewed_at = entry.get("reviewedAt")
    has_completed_review = (
        status == "REVIEWED"
        or review_status == "COMPLETED"
        or _non_empty_string(reviewed_at)
    )

    clean_text = entry.get("cleanText")
    if has_completed_review and _non_empty_string(clean_text):
        return {
            "entryId": entry_id,
            "userId": user_id,
            "text": clean_text,
            "textField": "cleanText",
            "sourceType": source_type,
        }

    raw_text = entry.get("rawText")
    if (
        source_type == "typed"
        and status == "REVIEWED"
        and _non_empty_string(raw_text)
    ):
        return {
            "entryId": entry_id,
            "userId": user_id,
            "text": raw_text,
            "textField": "rawText",
            "sourceType": source_type,
        }

    return None


def build_entry_semantic_chunks(
    entry: Mapping[str, object],
    *,
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
    overlap_size: int = DEFAULT_OVERLAP_SIZE,
) -> list[EntrySemanticChunk]:
    """Build flat persistence records for an eligible canonical entry."""

    canonical = canonical_entry_text(entry)
    if canonical is None:
        return []

    chunks = chunk_text(
        canonical["entryId"],
        canonical["text"],
        max_chunk_size=max_chunk_size,
        overlap_size=overlap_size,
    )
    has_created_at = "createdAt" in entry
    has_updated_at = "updatedAt" in entry
    results: list[EntrySemanticChunk] = []

    for chunk in chunks:
        record: EntrySemanticChunk = {
            **chunk,
            "userId": canonical["userId"],
            "sourceType": canonical["sourceType"],
            "canonicalTextField": canonical["textField"],
        }
        if has_created_at:
            record["entryCreatedAt"] = entry["createdAt"]
        if has_updated_at:
            record["entryUpdatedAt"] = entry["updatedAt"]
        results.append(record)
    return results


def _validated_identity(entry: Mapping[str, object]) -> tuple[str, str]:
    if not isinstance(entry, Mapping):
        raise SemanticMemoryIdentityError("entry must be a mapping")

    entry_id = entry.get("entryId")
    if not _non_empty_string(entry_id):
        raise SemanticMemoryIdentityError("entryId must be a non-empty string")

    user_id = entry.get("userId")
    if not _non_empty_string(user_id):
        raise SemanticMemoryIdentityError("userId must be a non-empty string")

    return entry_id, user_id


def _normalized_label(value: object, *, lowercase: bool = False) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    return normalized.casefold() if lowercase else normalized.upper()


def _non_empty_string(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "CanonicalEntryText",
    "EntrySemanticChunk",
    "SemanticMemoryIdentityError",
    "build_entry_semantic_chunks",
    "canonical_entry_text",
]
