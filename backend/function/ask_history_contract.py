from __future__ import annotations

import re
import uuid
from datetime import (
    date,
    datetime,
    timezone,
)
from typing import (
    Any,
    Mapping,
)


ASK_HISTORY_VERSION = "1.0"

ASK_HISTORY_ENTITY_TYPE = (
    "ASK_HISTORY"
)

ASK_HISTORY_SK_PREFIX = (
    "ASK_HISTORY#"
)

ASK_HISTORY_ID_PATTERN = re.compile(
    r"^askhist_[a-z0-9]{12,64}$"
)

ASK_HISTORY_STATUSES = {
    "ANSWERED",
    "INSUFFICIENT_CONTEXT",
}

ASK_HISTORY_SOURCE_TYPES = {
    "typed",
    "image",
    "unknown",
}

ASK_HISTORY_RELEVANCE_LEVELS = {
    "high",
    "medium",
    "low",
}

MAX_QUESTION_CHARACTERS = 500
MAX_HEADLINE_CHARACTERS = 240
MAX_SUMMARY_CHARACTERS = 2_000
MAX_EXPLANATION_CHARACTERS = 8_000
MAX_EVIDENCE_ITEMS = 6
MAX_LIST_ITEM_CHARACTERS = 500

MAX_LIST_ITEMS = {
    "takeaways": 6,
    "relatedThemes": 10,
    "growthSignals": 6,
    "limitations": 6,
    "suggestedFollowUps": 4,
}

FORBIDDEN_HISTORY_FIELDS = {
    "PK",
    "SK",
    "GSI1PK",
    "GSI1SK",
    "userId",
    "entryId",
    "jobId",
    "reservationId",
    "rawText",
    "cleanText",
    "s3RawKey",
    "s3RawBucket",
    "imagePreviewUrl",
    "executionArn",
    "executionName",
    "accessToken",
    "idToken",
    "refreshToken",
    "authorization",
    "systemPrompt",
    "prompt",
    "modelRequest",
}


class AskHistoryContractError(
    ValueError
):
    def __init__(
        self,
        code: str,
        message: str,
    ):
        super().__init__(message)

        self.code = str(code)
        self.message = str(message)


def _raise_contract_error(
    code: str,
    message: str,
) -> None:
    raise AskHistoryContractError(
        code,
        message,
    )


def _require_mapping(
    value: Any,
    *,
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(
        value,
        Mapping,
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must be "
                "an object."
            ),
        )

    return value


def _require_list(
    value: Any,
    *,
    field: str,
) -> list[Any]:
    if not isinstance(
        value,
        list,
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must be "
                "an array."
            ),
        )

    return value


def _require_keys(
    value: Mapping[str, Any],
    *,
    field: str,
    keys: set[str],
) -> None:
    missing = sorted(
        keys - set(value)
    )

    if missing:
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} is missing "
                f"required fields: "
                f"{', '.join(missing)}."
            ),
        )


def _clean_text(
    value: Any,
    *,
    field: str,
    max_characters: int,
    minimum_characters: int = 0,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            f"{field} must be text.",
        )

    normalized = value.strip()

    if (
        len(normalized)
        < minimum_characters
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must contain at "
                f"least {minimum_characters} "
                "characters."
            ),
        )

    if (
        len(normalized)
        > max_characters
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} exceeds the "
                f"{max_characters}-character "
                "limit."
            ),
        )

    return normalized


