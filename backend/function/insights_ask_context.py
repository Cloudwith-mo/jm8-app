from collections import defaultdict
from datetime import (
    date,
    datetime,
    timezone,
)
import re
from typing import Any

from insights_overview import (
    clean_text,
    first_ranked_value,
    get_entry_analysis,
    ranked_analysis_values,
)


ASK_CONTEXT_VERSION = "1.0"

QUESTION_MIN_CHARACTERS = 3
QUESTION_MAX_CHARACTERS = 500

MAX_SOURCE_SIGNALS = 24
MAX_TIMELINE_PERIODS = 24
MAX_RANKED_ITEMS = 8
MAX_VALUES_PER_SOURCE = 6
MAX_VALUE_CHARACTERS = 180


QUESTION_STOP_WORDS = {
    "a",
    "about",
    "all",
    "am",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "by",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "keep",
    "keeps",
    "last",
    "me",
    "most",
    "my",
    "of",
    "on",
    "or",
    "return",
    "returning",
    "the",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "which",
    "who",
    "why",
    "with",
    "year",
}


FIELD_HINTS = {
    "mood": {
        "anxious",
        "emotion",
        "emotional",
        "feel",
        "feeling",
        "felt",
        "mood",
        "sad",
    },
    "sentiment": {
        "negative",
        "positive",
        "sentiment",
    },
    "themes": {
        "recurring",
        "subject",
        "theme",
        "themes",
        "topic",
        "topics",
    },
    "challenges": {
        "challenge",
        "challenges",
        "concern",
        "difficult",
        "hard",
        "obstacle",
        "problem",
        "struggle",
        "struggles",
    },
    "goals": {
        "aim",
        "goal",
        "goals",
        "target",
        "want",
    },
    "growthSignals": {
        "change",
        "changed",
        "growth",
        "improve",
        "improved",
        "improvement",
        "overcome",
        "progress",
        "stronger",
    },
    "behaviorPatterns": {
        "behavior",
        "consistency",
        "discipline",
        "execution",
        "habit",
        "habits",
        "pattern",
        "patterns",
        "routine",
        "routines",
    },
    "peopleAndTopics": {
        "mention",
        "mentions",
        "people",
        "person",
    },
    "keyInsights": {
        "advice",
        "learn",
        "learned",
        "lesson",
        "mindset",
        "understand",
    },
}


class AskContextInputError(
    ValueError
):
    def __init__(
        self,
        code: str,
        message: str,
    ):
        super().__init__(message)

        self.code = code
        self.message = message


def normalize_now(
    now: datetime | None,
) -> datetime:
    if now is None:
        return datetime.now(
            timezone.utc
        )

    if now.tzinfo is None:
        return now.replace(
            tzinfo=timezone.utc
        )

    return now.astimezone(
        timezone.utc
    )


def normalize_question(
    value: Any,
) -> str:
    if not isinstance(value, str):
        raise AskContextInputError(
            "InvalidQuestion",
            (
                "question must be "
                "a string."
            ),
        )

    question = clean_text(
        value,
        max_characters=(
            QUESTION_MAX_CHARACTERS
            + 1
        ),
    )

    if (
        len(question)
        < QUESTION_MIN_CHARACTERS
    ):
        raise AskContextInputError(
            "InvalidQuestion",
            (
                "question must contain "
                "at least 3 characters."
            ),
        )

    if (
        len(question)
        > QUESTION_MAX_CHARACTERS
    ):
        raise AskContextInputError(
            "InvalidQuestion",
            (
                "question must contain "
                "500 characters or fewer."
            ),
        )

    return question


def parse_scope_date(
    value: Any,
    *,
    field_name: str,
) -> date | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise AskContextInputError(
            f"Invalid{field_name}",
            (
                f"{field_name} must use "
                "YYYY-MM-DD format."
            ),
        )

    normalized = value.strip()

    if not normalized:
        return None

    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}",
        normalized,
    ):
        raise AskContextInputError(
            f"Invalid{field_name}",
            (
                f"{field_name} must use "
                "YYYY-MM-DD format."
            ),
        )

    try:
        return date.fromisoformat(
            normalized
        )
    except ValueError as exc:
        raise AskContextInputError(
            f"Invalid{field_name}",
            (
                f"{field_name} must be "
                "a valid calendar date."
            ),
        ) from exc


