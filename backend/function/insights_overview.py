from collections import Counter
from datetime import datetime, timezone
from typing import Any


OVERVIEW_VERSION = "1.0"
RECENT_ENTRY_LIMIT = 5
MAX_RANKED_ITEMS = 5


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def clean_text(
    value: Any,
    *,
    max_characters: int = 240,
) -> str:
    cleaned = " ".join(
        str(value or "").split()
    )

    return cleaned[
        :max_characters
    ].rstrip()


def get_entry_analysis(
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    analysis = entry.get("analysis")

    if (
        not isinstance(analysis, dict)
        or not analysis
    ):
        return None

    status = str(
        entry.get("analysisStatus")
        or analysis.get("status")
        or ""
    ).strip().upper()

    if status not in {
        "ANALYZED",
        "COMPLETED",
    }:
        return None

    return analysis


def get_entry_sort_value(
    entry: dict[str, Any],
    analysis: dict[str, Any],
) -> str:
    return clean_text(
        entry.get(
            "analysisCompletedAt"
        )
        or analysis.get("analyzedAt")
        or entry.get("createdAt")
        or "",
        max_characters=80,
    )


def ranked_analysis_values(
    analyzed_entries: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ],
    fields: tuple[str, ...],
    *,
    denominator: int,
    limit: int = MAX_RANKED_ITEMS,
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}

    for _, analysis in analyzed_entries:
        seen_for_entry: set[str] = set()

        for field in fields:
            raw_value = analysis.get(field)

            if isinstance(raw_value, list):
                values = raw_value
            else:
                values = [raw_value]

            for value in values:
                label = clean_text(value)

                if not label:
                    continue

                identity = label.casefold()

                if identity in seen_for_entry:
                    continue

                seen_for_entry.add(identity)
                labels.setdefault(
                    identity,
                    label,
                )
                counts[identity] += 1

    ranked = sorted(
        counts.items(),
        key=lambda item: (
            -item[1],
            labels[item[0]].casefold(),
        ),
    )

    safe_denominator = max(
        denominator,
        0,
    )

    return [
        {
            "value": labels[identity],
            "count": count,
            "sharePercent": (
                round(
                    (
                        count
                        / safe_denominator
                    )
                    * 100
                )
                if safe_denominator
                else 0
            ),
        }
        for identity, count
        in ranked[:limit]
    ]


def first_ranked_value(
    values: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not values:
        return None

    return values[0]


def get_reflection_prompt(
    analyzed_entries: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ],
) -> str:
    for _, analysis in analyzed_entries:
        prompt = clean_text(
            analysis.get(
                "reflectionPrompt"
            ),
            max_characters=500,
        )

        if prompt:
            return prompt

    if analyzed_entries:
        return (
            "What recurring pattern in your "
            "journal deserves more attention?"
        )

    return (
        "What would you like your journal "
        "to help you understand?"
    )


def build_insights_overview(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    safe_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
    ]

    analyzed_entries: list[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ] = []

    for entry in safe_entries:
        analysis = get_entry_analysis(
            entry
        )

        if analysis:
            analyzed_entries.append(
                (
                    entry,
                    analysis,
                )
            )

    analyzed_entries.sort(
        key=lambda item: (
            get_entry_sort_value(
                item[0],
                item[1],
            )
        ),
        reverse=True,
    )

    total_entries = len(
        safe_entries
    )

    analyzed_count = len(
        analyzed_entries
    )

    entry_dates = sorted(
        {
            clean_text(
                entry.get("createdAt"),
                max_characters=80,
            )
            for entry in safe_entries
            if clean_text(
                entry.get("createdAt"),
                max_characters=80,
            )
        }
    )

    moods = ranked_analysis_values(
        analyzed_entries,
        ("mood",),
        denominator=analyzed_count,
    )

    sentiments = (
        ranked_analysis_values(
            analyzed_entries,
            ("sentiment",),
            denominator=analyzed_count,
        )
    )

    themes = ranked_analysis_values(
        analyzed_entries,
        ("themes",),
        denominator=analyzed_count,
    )

    challenges = (
        ranked_analysis_values(
            analyzed_entries,
            ("challenges",),
            denominator=analyzed_count,
        )
    )

    goals = ranked_analysis_values(
        analyzed_entries,
        ("goals",),
        denominator=analyzed_count,
    )

    progress = ranked_analysis_values(
        analyzed_entries,
        ("growthSignals",),
        denominator=analyzed_count,
    )

    behavior_patterns = (
        ranked_analysis_values(
            analyzed_entries,
            ("behaviorPatterns",),
            denominator=analyzed_count,
        )
    )

    recent_entries = analyzed_entries[
        :RECENT_ENTRY_LIMIT
    ]

    recent_mindset_signals = (
        ranked_analysis_values(
            recent_entries,
            (
                "growthSignals",
                "behaviorPatterns",
            ),
            denominator=len(
                recent_entries
            ),
        )
    )

    return {
        "overviewVersion": (
            OVERVIEW_VERSION
        ),
        "generatedAt": utc_now(),
        "coverage": {
            "totalEntries": total_entries,
            "analyzedEntries": (
                analyzed_count
            ),
            "unanalyzedEntries": max(
                total_entries
                - analyzed_count,
                0,
            ),
            "analysisCompletionPercent": (
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
            "firstEntryAt": (
                entry_dates[0]
                if entry_dates
                else None
            ),
            "latestEntryAt": (
                entry_dates[-1]
                if entry_dates
                else None
            ),
        },
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
        "topThemes": themes,
        "topChallenges": challenges,
        "topGoals": goals,
        "notableProgress": progress,
        "behaviorPatterns": (
            behavior_patterns
        ),
        "recentMindsetSignals": (
            recent_mindset_signals
        ),
        "recentAnalyzedEntries": len(
            recent_entries
        ),
        "reflectionPrompt": (
            get_reflection_prompt(
                analyzed_entries
            )
        ),
    }
