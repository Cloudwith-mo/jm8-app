"""Bounded semantic evidence composition for Ask JM8."""

from __future__ import annotations

from ask_source_references import register_source
import math
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Mapping


SEMANTIC_ASK_CONTEXT_VERSION = "1.0"
MAX_SEMANTIC_EVIDENCE_ITEMS = 8
MAX_SEMANTIC_EXCERPT_CHARACTERS = 1_200
MAX_SEMANTIC_EVIDENCE_CHARACTERS = 8_000

_SOURCE_TYPES = {"typed", "image", "unknown"}


class AskSemanticContextError(ValueError):
    """Raised when retrieved evidence cannot be composed safely."""


def compose_ask_context_with_semantic_evidence(
    context: object,
    retrieval: object,
    *,
    source_references: dict | None = None,
) -> dict[str, Any]:
    """Attach a privacy-minimized, date-scoped semantic evidence section."""

    if not isinstance(context, Mapping) or not isinstance(retrieval, Mapping):
        raise AskSemanticContextError("semantic Ask context is invalid")

    status = retrieval.get("semanticRetrievalStatus")
    evidence = retrieval.get("evidence")
    evidence_count = retrieval.get("evidenceCount")
    retrieval_limit = retrieval.get("retrievalLimit")
    version = retrieval.get("semanticRetrievalVersion")

    if (
        status not in {"EMPTY", "READY"}
        or not isinstance(version, str)
        or not version.strip()
        or not isinstance(evidence, list)
        or isinstance(evidence_count, bool)
        or not isinstance(evidence_count, int)
        or evidence_count != len(evidence)
        or isinstance(retrieval_limit, bool)
        or not isinstance(retrieval_limit, int)
        or retrieval_limit < 1
        or len(evidence) > retrieval_limit
        or (status == "EMPTY") != (not evidence)
    ):
        raise AskSemanticContextError("semantic retrieval response is invalid")

    start_date, end_date = _scope_dates(context.get("scope"))
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    included_characters = 0
    scope_excluded = 0
    invalid_excluded = 0
    duplicate_excluded = 0
    limit_excluded = 0

    for raw_item in evidence:
        normalized = _normalize_evidence_item(raw_item)
        if normalized is None:
            invalid_excluded += 1
            continue

        evidence_date = date.fromisoformat(normalized["date"])
        if (
            (start_date is not None and evidence_date < start_date)
            or (end_date is not None and evidence_date > end_date)
        ):
            scope_excluded += 1
            continue

        identity = (
            normalized["date"],
            normalized["sourceType"],
            normalized["excerpt"].casefold(),
        )
        if identity in seen:
            duplicate_excluded += 1
            continue

        excerpt_characters = len(normalized["excerpt"])
        if (
            len(items) >= MAX_SEMANTIC_EVIDENCE_ITEMS
            or included_characters + excerpt_characters
            > MAX_SEMANTIC_EVIDENCE_CHARACTERS
        ):
            limit_excluded += 1
            continue

        seen.add(identity)
        normalized.update(register_source(source_references, raw_item.get("entryId")))
        items.append(normalized)
        included_characters += excerpt_characters

    result = deepcopy(dict(context))
    result["semanticEvidence"] = {
        "semanticContextVersion": SEMANTIC_ASK_CONTEXT_VERSION,
        "retrievalVersion": version,
        "status": "READY" if items else "EMPTY",
        "retrievedEvidence": evidence_count,
        "includedEvidence": len(items),
        "scopeExcludedEvidence": scope_excluded,
        "invalidExcludedEvidence": invalid_excluded,
        "duplicateExcludedEvidence": duplicate_excluded,
        "limitExcludedEvidence": limit_excluded,
        "contextTruncated": bool(
            invalid_excluded or duplicate_excluded or limit_excluded
        ),
        "items": items,
    }
    return result


def _scope_dates(value: object) -> tuple[date | None, date | None]:
    if not isinstance(value, Mapping):
        raise AskSemanticContextError("semantic Ask scope is invalid")

    start = _optional_date(value.get("startDate"))
    end = _optional_date(value.get("endDate"))
    if start is not None and end is not None and start > end:
        raise AskSemanticContextError("semantic Ask scope is invalid")
    return start, end


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or value != value.strip() or not value:
        raise AskSemanticContextError("semantic Ask scope is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AskSemanticContextError("semantic Ask scope is invalid") from None


def _normalize_evidence_item(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None

    excerpt = _clean_excerpt(value.get("text"))
    evidence_date = _entry_date(value.get("entryCreatedAt"))
    source_type = value.get("sourceType")
    score = value.get("score")

    if (
        not excerpt
        or evidence_date is None
        or not isinstance(source_type, str)
        or source_type.casefold() not in _SOURCE_TYPES
        or isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
    ):
        return None

    return {
        "date": evidence_date.isoformat(),
        "sourceType": source_type.casefold(),
        "distance": round(float(score), 8),
        "excerpt": excerpt,
    }


def _clean_excerpt(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:MAX_SEMANTIC_EXCERPT_CHARACTERS].strip()


def _entry_date(value: object) -> date | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value and value == value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).date()


__all__ = [
    "AskSemanticContextError",
    "MAX_SEMANTIC_EVIDENCE_CHARACTERS",
    "MAX_SEMANTIC_EVIDENCE_ITEMS",
    "MAX_SEMANTIC_EXCERPT_CHARACTERS",
    "SEMANTIC_ASK_CONTEXT_VERSION",
    "compose_ask_context_with_semantic_evidence",
]