def _normalize_timestamp(
    value: Any,
    *,
    field: str,
) -> str:
    text = _clean_text(
        value,
        field=field,
        max_characters=60,
        minimum_characters=1,
    )

    try:
        parsed = datetime.fromisoformat(
            text.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        _raise_contract_error(
            "InvalidAskHistoryTimestamp",
            (
                f"{field} must be a valid "
                "ISO-8601 timestamp."
            ),
        )

    if parsed.tzinfo is None:
        _raise_contract_error(
            "InvalidAskHistoryTimestamp",
            (
                f"{field} must contain "
                "a timezone."
            ),
        )

    return (
        parsed.astimezone(
            timezone.utc
        ).isoformat()
    )


def _normalize_optional_date(
    value: Any,
    *,
    field: str,
) -> str | None:
    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must be a date "
                "or null."
            ),
        )

    normalized = value.strip()

    if not normalized:
        return None

    try:
        parsed = date.fromisoformat(
            normalized
        )
    except ValueError:
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must use "
                "YYYY-MM-DD format."
            ),
        )

    return parsed.isoformat()


def _normalize_nonnegative_integer(
    value: Any,
    *,
    field: str,
) -> int:
    if isinstance(
        value,
        bool,
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must be "
                "an integer."
            ),
        )

    try:
        normalized = int(value)
    except (
        TypeError,
        ValueError,
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must be "
                "an integer."
            ),
        )

    if normalized < 0:
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} cannot "
                "be negative."
            ),
        )

    return normalized


def _normalize_percentage(
    value: Any,
    *,
    field: str,
) -> int:
    normalized = (
        _normalize_nonnegative_integer(
            value,
            field=field,
        )
    )

    if normalized > 100:
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must be between "
                "0 and 100."
            ),
        )

    return normalized


def _normalize_boolean(
    value: Any,
    *,
    field: str,
) -> bool:
    if not isinstance(
        value,
        bool,
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} must be "
                "a boolean."
            ),
        )

    return value


def _normalize_string_list(
    value: Any,
    *,
    field: str,
    limit: int,
) -> list[str]:
    raw_items = _require_list(
        value,
        field=field,
    )

    if len(raw_items) > limit:
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                f"{field} cannot contain "
                f"more than {limit} items."
            ),
        )

    normalized: list[str] = []
    seen: set[str] = set()

    for index, item in enumerate(
        raw_items
    ):
        text = _clean_text(
            item,
            field=(
                f"{field}[{index}]"
            ),
            max_characters=(
                MAX_LIST_ITEM_CHARACTERS
            ),
            minimum_characters=1,
        )

        dedupe_key = text.casefold()

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        normalized.append(text)

    return normalized