def resolve_scope(
    *,
    start_date: Any = None,
    end_date: Any = None,
    now: datetime | None = None,
) -> tuple[
    date | None,
    date | None,
]:
    current_date = normalize_now(
        now
    ).date()

    parsed_start = parse_scope_date(
        start_date,
        field_name="StartDate",
    )

    parsed_end = parse_scope_date(
        end_date,
        field_name="EndDate",
    )

    if (
        parsed_start
        and parsed_start
        > current_date
    ):
        raise AskContextInputError(
            "FutureDateRange",
            (
                "startDate cannot be "
                "in the future."
            ),
        )

    if (
        parsed_end
        and parsed_end
        > current_date
    ):
        raise AskContextInputError(
            "FutureDateRange",
            (
                "endDate cannot be "
                "in the future."
            ),
        )

    if (
        parsed_start
        and parsed_end
        and parsed_start
        > parsed_end
    ):
        raise AskContextInputError(
            "InvalidDateRange",
            (
                "startDate cannot be "
                "after endDate."
            ),
        )

    return (
        parsed_start,
        parsed_end,
    )


def parse_entry_datetime(
    entry: dict[str, Any],
) -> datetime | None:
    raw_value = clean_text(
        entry.get("createdAt"),
        max_characters=80,
    )

    if not raw_value:
        return None

    normalized = raw_value

    if normalized.endswith("Z"):
        normalized = (
            normalized[:-1]
            + "+00:00"
        )

    try:
        parsed = datetime.fromisoformat(
            normalized
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def entry_is_in_scope(
    entry: dict[str, Any],
    *,
    start_date: date | None,
    end_date: date | None,
) -> bool:
    if (
        start_date is None
        and end_date is None
    ):
        return True

    created_at = parse_entry_datetime(
        entry
    )

    if created_at is None:
        return False

    created_date = created_at.date()

    if (
        start_date
        and created_date
        < start_date
    ):
        return False

    if (
        end_date
        and created_date
        > end_date
    ):
        return False

    return True


def safe_values(
    value: Any,
    *,
    max_items: int = (
        MAX_VALUES_PER_SOURCE
    ),
) -> list[str]:
    raw_values = (
        value
        if isinstance(value, list)
        else [value]
    )

    values: list[str] = []
    seen: set[str] = set()

    for raw_value in raw_values:
        cleaned = clean_text(
            raw_value,
            max_characters=(
                MAX_VALUE_CHARACTERS
            ),
        )

        if not cleaned:
            continue

        identity = cleaned.casefold()

        if identity in seen:
            continue

        seen.add(identity)
        values.append(cleaned)

        if len(values) >= max_items:
            break

    return values


def safe_scalar(
    value: Any,
) -> str | None:
    values = safe_values(
        value,
        max_items=1,
    )

    return (
        values[0]
        if values
        else None
    )


def normalize_source_type(
    value: Any,
) -> str:
    source_type = clean_text(
        value,
        max_characters=32,
    ).casefold()

    if source_type in {
        "image",
        "typed",
    }:
        return source_type

    return "unknown"


def build_source_signal(
    entry: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    created_at = parse_entry_datetime(
        entry
    )

    return {
        "date": (
            created_at.date().isoformat()
            if created_at
            else None
        ),
        "sourceType": (
            normalize_source_type(
                entry.get(
                    "sourceType"
                )
            )
        ),
        "mood": safe_scalar(
            analysis.get("mood")
        ),
        "sentiment": safe_scalar(
            analysis.get(
                "sentiment"
            )
        ),
        "secondaryMoods": safe_values(
            analysis.get(
                "secondaryMoods"
            )
        ),
        "themes": safe_values(
            analysis.get("themes")
        ),
        "emergingTopics": safe_values(
            analysis.get(
                "emergingTopics"
            )
        ),
        "keyInsights": safe_values(
            analysis.get(
                "keyInsights"
            )
        ),
        "challenges": safe_values(
            analysis.get(
                "challenges"
            )
        ),
        "goals": safe_values(
            analysis.get("goals")
        ),
        "growthSignals": safe_values(
            analysis.get(
                "growthSignals"
            )
        ),
        "behaviorPatterns": safe_values(
            analysis.get(
                "behaviorPatterns"
            )
        ),
        "peopleAndTopics": safe_values(
            analysis.get(
                "peopleAndTopics"
            )
        ),
        "nextStep": safe_scalar(
            analysis.get("nextStep")
        ),
    }


def tokenize(
    value: str,
) -> set[str]:
    tokens = {
        token.casefold()
        for token in re.findall(
            r"[A-Za-z0-9']+",
            value,
        )
    }

    return {
        token
        for token in tokens
        if (
            token
            not in QUESTION_STOP_WORDS
            and len(token) >= 2
        )
    }


def question_field_hints(
    question_tokens: set[str],
) -> set[str]:
    return {
        field
        for field, hints
        in FIELD_HINTS.items()
        if question_tokens.intersection(
            hints
        )
    }


def source_signal_blob(
    signal: dict[str, Any],
) -> str:
    values: list[str] = []

    for key, value in signal.items():
        if key in {
            "date",
            "sourceType",
        }:
            continue

        if isinstance(value, list):
            values.extend(
                str(item)
                for item in value
            )
        elif value:
            values.append(
                str(value)
            )

    return " ".join(values)


def score_source_signal(
    signal: dict[str, Any],
    *,
    question_tokens: set[str],
    hinted_fields: set[str],
) -> int:
    blob = source_signal_blob(
        signal
    ).casefold()

    signal_tokens = tokenize(
        blob
    )

    overlap = (
        question_tokens
        .intersection(
            signal_tokens
        )
    )

    score = len(overlap) * 4

    for token in question_tokens:
        if (
            len(token) >= 5
            and token in blob
            and token not in overlap
        ):
            score += 1

    for field in hinted_fields:
        value = signal.get(field)

        if isinstance(value, list):
            has_value = bool(value)
        else:
            has_value = bool(value)

        if has_value:
            score += 2

    return score


def select_source_signals(
    analyzed_entries: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ],
    *,
    question: str,
) -> list[dict[str, Any]]:
    question_tokens = tokenize(
        question
    )

    hinted_fields = (
        question_field_hints(
            question_tokens
        )
    )

    ranked: list[
        tuple[
            dict[str, Any],
            int,
            datetime,
        ]
    ] = []

    minimum_datetime = (
        datetime.min.replace(
            tzinfo=timezone.utc
        )
    )

    for entry, analysis in analyzed_entries:
        signal = build_source_signal(
            entry,
            analysis,
        )

        score = score_source_signal(
            signal,
            question_tokens=(
                question_tokens
            ),
            hinted_fields=(
                hinted_fields
            ),
        )

        ranked.append((
            signal,
            score,
            (
                parse_entry_datetime(
                    entry
                )
                or minimum_datetime
            ),
        ))

    ranked.sort(
        key=lambda item: (
            item[1],
            item[2],
        ),
        reverse=True,
    )

    return [
        {
            **signal,
            "relevanceScore": score,
        }
        for signal, score, _
        in ranked[
            :MAX_SOURCE_SIGNALS
        ]
    ]


def build_aggregate_signals(
    analyzed_entries: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ],
) -> dict[str, list[dict[str, Any]]]:
    denominator = len(
        analyzed_entries
    )

    field_map = {
        "moods": (
            "mood",
        ),
        "sentiments": (
            "sentiment",
        ),
        "themes": (
            "themes",
        ),
        "emergingTopics": (
            "emergingTopics",
        ),
        "keyInsights": (
            "keyInsights",
        ),
        "challenges": (
            "challenges",
        ),
        "goals": (
            "goals",
        ),
        "growthSignals": (
            "growthSignals",
        ),
        "behaviorPatterns": (
            "behaviorPatterns",
        ),
        "peopleAndTopics": (
            "peopleAndTopics",
        ),
        "nextSteps": (
            "nextStep",
        ),
    }

    return {
        output_field: (
            ranked_analysis_values(
                analyzed_entries,
                analysis_fields,
                denominator=denominator,
                limit=MAX_RANKED_ITEMS,
            )
        )
        for (
            output_field,
            analysis_fields,
        )
        in field_map.items()
    }


