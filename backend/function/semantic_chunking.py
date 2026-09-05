"""Deterministic, dependency-free semantic chunking for journal text.

The chunker deliberately works in characters rather than model tokens.  That
keeps its output stable across runtimes and avoids coupling stored memory data
to a tokenizer or an AWS service.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import TypedDict


CHUNKING_VERSION = "jm8-semantic-chunk-v1"
DEFAULT_MAX_CHUNK_SIZE = 1_200
DEFAULT_OVERLAP_SIZE = 200
MAX_INPUT_CHARACTERS = 1_000_000

# Explicit aliases make the unit of each limit unambiguous to callers.
DEFAULT_MAX_CHUNK_CHARACTERS = DEFAULT_MAX_CHUNK_SIZE
DEFAULT_OVERLAP_CHARACTERS = DEFAULT_OVERLAP_SIZE

_BLANK_LINES = re.compile(r"\n[\t\v\f \u00a0]*\n+")
_WHITESPACE = re.compile(r"\s+")
_PARAGRAPH_BOUNDARY = re.compile(r"\n\n")
_SENTENCE_BOUNDARY = re.compile(
    r"[.!?\u3002\uff01\uff1f](?:[\"'\u2019\u201d)\]]*)\s+"
)
_WORD_BOUNDARY = re.compile(r"\s+")


class SemanticChunk(TypedDict):
    """Flat persistence-ready chunk returned by :func:`chunk_text`."""

    entryId: str
    chunkId: str
    chunkOrdinal: int
    text: str
    characterCount: int
    wordCount: int
    contentDigest: str
    chunkDigest: str
    chunkingVersion: str
    chunkCount: int


class SemanticChunkingInputError(ValueError):
    """Raised when text or chunking limits violate the public contract."""


def normalize_text(text: str) -> str:
    """Return the canonical NFC and whitespace representation of ``text``.

    A blank line is a semantic paragraph boundary.  Runs of blank lines are
    canonicalized to two newlines; all other whitespace runs become one ASCII
    space.  Leading and trailing whitespace is removed.
    """

    _validate_input(text)
    if not text:
        return ""

    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _BLANK_LINES.sub("\n\n", normalized)

    paragraphs = []
    for paragraph in normalized.split("\n\n"):
        compact = _WHITESPACE.sub(" ", paragraph).strip()
        if compact:
            paragraphs.append(compact)
    return "\n\n".join(paragraphs)


def sha256_digest(text: str) -> str:
    """Return the lowercase SHA-256 hex digest of UTF-8 ``text``."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_text(
    entry_id: str,
    text: str,
    *,
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
    overlap_size: int = DEFAULT_OVERLAP_SIZE,
) -> list[SemanticChunk]:
    """Normalize and split text into deterministic, content-addressed chunks.

    Boundaries are selected in semantic order: paragraph, sentence, word, and
    finally a hard character boundary for an individual oversized word.  Each
    chunk after the first starts with at most ``overlap_size`` characters from
    the preceding source range.
    """

    _validate_entry_id(entry_id)
    _validate_input(text)
    _validate_limits(max_chunk_size, overlap_size)
    normalized = normalize_text(text)
    if not normalized:
        return []

    chunk_texts = _split_text(normalized, max_chunk_size, overlap_size)
    content_digest = sha256_digest(normalized)
    chunk_count = len(chunk_texts)
    chunks: list[SemanticChunk] = []

    for ordinal, value in enumerate(chunk_texts):
        chunk_digest = sha256_digest(value)
        chunks.append(
            {
                "entryId": entry_id,
                "chunkId": _chunk_id(
                    entry_id=entry_id,
                    content_digest=content_digest,
                    chunk_digest=chunk_digest,
                    chunk_ordinal=ordinal,
                ),
                "chunkOrdinal": ordinal,
                "text": value,
                "characterCount": len(value),
                "wordCount": len(value.split()),
                "contentDigest": content_digest,
                "chunkDigest": chunk_digest,
                "chunkingVersion": CHUNKING_VERSION,
                "chunkCount": chunk_count,
            }
        )
    return chunks