def normalize_ask_history_id(
    value: Any,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        _raise_contract_error(
            "InvalidAskHistoryId",
            (
                "historyId must be "
                "text."
            ),
        )

    normalized = value.strip().lower()

    if not ASK_HISTORY_ID_PATTERN.fullmatch(
        normalized
    ):
        _raise_contract_error(
            "InvalidAskHistoryId",
            (
                "historyId is not in the "
                "expected JM8 format."
            ),
        )

    return normalized


def new_ask_history_id() -> str:
    return (
        "askhist_"
        + uuid.uuid4().hex[:20]
    )


def ask_history_prefix() -> str:
    return ASK_HISTORY_SK_PREFIX


def ask_history_sk(
    created_at: Any,
    history_id: Any,
) -> str:
    normalized_created_at = (
        _normalize_timestamp(
            created_at,
            field="createdAt",
        )
    )

    normalized_history_id = (
        normalize_ask_history_id(
            history_id
        )
    )

    return (
        f"{ASK_HISTORY_SK_PREFIX}"
        f"{normalized_created_at}#"
        f"{normalized_history_id}"
    )


def _normalize_scope(
    value: Any,
) -> dict[str, str | None]:
    scope = _require_mapping(
        value,
        field="scope",
    )

    _require_keys(
        scope,
        field="scope",
        keys={
            "startDate",
            "endDate",
            "firstEntryAt",
            "latestEntryAt",
        },
    )

    return {
        "startDate": (
            _normalize_optional_date(
                scope.get("startDate"),
                field="scope.startDate",
            )
        ),
        "endDate": (
            _normalize_optional_date(
                scope.get("endDate"),
                field="scope.endDate",
            )
        ),
        "firstEntryAt": (
            _normalize_optional_date(
                scope.get("firstEntryAt"),
                field="scope.firstEntryAt",
            )
        ),
        "latestEntryAt": (
            _normalize_optional_date(
                scope.get("latestEntryAt"),
                field="scope.latestEntryAt",
            )
        ),
    }


def _normalize_coverage(
    value: Any,
) -> dict[str, int | bool]:
    coverage = _require_mapping(
        value,
        field="coverage",
    )

    _require_keys(
        coverage,
        field="coverage",
        keys={
            "totalEntries",
            "analyzedEntries",
            "unanalyzedEntries",
            "analysisCompletionPercent",
            "sourceSignalsAvailable",
            "sourceSignalsIncluded",
            "contextTruncated",
        },
    )

    return {
        "totalEntries": (
            _normalize_nonnegative_integer(
                coverage.get(
                    "totalEntries"
                ),
                field=(
                    "coverage.totalEntries"
                ),
            )
        ),
        "analyzedEntries": (
            _normalize_nonnegative_integer(
                coverage.get(
                    "analyzedEntries"
                ),
                field=(
                    "coverage."
                    "analyzedEntries"
                ),
            )
        ),
        "unanalyzedEntries": (
            _normalize_nonnegative_integer(
                coverage.get(
                    "unanalyzedEntries"
                ),
                field=(
                    "coverage."
                    "unanalyzedEntries"
                ),
            )
        ),
        "analysisCompletionPercent": (
            _normalize_percentage(
                coverage.get(
                    "analysisCompletionPercent"
                ),
                field=(
                    "coverage."
                    "analysisCompletionPercent"
                ),
            )
        ),
        "sourceSignalsAvailable": (
            _normalize_nonnegative_integer(
                coverage.get(
                    "sourceSignalsAvailable"
                ),
                field=(
                    "coverage."
                    "sourceSignalsAvailable"
                ),
            )
        ),
        "sourceSignalsIncluded": (
            _normalize_nonnegative_integer(
                coverage.get(
                    "sourceSignalsIncluded"
                ),
                field=(
                    "coverage."
                    "sourceSignalsIncluded"
                ),
            )
        ),
        "contextTruncated": (
            _normalize_boolean(
                coverage.get(
                    "contextTruncated"
                ),
                field=(
                    "coverage."
                    "contextTruncated"
                ),
            )
        ),
    }


def _normalize_answer_body(
    value: Any,
) -> dict[str, str]:
    body = _require_mapping(
        value,
        field="answer",
    )

    _require_keys(
        body,
        field="answer",
        keys={
            "headline",
            "summary",
            "explanation",
        },
    )

    return {
        "headline": _clean_text(
            body.get("headline"),
            field="answer.headline",
            max_characters=(
                MAX_HEADLINE_CHARACTERS
            ),
            minimum_characters=1,
        ),
        "summary": _clean_text(
            body.get("summary"),
            field="answer.summary",
            max_characters=(
                MAX_SUMMARY_CHARACTERS
            ),
            minimum_characters=1,
        ),
        "explanation": _clean_text(
            body.get("explanation"),
            field="answer.explanation",
            max_characters=(
                MAX_EXPLANATION_CHARACTERS
            ),
            minimum_characters=1,
        ),
    }


def _normalize_metrics(
    value: Any,
) -> dict[str, Any]:
    metrics = _require_mapping(
        value,
        field="metrics",
    )

    _require_keys(
        metrics,
        field="metrics",
        keys={
            "mentionCount",
            "strongestPeriod",
            "improvementPercent",
            "topTrigger",
        },
    )

    return {
        "mentionCount": (
            _normalize_nonnegative_integer(
                metrics.get(
                    "mentionCount"
                ),
                field=(
                    "metrics.mentionCount"
                ),
            )
        ),
        "strongestPeriod": (
            _clean_text(
                metrics.get(
                    "strongestPeriod"
                ),
                field=(
                    "metrics."
                    "strongestPeriod"
                ),
                max_characters=60,
            )
        ),
        "improvementPercent": (
            _normalize_percentage(
                metrics.get(
                    "improvementPercent"
                ),
                field=(
                    "metrics."
                    "improvementPercent"
                ),
            )
        ),
        "topTrigger": (
            _clean_text(
                metrics.get(
                    "topTrigger"
                ),
                field=(
                    "metrics.topTrigger"
                ),
                max_characters=500,
            )
        ),
    }


def _normalize_evidence(
    value: Any,
) -> list[dict[str, str]]:
    evidence = _require_list(
        value,
        field="evidence",
    )

    if (
        len(evidence)
        > MAX_EVIDENCE_ITEMS
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            (
                "evidence cannot contain "
                f"more than "
                f"{MAX_EVIDENCE_ITEMS} "
                "items."
            ),
        )

    normalized: list[
        dict[str, str]
    ] = []

    for index, raw_item in enumerate(
        evidence
    ):
        item = _require_mapping(
            raw_item,
            field=f"evidence[{index}]",
        )

        _require_keys(
            item,
            field=f"evidence[{index}]",
            keys={
                "paraphrase",
                "date",
                "sourceType",
                "relevance",
            },
        )

        source_type = _clean_text(
            item.get("sourceType"),
            field=(
                f"evidence[{index}]."
                "sourceType"
            ),
            max_characters=20,
            minimum_characters=1,
        ).lower()

        relevance = _clean_text(
            item.get("relevance"),
            field=(
                f"evidence[{index}]."
                "relevance"
            ),
            max_characters=20,
            minimum_characters=1,
        ).lower()

        if (
            source_type
            not in ASK_HISTORY_SOURCE_TYPES
        ):
            _raise_contract_error(
                "InvalidAskHistoryAnswer",
                (
                    f"evidence[{index}]."
                    "sourceType is invalid."
                ),
            )

        if (
            relevance
            not in (
                ASK_HISTORY_RELEVANCE_LEVELS
            )
        ):
            _raise_contract_error(
                "InvalidAskHistoryAnswer",
                (
                    f"evidence[{index}]."
                    "relevance is invalid."
                ),
            )

        normalized.append({
            "paraphrase": _clean_text(
                item.get("paraphrase"),
                field=(
                    f"evidence[{index}]."
                    "paraphrase"
                ),
                max_characters=800,
                minimum_characters=1,
            ),
            "date": _clean_text(
                item.get("date"),
                field=(
                    f"evidence[{index}].date"
                ),
                max_characters=60,
            ),
            "sourceType": source_type,
            "relevance": relevance,
        })

    return normalized


def normalize_ask_history_answer(
    value: Any,
) -> dict[str, Any]:
    answer = _require_mapping(
        value,
        field="Ask JM8 answer",
    )

    _require_keys(
        answer,
        field="Ask JM8 answer",
        keys={
            "answerVersion",
            "generatedAt",
            "status",
            "question",
            "scope",
            "coverage",
            "answer",
            "metrics",
            "evidence",
            "takeaways",
            "relatedThemes",
            "growthSignals",
            "limitations",
            "suggestedFollowUps",
        },
    )

    status = _clean_text(
        answer.get("status"),
        field="status",
        max_characters=40,
        minimum_characters=1,
    ).upper()

    if (
        status
        not in ASK_HISTORY_STATUSES
    ):
        _raise_contract_error(
            "InvalidAskHistoryAnswer",
            "Ask JM8 status is invalid.",
        )

    normalized: dict[str, Any] = {
        "answerVersion": _clean_text(
            answer.get(
                "answerVersion"
            ),
            field="answerVersion",
            max_characters=40,
            minimum_characters=1,
        ),
        "generatedAt": (
            _normalize_timestamp(
                answer.get(
                    "generatedAt"
                ),
                field="generatedAt",
            )
        ),
        "status": status,
        "question": _clean_text(
            answer.get("question"),
            field="question",
            max_characters=(
                MAX_QUESTION_CHARACTERS
            ),
            minimum_characters=3,
        ),
        "scope": _normalize_scope(
            answer.get("scope")
        ),
        "coverage": (
            _normalize_coverage(
                answer.get("coverage")
            )
        ),
        "answer": (
            _normalize_answer_body(
                answer.get("answer")
            )
        ),
        "metrics": (
            _normalize_metrics(
                answer.get("metrics")
            )
        ),
        "evidence": (
            _normalize_evidence(
                answer.get("evidence")
            )
        ),
    }

    for field, limit in (
        MAX_LIST_ITEMS.items()
    ):
        normalized[field] = (
            _normalize_string_list(
                answer.get(field),
                field=field,
                limit=limit,
            )
        )

    return normalized


def build_ask_history_item(
    *,
    user_id: Any,
    answer: Any,
    history_id: Any | None = None,
) -> dict[str, Any]:
    normalized_user_id = _clean_text(
        user_id,
        field="userId",
        max_characters=180,
        minimum_characters=1,
    )

    normalized_answer = (
        normalize_ask_history_answer(
            answer
        )
    )

    normalized_history_id = (
        normalize_ask_history_id(
            history_id
            or new_ask_history_id()
        )
    )

    created_at = normalized_answer[
        "generatedAt"
    ]

    return {
        "PK": (
            f"USER#{normalized_user_id}"
        ),
        "SK": ask_history_sk(
            created_at,
            normalized_history_id,
        ),
        "entityType": (
            ASK_HISTORY_ENTITY_TYPE
        ),
        "historyVersion": (
            ASK_HISTORY_VERSION
        ),
        "historyId": (
            normalized_history_id
        ),
        "createdAt": created_at,
        "answer": normalized_answer,
    }


def _normalize_history_record(
    value: Any,
) -> dict[str, Any]:
    record = _require_mapping(
        value,
        field="Ask history record",
    )

    if (
        record.get("entityType")
        != ASK_HISTORY_ENTITY_TYPE
    ):
        _raise_contract_error(
            "InvalidAskHistoryRecord",
            (
                "The item is not an "
                "Ask JM8 history record."
            ),
        )

    history_id = (
        normalize_ask_history_id(
            record.get("historyId")
        )
    )

    created_at = _normalize_timestamp(
        record.get("createdAt"),
        field="createdAt",
    )

    answer = (
        normalize_ask_history_answer(
            record.get("answer")
        )
    )

    if (
        answer["generatedAt"]
        != created_at
    ):
        _raise_contract_error(
            "InvalidAskHistoryRecord",
            (
                "History creation time does "
                "not match the answer."
            ),
        )

    return {
        "historyVersion": _clean_text(
            record.get(
                "historyVersion"
            ),
            field="historyVersion",
            max_characters=40,
            minimum_characters=1,
        ),
        "historyId": history_id,
        "createdAt": created_at,
        "answer": answer,
    }


def build_public_ask_history_detail(
    value: Any,
) -> dict[str, Any]:
    return _normalize_history_record(
        value
    )


def build_public_ask_history_summary(
    value: Any,
) -> dict[str, Any]:
    detail = (
        _normalize_history_record(
            value
        )
    )

    answer = detail["answer"]

    return {
        "historyVersion": (
            detail["historyVersion"]
        ),
        "historyId": (
            detail["historyId"]
        ),
        "createdAt": (
            detail["createdAt"]
        ),
        "answerVersion": (
            answer["answerVersion"]
        ),
        "question": answer["question"],
        "status": answer["status"],
        "scope": answer["scope"],
        "headline": (
            answer["answer"]["headline"]
        ),
        "summary": (
            answer["answer"]["summary"]
        ),
        "evidenceCount": len(
            answer["evidence"]
        ),
        "takeawayCount": len(
            answer["takeaways"]
        ),
    }
