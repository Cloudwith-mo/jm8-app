import re
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import Any

from insights_overview import (
    clean_text,
    first_ranked_value,
    get_entry_analysis,
    get_reflection_prompt,
    ranked_analysis_values,
)


REPORT_VERSION = "1.0"
MAX_REPORT_ITEMS = 5

WEEK_PERIOD_PATTERN = re.compile(
    r"^\d{4}-W\d{2}$"
)

MONTH_PERIOD_PATTERN = re.compile(
    r"^\d{4}-\d{2}$"
)


class ReportPeriodError(
    ValueError
):
    pass


def normalize_now(
    now: datetime | None = None,
) -> datetime:
    current = (
        now
        if isinstance(now, datetime)
        else datetime.now(
            timezone.utc
        )
    )

    if current.tzinfo is None:
        return current.replace(
            tzinfo=timezone.utc
        )

    return current.astimezone(
        timezone.utc
    )


def parse_entry_datetime(
    value: Any,
) -> datetime | None:
    cleaned = clean_text(
        value,
        max_characters=80,
    )

    if not cleaned:
        return None

    try:
        parsed = datetime.fromisoformat(
            cleaned.replace(
                "Z",
                "+00:00",
            )
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


def week_key(
    value: datetime,
) -> str:
    calendar = value.isocalendar()

    return (
        f"{calendar.year:04d}"
        f"-W{calendar.week:02d}"
    )


def month_key(
    value: datetime,
) -> str:
    return (
        f"{value.year:04d}"
        f"-{value.month:02d}"
    )


def shift_month(
    value: datetime,
    offset: int,
) -> datetime:
    month_index = (
        value.year * 12
        + value.month
        - 1
        + offset
    )

    year, zero_based_month = divmod(
        month_index,
        12,
    )

    return datetime(
        year,
        zero_based_month + 1,
        1,
        tzinfo=timezone.utc,
    )


def resolve_week_period(
    period: str | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    current_iso = now.isocalendar()

    current_start = (
        datetime.fromisocalendar(
            current_iso.year,
            current_iso.week,
            1,
        ).replace(
            tzinfo=timezone.utc
        )
    )

    requested = clean_text(
        period,
        max_characters=20,
    ).upper()

    if not requested:
        requested = week_key(
            current_start
        )

    if not WEEK_PERIOD_PATTERN.fullmatch(
        requested
    ):
        raise ReportPeriodError(
            "Weekly report period must "
            "use YYYY-Www format."
        )

    year = int(
        requested[:4]
    )

    week = int(
        requested[-2:]
    )

    try:
        start = (
            datetime.fromisocalendar(
                year,
                week,
                1,
            ).replace(
                tzinfo=timezone.utc
            )
        )
    except ValueError as exc:
        raise ReportPeriodError(
            "Weekly report period "
            "is invalid."
        ) from exc

    if start > current_start:
        raise ReportPeriodError(
            "Future report periods "
            "are not available."
        )

    end_exclusive = (
        start
        + timedelta(days=7)
    )

    is_current = (
        start == current_start
    )

    return {
        "key": requested,
        "start": start,
        "endExclusive": end_exclusive,
        "startDate": (
            start.date().isoformat()
        ),
        "endDate": (
            (
                end_exclusive
                - timedelta(days=1)
            )
            .date()
            .isoformat()
        ),
        "previousPeriod": week_key(
            start
            - timedelta(days=7)
        ),
        "nextPeriod": (
            None
            if is_current
            else week_key(
                end_exclusive
            )
        ),
        "isCurrentPeriod": (
            is_current
        ),
    }


def resolve_month_period(
    period: str | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    current_start = datetime(
        now.year,
        now.month,
        1,
        tzinfo=timezone.utc,
    )

    requested = clean_text(
        period,
        max_characters=20,
    )

    if not requested:
        requested = month_key(
            current_start
        )

    if not MONTH_PERIOD_PATTERN.fullmatch(
        requested
    ):
        raise ReportPeriodError(
            "Monthly report period must "
            "use YYYY-MM format."
        )

    year = int(
        requested[:4]
    )

    month = int(
        requested[-2:]
    )

    try:
        start = datetime(
            year,
            month,
            1,
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise ReportPeriodError(
            "Monthly report period "
            "is invalid."
        ) from exc

    if start > current_start:
        raise ReportPeriodError(
            "Future report periods "
            "are not available."
        )

    end_exclusive = shift_month(
        start,
        1,
    )

    is_current = (
        start == current_start
    )

    return {
        "key": requested,
        "start": start,
        "endExclusive": end_exclusive,
        "startDate": (
            start.date().isoformat()
        ),
        "endDate": (
            (
                end_exclusive
                - timedelta(days=1)
            )
            .date()
            .isoformat()
        ),
        "previousPeriod": month_key(
            shift_month(
                start,
                -1,
            )
        ),
        "nextPeriod": (
            None
            if is_current
            else month_key(
                end_exclusive
            )
        ),
        "isCurrentPeriod": (
            is_current
        ),
    }


def filter_period_entries(
    entries: list[dict[str, Any]],
    *,
    start: datetime,
    end_exclusive: datetime,
) -> list[dict[str, Any]]:
    period_entries: list[
        dict[str, Any]
    ] = []

    for entry in entries:
        if not isinstance(
            entry,
            dict,
        ):
            continue

        created_at = (
            parse_entry_datetime(
                entry.get("createdAt")
            )
        )

        if not created_at:
            continue

        if (
            start
            <= created_at
            < end_exclusive
        ):
            period_entries.append(
                entry
            )

    period_entries.sort(
        key=lambda entry: (
            parse_entry_datetime(
                entry.get("createdAt")
            )
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True,
    )

    return period_entries


def prepare_analyzed_entries(
    entries: list[dict[str, Any]],
) -> list[
    tuple[
        dict[str, Any],
        dict[str, Any],
    ]
]:
    analyzed_entries = []

    for entry in entries:
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

    return analyzed_entries


def first_recurring_value(
    values: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for value in values:
        if int(
            value.get("count")
            or 0
        ) >= 2:
            return value

    return None


def build_report(
    entries: list[dict[str, Any]],
    *,
    report_type: str,
    period: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = normalize_now(
        now
    )

    normalized_type = str(
        report_type or ""
    ).strip().upper()

    if normalized_type == "WEEKLY":
        resolved = resolve_week_period(
            period,
            now=current,
        )
    elif normalized_type == "MONTHLY":
        resolved = resolve_month_period(
            period,
            now=current,
        )
    else:
        raise ValueError(
            "Report type must be "
            "WEEKLY or MONTHLY."
        )

    period_entries = (
        filter_period_entries(
            entries,
            start=resolved["start"],
            end_exclusive=(
                resolved[
                    "endExclusive"
                ]
            ),
        )
    )

    analyzed_entries = (
        prepare_analyzed_entries(
            period_entries
        )
    )

    total_entries = len(
        period_entries
    )

    analyzed_count = len(
        analyzed_entries
    )

    moods = ranked_analysis_values(
        analyzed_entries,
        ("mood",),
        denominator=analyzed_count,
        limit=MAX_REPORT_ITEMS,
    )

    sentiments = (
        ranked_analysis_values(
            analyzed_entries,
            ("sentiment",),
            denominator=analyzed_count,
            limit=MAX_REPORT_ITEMS,
        )
    )

    themes = ranked_analysis_values(
        analyzed_entries,
        ("themes",),
        denominator=analyzed_count,
        limit=MAX_REPORT_ITEMS,
    )

    challenges = (
        ranked_analysis_values(
            analyzed_entries,
            ("challenges",),
            denominator=analyzed_count,
            limit=MAX_REPORT_ITEMS,
        )
    )

    progress = ranked_analysis_values(
        analyzed_entries,
        ("growthSignals",),
        denominator=analyzed_count,
        limit=MAX_REPORT_ITEMS,
    )

    goals = ranked_analysis_values(
        analyzed_entries,
        ("goals",),
        denominator=analyzed_count,
        limit=MAX_REPORT_ITEMS,
    )

    behavior_patterns = (
        ranked_analysis_values(
            analyzed_entries,
            ("behaviorPatterns",),
            denominator=analyzed_count,
            limit=MAX_REPORT_ITEMS,
        )
    )

    created_dates = [
        parse_entry_datetime(
            entry.get("createdAt")
        )
        for entry in period_entries
    ]

    valid_dates = sorted(
        date
        for date in created_dates
        if date is not None
    )

    if analyzed_count == 0:
        report_status = "EMPTY"
    elif (
        analyzed_count < total_entries
        or analyzed_count < 2
    ):
        report_status = "PARTIAL"
    else:
        report_status = "READY"

    public_period = {
        key: value
        for key, value in resolved.items()
        if key not in {
            "start",
            "endExclusive",
        }
    }

    return {
        "reportVersion": (
            REPORT_VERSION
        ),
        "reportType": (
            normalized_type
        ),
        "generatedAt": (
            current.isoformat()
        ),
        "status": report_status,
        "period": public_period,
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
                valid_dates[0].isoformat()
                if valid_dates
                else None
            ),
            "latestEntryAt": (
                valid_dates[-1].isoformat()
                if valid_dates
                else None
            ),
        },
        "highlights": {
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
            "topRecurringTheme": (
                first_recurring_value(
                    themes
                )
            ),
            "biggestChallenge": (
                first_ranked_value(
                    challenges
                )
            ),
            "notableProgress": (
                first_ranked_value(
                    progress
                )
            ),
            "repeatedConcern": (
                first_recurring_value(
                    challenges
                )
            ),
        },
        "topThemes": themes,
        "topChallenges": challenges,
        "progressSignals": progress,
        "goalsMentioned": goals,
        "behaviorPatterns": (
            behavior_patterns
        ),
        "reflectionPrompt": (
            get_reflection_prompt(
                analyzed_entries
            )
        ),
    }


def build_weekly_report(
    entries: list[dict[str, Any]],
    *,
    period: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return build_report(
        entries,
        report_type="WEEKLY",
        period=period,
        now=now,
    )


def build_monthly_report(
    entries: list[dict[str, Any]],
    *,
    period: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    return build_report(
        entries,
        report_type="MONTHLY",
        period=period,
        now=now,
    )
