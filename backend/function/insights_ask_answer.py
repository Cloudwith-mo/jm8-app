import json
import math
import os
from datetime import (
    datetime,
    timezone,
)
from typing import Any

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
)

from llm_journal_analyzer import (
    RETRYABLE_BEDROCK_ERROR_CODES,
    clean_string_list,
    clean_text_value,
    create_bedrock_client,
    get_model_id,
    percentage_value,
)


ASK_ANSWER_VERSION = "1.0"
ASK_PROMPT_VERSION = "ask-jm8-v2"

MAX_MODEL_CONTEXT_CHARACTERS = 70_000
MAX_MODEL_SOURCE_SIGNALS = 10
MAX_MODEL_TIMELINE_PERIODS = 12
MAX_MODEL_AGGREGATE_ITEMS = 6
MAX_MODEL_SEMANTIC_EVIDENCE_ITEMS = 8
MAX_MODEL_SEMANTIC_EXCERPT_CHARACTERS = 1_200
MAX_MODEL_SEMANTIC_EVIDENCE_CHARACTERS = 8_000

MAX_EVIDENCE_ITEMS = 6
MAX_TAKEAWAYS = 6
MAX_RELATED_THEMES = 10
MAX_GROWTH_SIGNALS = 6
MAX_LIMITATIONS = 6
MAX_FOLLOW_UPS = 4


PRIMARY_SIGNAL_CATEGORIES = [
    "moods",
    "sentiments",
    "themes",
    "emergingTopics",
    "keyInsights",
    "challenges",
    "goals",
    "growthSignals",
    "behaviorPatterns",
    "peopleAndTopics",
    "nextSteps",
    "none",
]


FORBIDDEN_CONTEXT_KEYS = {
    "entryId",
    "userId",
    "rawText",
    "cleanText",
    "jobId",
    "s3RawKey",
    "s3RawBucket",
    "imagePreviewUrl",
    "executionArn",
    "executionName",
    "PK",
    "SK",
    "GSI1PK",
    "GSI1SK",
}


ASK_ANSWER_OUTPUT_SCHEMA: dict[
    str,
    Any,
] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "ANSWERED",
                "INSUFFICIENT_CONTEXT",
            ],
        },
        "headline": {
            "type": "string",
        },
        "summary": {
            "type": "string",
        },
        "explanation": {
            "type": "string",
        },
        "primarySignal": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": (
                        PRIMARY_SIGNAL_CATEGORIES
                    ),
                },
                "value": {
                    "type": "string",
                },
            },
            "required": [
                "category",
                "value",
            ],
            "additionalProperties": False,
        },
        "strongestPeriod": {
            "type": "string",
        },
        "improvementPercent": {
            "type": "integer",
        },
        "topTrigger": {
            "type": "string",
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "paraphrase": {
                        "type": "string",
                    },
                    "date": {
                        "type": "string",
                    },
                    "sourceType": {
                        "type": "string",
                        "enum": [
                            "typed",
                            "image",
                            "unknown",
                        ],
                    },
                    "relevance": {
                        "type": "string",
                        "enum": [
                            "high",
                            "medium",
                            "low",
                        ],
                    },
                },
                "required": [
                    "paraphrase",
                    "date",
                    "sourceType",
                    "relevance",
                ],
                "additionalProperties": (
                    False
                ),
            },
        },
        "takeaways": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "relatedThemes": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "growthSignals": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "limitations": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "suggestedFollowUps": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },
    "required": [
        "status",
        "headline",
        "summary",
        "explanation",
        "primarySignal",
        "strongestPeriod",
        "improvementPercent",
        "topTrigger",
        "evidence",
        "takeaways",
        "relatedThemes",
        "growthSignals",
        "limitations",
        "suggestedFollowUps",
    ],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """
You are JM8, a private journal-history intelligence engine.

Answer the user's question using only the supplied private journal context.
The context may contain derived analysis signals and a bounded set of raw
journal excerpts selected through tenant-isolated semantic retrieval.

Grounding rules:

1. Do not invent events, dates, people, motives, goals, quotations, or
   journal entries.
2. Do not claim certainty when the context is partial, ambiguous, or
   insufficient.
3. Do not diagnose mental-health conditions or make clinical judgments.
4. Do not reproduce or directly quote journal excerpts. Evidence must be a
   concise paraphrase of a supplied source signal or semantic excerpt.
5. Evidence dates and source types must exactly match a supplied source
   signal or semantic excerpt.
6. A related theme must already appear in the supplied aggregate themes
   or emerging topics.
7. A growth signal must already appear in the supplied aggregate growth
   signals.
8. The primary signal must match an aggregate signal whenever possible.
9. strongestPeriod must be an exact period supplied in the timeline or
   an empty string.
10. improvementPercent must be zero when the context does not support a
    meaningful comparison over time.
11. Explain limitations clearly when coverage is incomplete or context
    is truncated.
12. Keep the response concise, respectful, useful, and action-oriented.
13. suggestedFollowUps must be questions the user could ask JM8 next.
14. Return INSUFFICIENT_CONTEXT when the available evidence cannot
    responsibly answer the question.
15. Distinguish what the journal evidence supports from your inference. Do
    not treat vector similarity as proof that a claim is true.
""".strip()


