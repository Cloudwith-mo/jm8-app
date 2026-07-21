from collections import Counter
from typing import Any

from insights_overview import (
    clean_text,
    first_ranked_value,
    get_entry_analysis,
    get_entry_sort_value,
    ranked_analysis_values,
    utc_now,
)


THEMES_VERSION = "1.0"
MOODS_VERSION = "1.0"

WINDOW_SIZE = 5
MAX_ITEMS = 20
MAX_MONTH_ITEMS = 5
MAX_EMERGING_ITEMS = 5


AnalyzedEntry = tuple[
    dict[str, Any],
    dict[str, Any],
]


def prepare_entries(
    entries: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[AnalyzedEntry],
]:
    safe_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict)
    ]

    analyzed_entries: list[
        AnalyzedEntry
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
        )
    )

    return (
        safe_entries,
        analyzed_entries,
    )


def entry_date(
    entry: dict[str, Any],
    analysis: dict[str, Any],
) -> str:
    return clean_text(
        entry.get("createdAt")
        or get_entry_sort_value(
            entry,
            analysis,
        ),
        max_characters=80,
    )


def month_period(
    value: str,
) -> str | None:
    if (
        len(value) >= 7
        and value[:4].isdigit()
        and value[4] == "-"
        and value[5:7].isdigit()
    ):
        return value[:7]

    return None


def unique_values(
    analysis: dict[str, Any],
    field: str,
) -> list[str]:
    raw_value = analysis.get(field)

    values = (
        raw_value
        if isinstance(
            raw_value,
            list,
        )
        else [raw_value]
    )

    unique: list[str] = []
    seen: set[str] = set()

    for value in values:
        label = clean_text(value)

        if not label:
            continue

        identity = label.casefold()

        if identity in seen:
            continue

        seen.add(identity)
        unique.append(label)

    return unique


def build_coverage(
    safe_entries: list[
        dict[str, Any]
    ],
    analyzed_entries: list[
        AnalyzedEntry
    ],
) -> dict[str, Any]:
    dates = sorted(
        {
            entry_date(
                entry,
                analysis,
            )
            for entry, analysis
            in analyzed_entries
            if entry_date(
                entry,
                analysis,
            )
        }
    )

    total_entries = len(
        safe_entries
    )

    analyzed_count = len(
        analyzed_entries
    )

    return {
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
            dates[0]
            if dates
            else None
        ),
        "latestEntryAt": (
            dates[-1]
            if dates
            else None
        ),
    }


def field_counts(
    analyzed_entries: list[
        AnalyzedEntry
    ],
    field: str,
) -> tuple[
    Counter[str],
    dict[str, str],
]:
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}

    for _, analysis in analyzed_entries:
        for label in unique_values(
            analysis,
            field,
        ):
            identity = label.casefold()

            labels.setdefault(
                identity,
                label,
            )

            counts[identity] += 1

    return counts, labels


def monthly_groups(
    analyzed_entries: list[
        AnalyzedEntry
    ],
) -> dict[
    str,
    list[AnalyzedEntry],
]:
    groups: dict[
        str,
        list[AnalyzedEntry],
    ] = {}

    for entry, analysis in (
        analyzed_entries
    ):
        period = month_period(
            entry_date(
                entry,
                analysis,
            )
        )

        if not period:
            continue

        groups.setdefault(
            period,
            [],
        ).append(
            (
                entry,
                analysis,
            )
        )

    return groups


def build_theme_details(
    analyzed_entries: list[
        AnalyzedEntry
    ],
) -> list[dict[str, Any]]:
    total_count = len(
        analyzed_entries
    )

    counts, labels = field_counts(
        analyzed_entries,
        "themes",
    )

    recent_entries = (
        analyzed_entries[
            -WINDOW_SIZE:
        ]
    )

    previous_entries = (
        analyzed_entries[
            -(
                WINDOW_SIZE
                * 2
            ):
            -WINDOW_SIZE
        ]
    )

    recent_counts, _ = field_counts(
        recent_entries,
        "themes",
    )

    previous_counts, _ = field_counts(
        previous_entries,
        "themes",
    )

    first_seen: dict[
        str,
        str,
    ] = {}

    last_seen: dict[
        str,
        str,
    ] = {}

    for entry, analysis in (
        analyzed_entries
    ):
        observed_at = entry_date(
            entry,
            analysis,
        )

        for label in unique_values(
            analysis,
            "themes",
        ):
            identity = label.casefold()

            if observed_at:
                first_seen.setdefault(
                    identity,
                    observed_at,
                )

                last_seen[
                    identity
                ] = observed_at

    ranked = sorted(
        counts,
        key=lambda identity: (
            -counts[identity],
            -recent_counts[identity],
            labels[identity].casefold(),
        ),
    )

    details: list[
        dict[str, Any]
    ] = []

    for identity in ranked[
        :MAX_ITEMS
    ]:
        recent_count = (
            recent_counts[identity]
        )

        previous_count = (
            previous_counts[identity]
        )

        if (
            recent_count
            > previous_count
        ):
            trend = "RISING"
        elif (
            recent_count
            < previous_count
        ):
            trend = "COOLING"
        else:
            trend = "STEADY"

        count = counts[identity]

        details.append({
            "value": labels[identity],
            "count": count,
            "sharePercent": (
                round(
                    (
                        count
                        / total_count
                    )
                    * 100
                )
                if total_count
                else 0
            ),
            "firstSeenAt": (
                first_seen.get(
                    identity
                )
            ),
            "lastSeenAt": (
                last_seen.get(
                    identity
                )
            ),
            "recentCount": (
                recent_count
            ),
            "previousCount": (
                previous_count
            ),
            "trend": trend,
        })

    return details