def build_timeline(
    analyzed_entries: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ],
) -> list[dict[str, Any]]:
    grouped: dict[
        str,
        list[
            tuple[
                dict[str, Any],
                dict[str, Any],
            ]
        ],
    ] = defaultdict(list)

    for entry, analysis in analyzed_entries:
        created_at = (
            parse_entry_datetime(
                entry
            )
        )

        if created_at is None:
            continue

        grouped[
            created_at.strftime(
                "%Y-%m"
            )
        ].append((
            entry,
            analysis,
        ))

    periods = sorted(
        grouped
    )[
        -MAX_TIMELINE_PERIODS:
    ]

    timeline: list[
        dict[str, Any]
    ] = []

    for period in periods:
        period_entries = (
            grouped[period]
        )

        denominator = len(
            period_entries
        )

        moods = ranked_analysis_values(
            period_entries,
            ("mood",),
            denominator=denominator,
            limit=3,
        )

        sentiments = (
            ranked_analysis_values(
                period_entries,
                ("sentiment",),
                denominator=denominator,
                limit=3,
            )
        )

        timeline.append({
            "period": period,
            "analyzedEntries": (
                denominator
            ),
            "dominantMood": (
                first_ranked_value(
                    moods
                )
            ),
            "dominantSentiment": (
                first_ranked_value(
                    sentiments
                )
            ),
            "topThemes": (
                ranked_analysis_values(
                    period_entries,
                    ("themes",),
                    denominator=(
                        denominator
                    ),
                    limit=3,
                )
            ),
            "topChallenges": (
                ranked_analysis_values(
                    period_entries,
                    ("challenges",),
                    denominator=(
                        denominator
                    ),
                    limit=3,
                )
            ),
            "topGoals": (
                ranked_analysis_values(
                    period_entries,
                    ("goals",),
                    denominator=(
                        denominator
                    ),
                    limit=3,
                )
            ),
            "growthSignals": (
                ranked_analysis_values(
                    period_entries,
                    ("growthSignals",),
                    denominator=(
                        denominator
                    ),
                    limit=3,
                )
            ),
        })

    return timeline