class AskAnswerError(
    RuntimeError
):
    """Base exception for Ask JM8 answer failures."""


class AskAnswerInputError(
    AskAnswerError
):
    """Raised when grounded context is invalid."""

    def __init__(
        self,
        code: str,
        message: str,
    ):
        super().__init__(message)

        self.code = code
        self.message = message


class AskAnswerInvocationError(
    AskAnswerError
):
    """Raised when Bedrock cannot answer the question."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "Unknown",
        retryable: bool = False,
        retry_attempts: int = 0,
    ):
        super().__init__(message)

        self.error_code = str(
            error_code or "Unknown"
        )

        self.retryable = bool(
            retryable
        )

        self.retry_attempts = max(
            int(
                retry_attempts or 0
            ),
            0,
        )


class AskAnswerResponseError(
    AskAnswerError
):
    """Raised when Bedrock returns an unsafe response."""


def utc_now(
    now: datetime | None = None,
) -> str:
    if now is None:
        resolved = datetime.now(
            timezone.utc
        )
    elif now.tzinfo is None:
        resolved = now.replace(
            tzinfo=timezone.utc
        )
    else:
        resolved = now.astimezone(
            timezone.utc
        )

    return resolved.isoformat()


def get_answer_version() -> str:
    return (
        os.environ.get(
            "ASK_ANSWER_VERSION"
        )
        or ASK_ANSWER_VERSION
    ).strip()


def get_prompt_version() -> str:
    return (
        os.environ.get(
            "ASK_PROMPT_VERSION"
        )
        or ASK_PROMPT_VERSION
    ).strip()


def integer_value(
    value: Any,
) -> int:
    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return 0


def find_forbidden_context_key(
    value: Any,
) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(
                key
            )

            if (
                normalized_key
                in FORBIDDEN_CONTEXT_KEYS
            ):
                return normalized_key

            found = (
                find_forbidden_context_key(
                    child
                )
            )

            if found:
                return found

    elif isinstance(value, list):
        for item in value:
            found = (
                find_forbidden_context_key(
                    item
                )
            )

            if found:
                return found

    return None


def clean_ranked_items(
    value: Any,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    items: list[
        dict[str, Any]
    ] = []

    seen: set[str] = set()

    for raw_item in value:
        if not isinstance(
            raw_item,
            dict,
        ):
            continue

        label = clean_text_value(
            raw_item.get("value"),
            max_characters=180,
        )

        if not label:
            continue

        identity = label.casefold()

        if identity in seen:
            continue

        seen.add(identity)

        items.append({
            "value": label,
            "count": max(
                integer_value(
                    raw_item.get("count")
                ),
                0,
            ),
            "sharePercent": (
                percentage_value(
                    raw_item.get(
                        "sharePercent"
                    )
                )
            ),
        })

        if len(items) >= limit:
            break

    return items


def clean_signal_values(
    value: Any,
    *,
    max_items: int = 3,
) -> list[str]:
    return clean_string_list(
        value,
        max_items=max_items,
        max_characters=120,
    )


def compact_source_signal(
    signal: dict[str, Any],
) -> dict[str, Any]:
    source_type = clean_text_value(
        signal.get("sourceType"),
        max_characters=20,
    ).casefold()

    if source_type not in {
        "typed",
        "image",
        "unknown",
    }:
        source_type = "unknown"

    return {
        "date": clean_text_value(
            signal.get("date"),
            max_characters=10,
        ),
        "sourceType": source_type,
        "mood": clean_text_value(
            signal.get("mood"),
            max_characters=50,
        ),
        "sentiment": clean_text_value(
            signal.get("sentiment"),
            max_characters=50,
        ),
        "secondaryMoods": (
            clean_signal_values(
                signal.get(
                    "secondaryMoods"
                )
            )
        ),
        "themes": clean_signal_values(
            signal.get("themes")
        ),
        "emergingTopics": (
            clean_signal_values(
                signal.get(
                    "emergingTopics"
                )
            )
        ),
        "keyInsights": (
            clean_signal_values(
                signal.get(
                    "keyInsights"
                )
            )
        ),
        "challenges": (
            clean_signal_values(
                signal.get(
                    "challenges"
                )
            )
        ),
        "goals": clean_signal_values(
            signal.get("goals")
        ),
        "growthSignals": (
            clean_signal_values(
                signal.get(
                    "growthSignals"
                )
            )
        ),
        "behaviorPatterns": (
            clean_signal_values(
                signal.get(
                    "behaviorPatterns"
                )
            )
        ),
        "peopleAndTopics": (
            clean_signal_values(
                signal.get(
                    "peopleAndTopics"
                )
            )
        ),
        "nextStep": clean_text_value(
            signal.get("nextStep"),
            max_characters=180,
        ),
        "relevanceScore": max(
            integer_value(
                signal.get(
                    "relevanceScore"
                )
            ),
            0,
        ),
    }


def compact_timeline_item(
    item: dict[str, Any],
) -> dict[str, Any]:
    dominant_mood = item.get(
        "dominantMood"
    )

    dominant_sentiment = item.get(
        "dominantSentiment"
    )

    return {
        "period": clean_text_value(
            item.get("period"),
            max_characters=20,
        ),
        "analyzedEntries": max(
            integer_value(
                item.get(
                    "analyzedEntries"
                )
            ),
            0,
        ),
        "dominantMood": (
            clean_ranked_items(
                (
                    [dominant_mood]
                    if isinstance(
                        dominant_mood,
                        dict,
                    )
                    else []
                ),
                limit=1,
            )
        ),
        "dominantSentiment": (
            clean_ranked_items(
                (
                    [dominant_sentiment]
                    if isinstance(
                        dominant_sentiment,
                        dict,
                    )
                    else []
                ),
                limit=1,
            )
        ),
        "topThemes": clean_ranked_items(
            item.get("topThemes"),
            limit=3,
        ),
        "topChallenges": (
            clean_ranked_items(
                item.get(
                    "topChallenges"
                ),
                limit=3,
            )
        ),
        "topGoals": clean_ranked_items(
            item.get("topGoals"),
            limit=3,
        ),
        "growthSignals": (
            clean_ranked_items(
                item.get(
                    "growthSignals"
                ),
                limit=3,
            )
        ),
    }


def normalize_scope(
    value: Any,
) -> dict[str, Any]:
    scope = (
        value
        if isinstance(value, dict)
        else {}
    )

    return {
        "startDate": (
            clean_text_value(
                scope.get("startDate"),
                max_characters=40,
            )
            or None
        ),
        "endDate": (
            clean_text_value(
                scope.get("endDate"),
                max_characters=40,
            )
            or None
        ),
        "firstEntryAt": (
            clean_text_value(
                scope.get(
                    "firstEntryAt"
                ),
                max_characters=80,
            )
            or None
        ),
        "latestEntryAt": (
            clean_text_value(
                scope.get(
                    "latestEntryAt"
                ),
                max_characters=80,
            )
            or None
        ),
    }


def normalize_coverage(
    value: Any,
) -> dict[str, Any]:
    coverage = (
        value
        if isinstance(value, dict)
        else {}
    )

    total_entries = max(
        integer_value(
            coverage.get(
                "totalEntries"
            )
        ),
        0,
    )

    analyzed_entries = max(
        integer_value(
            coverage.get(
                "analyzedEntries"
            )
        ),
        0,
    )

    unanalyzed_entries = max(
        integer_value(
            coverage.get(
                "unanalyzedEntries"
            )
        ),
        0,
    )

    source_signals_available = max(
        integer_value(
            coverage.get(
                "sourceSignalsAvailable"
            )
        ),
        0,
    )

    source_signals_included = max(
        integer_value(
            coverage.get(
                "sourceSignalsIncluded"
            )
        ),
        0,
    )

    return {
        "totalEntries": total_entries,
        "analyzedEntries": (
            analyzed_entries
        ),
        "unanalyzedEntries": (
            unanalyzed_entries
        ),
        "analysisCompletionPercent": (
            percentage_value(
                coverage.get(
                    "analysisCompletionPercent"
                )
            )
        ),
        "sourceSignalsAvailable": (
            source_signals_available
        ),
        "sourceSignalsIncluded": (
            source_signals_included
        ),
        "contextTruncated": bool(
            coverage.get(
                "contextTruncated"
            )
        ),
    }


def normalize_semantic_evidence(
    value: Any,
) -> dict[str, Any]:
    semantic = (
        value
        if isinstance(value, dict)
        else {}
    )

    status = clean_text_value(
        semantic.get("status"),
        max_characters=20,
    ).upper()

    if status not in {"EMPTY", "READY"}:
        status = "EMPTY"

    raw_items = semantic.get("items")
    items: list[dict[str, Any]] = []
    included_characters = 0

    for item in (
        raw_items
        if isinstance(raw_items, list)
        else []
    ):
        if not isinstance(item, dict):
            continue

        evidence_date = clean_text_value(
            item.get("date"),
            max_characters=10,
        )
        source_type = clean_text_value(
            item.get("sourceType"),
            max_characters=20,
        ).casefold()
        excerpt = clean_text_value(
            item.get("excerpt"),
            max_characters=(
                MAX_MODEL_SEMANTIC_EXCERPT_CHARACTERS
            ),
        )
        distance = item.get("distance")

        if (
            not evidence_date
            or source_type not in {"typed", "image", "unknown"}
            or not excerpt
            or isinstance(distance, bool)
            or not isinstance(distance, (int, float))
            or not math.isfinite(float(distance))
        ):
            continue

        if (
            included_characters + len(excerpt)
            > MAX_MODEL_SEMANTIC_EVIDENCE_CHARACTERS
        ):
            continue

        items.append({
            "date": evidence_date,
            "sourceType": source_type,
            "distance": round(float(distance), 8),
            "excerpt": excerpt,
        })
        included_characters += len(excerpt)

        if len(items) >= MAX_MODEL_SEMANTIC_EVIDENCE_ITEMS:
            break

    if not items:
        status = "EMPTY"

    return {
        "semanticContextVersion": clean_text_value(
            semantic.get("semanticContextVersion"),
            max_characters=30,
        ),
        "retrievalVersion": clean_text_value(
            semantic.get("retrievalVersion"),
            max_characters=30,
        ),
        "status": status,
        "retrievedEvidence": max(
            integer_value(semantic.get("retrievedEvidence")),
            0,
        ),
        "includedEvidence": len(items),
        "scopeExcludedEvidence": max(
            integer_value(semantic.get("scopeExcludedEvidence")),
            0,
        ),
        "invalidExcludedEvidence": max(
            integer_value(semantic.get("invalidExcludedEvidence")),
            0,
        ),
        "duplicateExcludedEvidence": max(
            integer_value(semantic.get("duplicateExcludedEvidence")),
            0,
        ),
        "limitExcludedEvidence": max(
            integer_value(semantic.get("limitExcludedEvidence")),
            0,
        ),
        "contextTruncated": bool(
            semantic.get("contextTruncated")
        ),
        "items": items,
    }


def prepare_model_context(
    context: Any,
) -> dict[str, Any]:
    if not isinstance(
        context,
        dict,
    ):
        raise AskAnswerInputError(
            "InvalidContext",
            (
                "Ask JM8 context must "
                "be an object."
            ),
        )

    forbidden_key = (
        find_forbidden_context_key(
            context
        )
    )

    if forbidden_key:
        raise AskAnswerInputError(
            "UnsafeContext",
            (
                "Ask JM8 context contains "
                "a private field."
            ),
        )

    question = clean_text_value(
        context.get("question"),
        max_characters=500,
    )

    if len(question) < 3:
        raise AskAnswerInputError(
            "InvalidContext",
            (
                "Ask JM8 context must "
                "contain a question."
            ),
        )

    aggregate_signals = (
        context.get(
            "aggregateSignals"
        )
    )

    if not isinstance(
        aggregate_signals,
        dict,
    ):
        aggregate_signals = {}

    compact_aggregates = {
        category: clean_ranked_items(
            aggregate_signals.get(
                category
            ),
            limit=(
                MAX_MODEL_AGGREGATE_ITEMS
            ),
        )
        for category
        in PRIMARY_SIGNAL_CATEGORIES
        if category != "none"
    }

    raw_timeline = context.get(
        "timeline"
    )

    timeline = [
        compact_timeline_item(item)
        for item in (
            raw_timeline
            if isinstance(
                raw_timeline,
                list,
            )
            else []
        )
        if isinstance(item, dict)
    ][
        -MAX_MODEL_TIMELINE_PERIODS:
    ]

    raw_source_signals = context.get(
        "sourceSignals"
    )

    source_signals = [
        compact_source_signal(item)
        for item in (
            raw_source_signals
            if isinstance(
                raw_source_signals,
                list,
            )
            else []
        )
        if isinstance(item, dict)
    ][
        :MAX_MODEL_SOURCE_SIGNALS
    ]

    prepared = {
        "contextVersion": (
            clean_text_value(
                context.get(
                    "contextVersion"
                ),
                max_characters=30,
            )
        ),
        "contextStatus": (
            clean_text_value(
                context.get(
                    "contextStatus"
                ),
                max_characters=30,
            )
        ),
        "question": question,
        "scope": normalize_scope(
            context.get("scope")
        ),
        "coverage": (
            normalize_coverage(
                context.get(
                    "coverage"
                )
            )
        ),
        "aggregateSignals": (
            compact_aggregates
        ),
        "timeline": timeline,
        "sourceSignals": (
            source_signals
        ),
        "semanticEvidence": (
            normalize_semantic_evidence(
                context.get(
                    "semanticEvidence"
                )
            )
        ),
    }

    serialized = json.dumps(
        prepared,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    if (
        len(serialized)
        > MAX_MODEL_CONTEXT_CHARACTERS
    ):
        raise AskAnswerInputError(
            "ContextTooLarge",
            (
                "Ask JM8 context exceeds "
                "the current model limit."
            ),
        )

    return prepared


def extract_response_text(
    response: dict[str, Any],
) -> str:
    content = (
        response.get("output", {})
        .get("message", {})
        .get("content", [])
    )

    for block in content:
        text = block.get("text")

        if (
            isinstance(text, str)
            and text.strip()
        ):
            return text.strip()

    raise AskAnswerResponseError(
        (
            "Ask JM8 received no "
            "structured answer."
        )
    )


def ranked_value_lookup(
    context: dict[str, Any],
    category: str,
) -> dict[str, dict[str, Any]]:
    aggregates = context.get(
        "aggregateSignals"
    )

    if not isinstance(
        aggregates,
        dict,
    ):
        return {}

    values = aggregates.get(
        category
    )

    if not isinstance(
        values,
        list,
    ):
        return {}

    result: dict[
        str,
        dict[str, Any],
    ] = {}

    for item in values:
        if not isinstance(item, dict):
            continue

        label = clean_text_value(
            item.get("value"),
            max_characters=180,
        )

        if not label:
            continue

        result[label.casefold()] = {
            "value": label,
            "count": max(
                integer_value(
                    item.get("count")
                ),
                0,
            ),
            "sharePercent": (
                percentage_value(
                    item.get(
                        "sharePercent"
                    )
                )
            ),
        }

    return result


def exact_grounded_values(
    raw_values: Any,
    *,
    allowed: dict[
        str,
        dict[str, Any],
    ],
    max_items: int,
) -> list[str]:
    requested = clean_string_list(
        raw_values,
        max_items=max_items * 2,
        max_characters=180,
    )

    results: list[str] = []
    seen: set[str] = set()

    for value in requested:
        identity = value.casefold()

        match = allowed.get(
            identity
        )

        if not match:
            continue

        canonical = str(
            match["value"]
        )

        canonical_identity = (
            canonical.casefold()
        )

        if canonical_identity in seen:
            continue

        seen.add(
            canonical_identity
        )

        results.append(canonical)

        if len(results) >= max_items:
            break

    return results


def normalize_evidence(
    raw_evidence: Any,
    *,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(
        raw_evidence,
        list,
    ):
        return []

    source_signals = context.get(
        "sourceSignals"
    )

    allowed_sources: set[
        tuple[str, str]
    ] = set()

    if isinstance(
        source_signals,
        list,
    ):
        for signal in source_signals:
            if not isinstance(
                signal,
                dict,
            ):
                continue

            signal_date = (
                clean_text_value(
                    signal.get("date"),
                    max_characters=10,
                )
            )

            source_type = (
                clean_text_value(
                    signal.get(
                        "sourceType"
                    ),
                    max_characters=20,
                ).casefold()
            )

            if (
                signal_date
                and source_type
                in {
                    "typed",
                    "image",
                    "unknown",
                }
            ):
                allowed_sources.add((
                    signal_date,
                    source_type,
                ))

    semantic_evidence = context.get(
        "semanticEvidence"
    )

    if isinstance(semantic_evidence, dict):
        semantic_items = semantic_evidence.get(
            "items"
        )
        for item in (
            semantic_items
            if isinstance(semantic_items, list)
            else []
        ):
            if not isinstance(item, dict):
                continue

            evidence_date = clean_text_value(
                item.get("date"),
                max_characters=10,
            )
            source_type = clean_text_value(
                item.get("sourceType"),
                max_characters=20,
            ).casefold()

            if (
                evidence_date
                and source_type
                in {"typed", "image", "unknown"}
            ):
                allowed_sources.add((
                    evidence_date,
                    source_type,
                ))

    results: list[
        dict[str, Any]
    ] = []

    seen: set[
        tuple[str, str, str]
    ] = set()

    for item in raw_evidence:
        if not isinstance(item, dict):
            continue

        paraphrase = clean_text_value(
            item.get("paraphrase"),
            max_characters=360,
        )

        evidence_date = (
            clean_text_value(
                item.get("date"),
                max_characters=10,
            )
        )

        source_type = (
            clean_text_value(
                item.get(
                    "sourceType"
                ),
                max_characters=20,
            ).casefold()
        )

        relevance = (
            clean_text_value(
                item.get("relevance"),
                max_characters=20,
            ).casefold()
        )

        if relevance not in {
            "high",
            "medium",
            "low",
        }:
            relevance = "medium"

        if not paraphrase:
            continue

        if (
            evidence_date,
            source_type,
        ) not in allowed_sources:
            continue

        identity = (
            paraphrase.casefold(),
            evidence_date,
            source_type,
        )

        if identity in seen:
            continue

        seen.add(identity)

        results.append({
            "paraphrase": (
                paraphrase
            ),
            "date": evidence_date,
            "sourceType": (
                source_type
            ),
            "relevance": relevance,
        })

        if (
            len(results)
            >= MAX_EVIDENCE_ITEMS
        ):
            break

    return results


def append_unique(
    values: list[str],
    value: str,
    *,
    max_items: int,
) -> None:
    normalized = clean_text_value(
        value,
        max_characters=360,
    )

    if not normalized:
        return

    identities = {
        item.casefold()
        for item in values
    }

    if (
        normalized.casefold()
        in identities
    ):
        return

    if len(values) >= max_items:
        return

    values.append(normalized)


def deterministic_limitations(
    context: dict[str, Any],
) -> list[str]:
    limitations: list[str] = []

    context_status = str(
        context.get(
            "contextStatus"
        )
        or ""
    ).upper()

    coverage = context.get(
        "coverage"
    )

    if not isinstance(
        coverage,
        dict,
    ):
        coverage = {}

    if context_status == "PARTIAL":
        append_unique(
            limitations,
            (
                "Some journal entries in "
                "the selected scope have not "
                "been analyzed, so the answer "
                "may change as coverage improves."
            ),
            max_items=MAX_LIMITATIONS,
        )

    if bool(
        coverage.get(
            "contextTruncated"
        )
    ):
        append_unique(
            limitations,
            (
                "JM8 used the highest-relevance "
                "source signals because the full "
                "matching history exceeded the "
                "answer context limit."
            ),
            max_items=MAX_LIMITATIONS,
        )

    semantic = context.get(
        "semanticEvidence"
    )

    if isinstance(semantic, dict):
        if bool(
            semantic.get(
                "contextTruncated"
            )
        ):
            append_unique(
                limitations,
                (
                    "JM8 used a bounded subset "
                    "of the matching journal "
                    "passages for this answer."
                ),
                max_items=MAX_LIMITATIONS,
            )

        if (
            integer_value(
                semantic.get(
                    "scopeExcludedEvidence"
                )
            )
            > 0
        ):
            append_unique(
                limitations,
                (
                    "Semantic matches outside "
                    "the selected date range "
                    "were excluded."
                ),
                max_items=MAX_LIMITATIONS,
            )

    return limitations


def build_empty_answer(
    context: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    return {
        "answerVersion": (
            get_answer_version()
        ),
        "generatedAt": utc_now(
            now
        ),
        "status": (
            "INSUFFICIENT_CONTEXT"
        ),
        "question": (
            context["question"]
        ),
        "scope": normalize_scope(
            context.get("scope")
        ),
        "coverage": (
            normalize_coverage(
                context.get(
                    "coverage"
                )
            )
        ),
        "answer": {
            "headline": (
                "Not enough analyzed "
                "journal history yet"
            ),
            "summary": (
                "JM8 needs at least one "
                "analyzed entry in this "
                "scope before it can answer "
                "this question responsibly."
            ),
            "explanation": (
                "Analyze more entries or "
                "choose a broader date range, "
                "then ask the question again."
            ),
        },
        "metrics": {
            "mentionCount": 0,
            "strongestPeriod": "",
            "improvementPercent": 0,
            "topTrigger": "",
        },
        "evidence": [],
        "takeaways": [],
        "relatedThemes": [],
        "growthSignals": [],
        "limitations": [
            (
                "No analyzed journal "
                "signals were available "
                "for this question."
            ),
        ],
        "suggestedFollowUps": [
            (
                "What should I reflect "
                "on in my next entry?"
            ),
        ],
    }


def normalize_answer_payload(
    payload: dict[str, Any],
    *,
    context: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    status = clean_text_value(
        payload.get("status"),
        max_characters=40,
    ).upper()

    if status not in {
        "ANSWERED",
        "INSUFFICIENT_CONTEXT",
    }:
        raise AskAnswerResponseError(
            (
                "Ask JM8 returned an "
                "invalid answer status."
            )
        )

    headline = clean_text_value(
        payload.get("headline"),
        max_characters=320,
    )

    summary = clean_text_value(
        payload.get("summary"),
        max_characters=900,
    )

    explanation = clean_text_value(
        payload.get("explanation"),
        max_characters=1_800,
    )

    if not headline or not summary:
        raise AskAnswerResponseError(
            (
                "Ask JM8 returned an "
                "incomplete answer."
            )
        )

    if not explanation:
        explanation = summary

    primary_signal = payload.get(
        "primarySignal"
    )

    if not isinstance(
        primary_signal,
        dict,
    ):
        primary_signal = {}

    primary_category = (
        clean_text_value(
            primary_signal.get(
                "category"
            ),
            max_characters=40,
        )
    )

    primary_value = (
        clean_text_value(
            primary_signal.get("value"),
            max_characters=180,
        )
    )

    matched_primary = None

    if (
        primary_category
        in PRIMARY_SIGNAL_CATEGORIES
        and primary_category != "none"
        and primary_value
    ):
        matched_primary = (
            ranked_value_lookup(
                context,
                primary_category,
            ).get(
                primary_value.casefold()
            )
        )

    mention_count = (
        matched_primary["count"]
        if matched_primary
        else 0
    )

    timeline = context.get(
        "timeline"
    )

    allowed_periods = {
        clean_text_value(
            item.get("period"),
            max_characters=20,
        )
        for item in (
            timeline
            if isinstance(
                timeline,
                list,
            )
            else []
        )
        if isinstance(item, dict)
    }

    strongest_period = (
        clean_text_value(
            payload.get(
                "strongestPeriod"
            ),
            max_characters=20,
        )
    )

    if (
        strongest_period
        not in allowed_periods
    ):
        strongest_period = ""

    themes_allowed = {
        **ranked_value_lookup(
            context,
            "themes",
        ),
        **ranked_value_lookup(
            context,
            "emergingTopics",
        ),
    }

    growth_allowed = (
        ranked_value_lookup(
            context,
            "growthSignals",
        )
    )

    limitations = clean_string_list(
        payload.get("limitations"),
        max_items=MAX_LIMITATIONS,
        max_characters=360,
    )

    for limitation in (
        deterministic_limitations(
            context
        )
    ):
        append_unique(
            limitations,
            limitation,
            max_items=MAX_LIMITATIONS,
        )

    if (
        status
        == "INSUFFICIENT_CONTEXT"
        and not limitations
    ):
        append_unique(
            limitations,
            (
                "The available analyzed "
                "signals were not sufficient "
                "to answer this question "
                "responsibly."
            ),
            max_items=MAX_LIMITATIONS,
        )

    return {
        "answerVersion": (
            get_answer_version()
        ),
        "generatedAt": utc_now(
            now
        ),
        "status": status,
        "question": (
            context["question"]
        ),
        "scope": normalize_scope(
            context.get("scope")
        ),
        "coverage": (
            normalize_coverage(
                context.get(
                    "coverage"
                )
            )
        ),
        "answer": {
            "headline": headline,
            "summary": summary,
            "explanation": (
                explanation
            ),
        },
        "metrics": {
            "mentionCount": (
                mention_count
            ),
            "strongestPeriod": (
                strongest_period
            ),
            "improvementPercent": (
                percentage_value(
                    payload.get(
                        "improvementPercent"
                    )
                )
            ),
            "topTrigger": (
                clean_text_value(
                    payload.get(
                        "topTrigger"
                    ),
                    max_characters=240,
                )
            ),
        },
        "evidence": (
            normalize_evidence(
                payload.get("evidence"),
                context=context,
            )
        ),
        "takeaways": (
            clean_string_list(
                payload.get(
                    "takeaways"
                ),
                max_items=MAX_TAKEAWAYS,
                max_characters=360,
            )
        ),
        "relatedThemes": (
            exact_grounded_values(
                payload.get(
                    "relatedThemes"
                ),
                allowed=themes_allowed,
                max_items=(
                    MAX_RELATED_THEMES
                ),
            )
        ),
        "growthSignals": (
            exact_grounded_values(
                payload.get(
                    "growthSignals"
                ),
                allowed=growth_allowed,
                max_items=(
                    MAX_GROWTH_SIGNALS
                ),
            )
        ),
        "limitations": limitations,
        "suggestedFollowUps": (
            clean_string_list(
                payload.get(
                    "suggestedFollowUps"
                ),
                max_items=MAX_FOLLOW_UPS,
                max_characters=240,
            )
        ),
    }


def answer_journal_history(
    context: Any,
    *,
    client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    prepared_context = (
        prepare_model_context(
            context
        )
    )

    analyzed_entries = (
        prepared_context[
            "coverage"
        ]["analyzedEntries"]
    )

    semantic_items = (
        prepared_context[
            "semanticEvidence"
        ]["items"]
    )

    if (
        analyzed_entries == 0
        and not semantic_items
    ):
        return build_empty_answer(
            prepared_context,
            now=now,
        )

    model_id = get_model_id()

    bedrock = (
        client
        or create_bedrock_client()
    )

    context_json = json.dumps(
        prepared_context,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        response = bedrock.converse(
            modelId=model_id,
            system=[
                {
                    "text": (
                        SYSTEM_PROMPT
                    ),
                },
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Answer this private "
                                "journal-history question "
                                "using only the supplied "
                                "grounded context.\n\n"
                                f"{context_json}"
                            ),
                        },
                    ],
                },
            ],
            inferenceConfig={
                "maxTokens": 2_400,
                "temperature": 0.1,
            },
            outputConfig={
                "textFormat": {
                    "type": (
                        "json_schema"
                    ),
                    "structure": {
                        "jsonSchema": {
                            "name": (
                                "jm8_history_"
                                "answer_v2"
                            ),
                            "description": (
                                "Grounded structured "
                                "answer across private "
                                "Journal M8 history."
                            ),
                            "schema": (
                                json.dumps(
                                    (
                                        ASK_ANSWER_OUTPUT_SCHEMA
                                    ),
                                    separators=(
                                        ",",
                                        ":",
                                    ),
                                )
                            ),
                        },
                    },
                },
            },
            requestMetadata={
                "application": (
                    "journalm8"
                ),
                "stage": (
                    os.environ.get(
                        "STAGE"
                    )
                    or "dev"
                ),
                "purpose": (
                    "ask-jm8-history"
                ),
                "answer-version": (
                    get_answer_version()
                ),
                "prompt-version": (
                    get_prompt_version()
                ),
            },
        )

    except ClientError as exc:
        error = (
            exc.response.get("Error")
            or {}
        )

        metadata = (
            exc.response.get(
                "ResponseMetadata"
            )
            or {}
        )

        error_code = str(
            error.get("Code")
            or "Unknown"
        )

        retry_attempts = int(
            metadata.get(
                "RetryAttempts"
            )
            or 0
        )

        raise AskAnswerInvocationError(
            (
                "Ask JM8 could not "
                "complete the request."
            ),
            error_code=error_code,
            retryable=(
                error_code
                in (
                    RETRYABLE_BEDROCK_ERROR_CODES
                )
            ),
            retry_attempts=(
                retry_attempts
            ),
        ) from exc

    except BotoCoreError as exc:
        raise AskAnswerInvocationError(
            (
                "Ask JM8 could not "
                "complete the request."
            ),
            error_code=(
                type(exc).__name__
            ),
            retryable=True,
            retry_attempts=0,
        ) from exc

    try:
        raw_response = (
            extract_response_text(
                response
            )
        )

        payload = json.loads(
            raw_response
        )

    except json.JSONDecodeError as exc:
        raise AskAnswerResponseError(
            (
                "Ask JM8 returned "
                "invalid JSON."
            )
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise AskAnswerResponseError(
            (
                "Ask JM8 answer was "
                "not an object."
            )
        )

    normalized = (
        normalize_answer_payload(
            payload,
            context=prepared_context,
            now=now,
        )
    )

    usage = response.get(
        "usage"
    ) or {}

    metrics = response.get(
        "metrics"
    ) or {}

    print(json.dumps(
        {
            "event": (
                "ask_jm8_answer_"
                "completed"
            ),
            "status": (
                normalized["status"]
            ),
            "analyzedEntries": (
                prepared_context[
                    "coverage"
                ]["analyzedEntries"]
            ),
            "sourceSignals": len(
                prepared_context[
                    "sourceSignals"
                ]
            ),
            "semanticEvidence": len(
                prepared_context[
                    "semanticEvidence"
                ]["items"]
            ),
            "evidenceItems": len(
                normalized["evidence"]
            ),
            "totalTokens": int(
                usage.get(
                    "totalTokens"
                )
                or 0
            ),
            "latencyMs": int(
                metrics.get(
                    "latencyMs"
                )
                or 0
            ),
        },
        sort_keys=True,
    ))

    return normalized