def build_theme_months(
    analyzed_entries: list[
        AnalyzedEntry
    ],
) -> list[dict[str, Any]]:
    groups = monthly_groups(
        analyzed_entries
    )

    months: list[
        dict[str, Any]
    ] = []

    for period in sorted(groups):
        entries = groups[period]

        months.append({
            "period": period,
            "analyzedEntries": len(
                entries
            ),
            "topThemes": (
                ranked_analysis_values(
                    entries,
                    ("themes",),
                    denominator=len(
                        entries
                    ),
                    limit=(
                        MAX_MONTH_ITEMS
                    ),
                )
            ),
        })

    return months


def build_theme_insights(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    (
        safe_entries,
        analyzed_entries,
    ) = prepare_entries(entries)

    themes = build_theme_details(
        analyzed_entries
    )

    emerging_themes = sorted(
        [
            item
            for item in themes
            if (
                item["trend"]
                == "RISING"
                and item[
                    "recentCount"
                ] > 0
            )
        ],
        key=lambda item: (
            -(
                item["recentCount"]
                - item[
                    "previousCount"
                ]
            ),
            -item["recentCount"],
            -item["count"],
            item["value"].casefold(),
        ),
    )[
        :MAX_EMERGING_ITEMS
    ]

    return {
        "themesVersion": (
            THEMES_VERSION
        ),
        "generatedAt": utc_now(),
        "coverage": build_coverage(
            safe_entries,
            analyzed_entries,
        ),
        "windowSize": WINDOW_SIZE,
        "themes": themes,
        "emergingThemes": (
            emerging_themes
        ),
        "monthlyBreakdown": (
            build_theme_months(
                analyzed_entries
            )
        ),
    }


def build_mood_months(
    analyzed_entries: list[
        AnalyzedEntry
    ],
) -> list[dict[str, Any]]:
    groups = monthly_groups(
        analyzed_entries
    )

    months: list[
        dict[str, Any]
    ] = []

    for period in sorted(groups):
        entries = groups[period]

        moods = ranked_analysis_values(
            entries,
            ("mood",),
            denominator=len(entries),
            limit=MAX_MONTH_ITEMS,
        )

        sentiments = (
            ranked_analysis_values(
                entries,
                ("sentiment",),
                denominator=len(
                    entries
                ),
                limit=MAX_MONTH_ITEMS,
            )
        )

        months.append({
            "period": period,
            "analyzedEntries": len(
                entries
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
            "moods": moods,
            "sentiments": sentiments,
        })

    return months


def build_mood_insights(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    (
        safe_entries,
        analyzed_entries,
    ) = prepare_entries(entries)

    analyzed_count = len(
        analyzed_entries
    )

    moods = ranked_analysis_values(
        analyzed_entries,
        ("mood",),
        denominator=analyzed_count,
        limit=MAX_ITEMS,
    )

    sentiments = (
        ranked_analysis_values(
            analyzed_entries,
            ("sentiment",),
            denominator=analyzed_count,
            limit=MAX_ITEMS,
        )
    )

    recent_entries = (
        analyzed_entries[
            -WINDOW_SIZE:
        ]
    )

    previous_entries = (
        analyzed_entries[
            -(
                WINDOW_SIZE
                * 2
            ):
            -WINDOW_SIZE
        ]
    )

    recent_moods = (
        ranked_analysis_values(
            recent_entries,
            ("mood",),
            denominator=len(
                recent_entries
            ),
            limit=MAX_ITEMS,
        )
    )

    previous_moods = (
        ranked_analysis_values(
            previous_entries,
            ("mood",),
            denominator=len(
                previous_entries
            ),
            limit=MAX_ITEMS,
        )
    )

    recent_dominant = (
        first_ranked_value(
            recent_moods
        )
    )

    previous_dominant = (
        first_ranked_value(
            previous_moods
        )
    )

    recent_value = (
        recent_dominant.get(
            "value"
        )
        if recent_dominant
        else None
    )

    previous_value = (
        previous_dominant.get(
            "value"
        )
        if previous_dominant
        else None
    )

    mood_changed = bool(
        recent_value
        and previous_value
        and (
            recent_value.casefold()
            != previous_value.casefold()
        )
    )

    return {
        "moodsVersion": (
            MOODS_VERSION
        ),
        "generatedAt": utc_now(),
        "coverage": build_coverage(
            safe_entries,
            analyzed_entries,
        ),
        "windowSize": WINDOW_SIZE,
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
        "moods": moods,
        "sentiments": sentiments,
        "recentDominantMood": (
            recent_dominant
        ),
        "previousDominantMood": (
            previous_dominant
        ),
        "moodShift": {
            "changed": mood_changed,
            "from": previous_value,
            "to": recent_value,
        },
        "monthlyBreakdown": (
            build_mood_months(
                analyzed_entries
            )
        ),
    }