def build_ask_context(
    entries: list[dict[str, Any]],
    *,
    question: Any,
    start_date: Any = None,
    end_date: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_question = (
        normalize_question(
            question
        )
    )

    (
        parsed_start,
        parsed_end,
    ) = resolve_scope(
        start_date=start_date,
        end_date=end_date,
        now=now,
    )

    safe_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
    ]

    scoped_entries = [
        entry
        for entry in safe_entries
        if entry_is_in_scope(
            entry,
            start_date=parsed_start,
            end_date=parsed_end,
        )
    ]

    analyzed_entries: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ] = []

    for entry in scoped_entries:
        analysis = get_entry_analysis(
            entry
        )

        if analysis:
            analyzed_entries.append((
                entry,
                analysis,
            ))

    minimum_datetime = (
        datetime.min.replace(
            tzinfo=timezone.utc
        )
    )

    analyzed_entries.sort(
        key=lambda item: (
            parse_entry_datetime(
                item[0]
            )
            or minimum_datetime
        ),
        reverse=True,
    )

    entry_datetimes = sorted(
        created_at
        for created_at in (
            parse_entry_datetime(
                entry
            )
            for entry
            in scoped_entries
        )
        if created_at is not None
    )

    total_entries = len(
        scoped_entries
    )

    analyzed_count = len(
        analyzed_entries
    )

    source_signals = (
        select_source_signals(
            analyzed_entries,
            question=(
                normalized_question
            ),
        )
    )

    if analyzed_count == 0:
        context_status = "EMPTY"
    elif (
        analyzed_count
        < total_entries
        or analyzed_count < 2
    ):
        context_status = "PARTIAL"
    else:
        context_status = "READY"

    return {
        "contextVersion": (
            ASK_CONTEXT_VERSION
        ),
        "contextStatus": (
            context_status
        ),
        "question": (
            normalized_question
        ),
        "scope": {
            "startDate": (
                parsed_start.isoformat()
                if parsed_start
                else None
            ),
            "endDate": (
                parsed_end.isoformat()
                if parsed_end
                else None
            ),
            "firstEntryAt": (
                entry_datetimes[
                    0
                ].isoformat()
                if entry_datetimes
                else None
            ),
            "latestEntryAt": (
                entry_datetimes[
                    -1
                ].isoformat()
                if entry_datetimes
                else None
            ),
        },
        "coverage": {
            "totalEntries": (
                total_entries
            ),
            "analyzedEntries": (
                analyzed_count
            ),
            "unanalyzedEntries": max(
                total_entries
                - analyzed_count,
                0,
            ),
            (
                "analysisCompletionPercent"
            ): (
                round(
                    (
                        analyzed_count
                        / total_entries
                    )
                    * 100
                )
                if total_entries
                else 0
            ),
            "sourceSignalsAvailable": (
                analyzed_count
            ),
            "sourceSignalsIncluded": (
                len(source_signals)
            ),
            "contextTruncated": (
                analyzed_count
                > len(source_signals)
            ),
        },
        "aggregateSignals": (
            build_aggregate_signals(
                analyzed_entries
            )
        ),
        "timeline": build_timeline(
            analyzed_entries
        ),
        "sourceSignals": (
            source_signals
        ),
    }