def semantic_chunk_text(
    entry_id: str,
    text: str,
    *,
    max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
    overlap_size: int = DEFAULT_OVERLAP_SIZE,
) -> list[SemanticChunk]:
    """Named alias for callers that prefer an explicit semantic operation."""

    return chunk_text(
        entry_id,
        text,
        max_chunk_size=max_chunk_size,
        overlap_size=overlap_size,
    )


def _validate_entry_id(entry_id: object) -> None:
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise SemanticChunkingInputError("entry_id must be a non-empty string")


def _validate_input(text: object) -> None:
    if not isinstance(text, str):
        raise SemanticChunkingInputError("text must be a string")
    if len(text) > MAX_INPUT_CHARACTERS:
        raise SemanticChunkingInputError(
            f"text exceeds the {MAX_INPUT_CHARACTERS} character limit"
        )


def _validate_limits(max_chunk_size: object, overlap_size: object) -> None:
    if (
        not isinstance(max_chunk_size, int)
        or isinstance(max_chunk_size, bool)
        or max_chunk_size < 1
    ):
        raise SemanticChunkingInputError("max_chunk_size must be a positive integer")
    if (
        not isinstance(overlap_size, int)
        or isinstance(overlap_size, bool)
        or overlap_size < 0
    ):
        raise SemanticChunkingInputError("overlap_size must be a non-negative integer")
    if overlap_size >= max_chunk_size:
        raise SemanticChunkingInputError(
            "overlap_size must be smaller than max_chunk_size"
        )


def _split_text(text: str, max_size: int, overlap_size: int) -> list[str]:
    ranges: list[tuple[int, int]] = []
    start = 0
    covered_end = 0
    text_length = len(text)

    while covered_end < text_length:
        limit = min(start + max_size, text_length)
        if limit == text_length:
            end = text_length
        else:
            end = _preferred_end(
                text,
                start=start,
                limit=limit,
                minimum_end=covered_end + 1,
            )

        # The selected range always advances beyond text already covered.
        if end <= covered_end:
            end = limit
        ranges.append((start, end))
        covered_end = end
        if covered_end == text_length:
            break
        start = _overlap_start(text, end, overlap_size)

    # Stripping is safe because normalization makes all edge characters here
    # whitespace separators, never meaningful source text.
    return [text[start:end].strip() for start, end in ranges]


def _preferred_end(
    text: str,
    *,
    start: int,
    limit: int,
    minimum_end: int,
) -> int:
    window = text[start:limit]
    minimum_offset = max(1, minimum_end - start)

    for pattern in (
        _PARAGRAPH_BOUNDARY,
        _SENTENCE_BOUNDARY,
        _WORD_BOUNDARY,
    ):
        candidates = [
            match.end()
            for match in pattern.finditer(window)
            if match.end() >= minimum_offset
        ]
        if candidates:
            return start + candidates[-1]
    return limit


def _overlap_start(text: str, end: int, overlap_size: int) -> int:
    if overlap_size == 0:
        return end

    desired = max(0, end - overlap_size)
    if desired == 0:
        return 0

    # Move forward to the first clean word boundary.  Moving forward, never
    # backward, guarantees the actual overlap cannot exceed the configured
    # maximum.  A long word has no safe boundary and is overlapped exactly.
    boundary = _WORD_BOUNDARY.search(text, desired, end)
    if boundary is None or boundary.end() >= end:
        return desired
    return boundary.end()


def _chunk_id(
    *,
    entry_id: str,
    content_digest: str,
    chunk_digest: str,
    chunk_ordinal: int,
) -> str:
    identity = "\0".join(
        (
            CHUNKING_VERSION,
            entry_id,
            content_digest,
            str(chunk_ordinal),
            chunk_digest,
        )
    )
    return f"chunk_{sha256_digest(identity)}"


__all__ = [
    "CHUNKING_VERSION",
    "DEFAULT_MAX_CHUNK_CHARACTERS",
    "DEFAULT_MAX_CHUNK_SIZE",
    "DEFAULT_OVERLAP_CHARACTERS",
    "DEFAULT_OVERLAP_SIZE",
    "MAX_INPUT_CHARACTERS",
    "SemanticChunk",
    "SemanticChunkingInputError",
    "chunk_text",
    "normalize_text",
    "semantic_chunk_text",
    "sha256_digest",
]
