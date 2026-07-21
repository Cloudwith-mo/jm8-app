import json
import unittest
from datetime import (
    datetime,
    timezone,
)


from insights_reports import (
    ReportPeriodError,
    build_monthly_report,
    build_weekly_report,
)


NOW = datetime(
    2026,
    7,
    21,
    16,
    0,
    tzinfo=timezone.utc,
)


def analyzed_entry(
    *,
    number: int,
    created_at: str,
    mood: str = "confident",
    sentiment: str = "positive",
    themes: list[str] | None = None,
    challenges: list[str] | None = None,
    growth_signals: list[str] | None = None,
    goals: list[str] | None = None,
    behavior_patterns: list[str] | None = None,
    reflection_prompt: str = "",
) -> dict:
    return {
        "entryId": (
            f"entry-{number}"
        ),
        "createdAt": created_at,
        "analysisStatus": "COMPLETED",
        "analysis": {
            "status": "ANALYZED",
            "mood": mood,
            "sentiment": sentiment,
            "themes": themes or [],
            "challenges": (
                challenges or []
            ),
            "growthSignals": (
                growth_signals or []
            ),
            "goals": goals or [],
            "behaviorPatterns": (
                behavior_patterns
                or []
            ),
            "reflectionPrompt": (
                reflection_prompt
            ),
        },
    }


class InsightsReportTests(
    unittest.TestCase
):
    def test_current_week_metadata(
        self,
    ):
        report = build_weekly_report(
            [],
            now=NOW,
        )

        period = report["period"]

        self.assertEqual(
            period["key"],
            "2026-W30",
        )

        self.assertEqual(
            period["startDate"],
            "2026-07-20",
        )

        self.assertEqual(
            period["endDate"],
            "2026-07-26",
        )

        self.assertEqual(
            period["previousPeriod"],
            "2026-W29",
        )

        self.assertIsNone(
            period["nextPeriod"]
        )

        self.assertTrue(
            period["isCurrentPeriod"]
        )

    def test_previous_week_navigation(
        self,
    ):
        report = build_weekly_report(
            [],
            period="2026-W29",
            now=NOW,
        )

        period = report["period"]

        self.assertEqual(
            period["previousPeriod"],
            "2026-W28",
        )

        self.assertEqual(
            period["nextPeriod"],
            "2026-W30",
        )

        self.assertFalse(
            period["isCurrentPeriod"]
        )

    def test_current_month_metadata(
        self,
    ):
        report = build_monthly_report(
            [],
            now=NOW,
        )

        period = report["period"]

        self.assertEqual(
            period["key"],
            "2026-07",
        )

        self.assertEqual(
            period["startDate"],
            "2026-07-01",
        )

        self.assertEqual(
            period["endDate"],
            "2026-07-31",
        )

        self.assertEqual(
            period["previousPeriod"],
            "2026-06",
        )

        self.assertIsNone(
            period["nextPeriod"]
        )

    def test_invalid_week_rejected(
        self,
    ):
        with self.assertRaises(
            ReportPeriodError
        ):
            build_weekly_report(
                [],
                period="2026-W99",
                now=NOW,
            )

    def test_invalid_month_rejected(
        self,
    ):
        with self.assertRaises(
            ReportPeriodError
        ):
            build_monthly_report(
                [],
                period="2026-13",
                now=NOW,
            )

    def test_future_period_rejected(
        self,
    ):
        with self.subTest(
            report="weekly"
        ):
            with self.assertRaises(
                ReportPeriodError
            ):
                build_weekly_report(
                    [],
                    period="2026-W31",
                    now=NOW,
                )

        with self.subTest(
            report="monthly"
        ):
            with self.assertRaises(
                ReportPeriodError
            ):
                build_monthly_report(
                    [],
                    period="2026-08",
                    now=NOW,
                )

    def test_entries_filtered_to_period(
        self,
    ):
        entries = [
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-07-19T23:59:59"
                    "+00:00"
                ),
            ),
            analyzed_entry(
                number=2,
                created_at=(
                    "2026-07-20T00:00:00"
                    "+00:00"
                ),
            ),
            analyzed_entry(
                number=3,
                created_at=(
                    "2026-07-27T00:00:00"
                    "+00:00"
                ),
            ),
        ]

        report = build_weekly_report(
            entries,
            now=NOW,
        )

        self.assertEqual(
            report["coverage"][
                "totalEntries"
            ],
            1,
        )

        self.assertEqual(
            report["coverage"][
                "analyzedEntries"
            ],
            1,
        )

    def test_report_aggregates_highlights(
        self,
    ):
        entries = [
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-07-20T10:00:00"
                    "+00:00"
                ),
                themes=["Discipline"],
                challenges=[
                    "Consistency"
                ],
                growth_signals=[
                    "Improved focus"
                ],
                goals=[
                    "Build discipline"
                ],
                behavior_patterns=[
                    "Plans each morning"
                ],
            ),
            analyzed_entry(
                number=2,
                created_at=(
                    "2026-07-21T10:00:00"
                    "+00:00"
                ),
                themes=["Discipline"],
                challenges=[
                    "Consistency"
                ],
                growth_signals=[
                    "Improved focus"
                ],
                goals=[
                    "Build discipline"
                ],
                behavior_patterns=[
                    "Plans each morning"
                ],
            ),
        ]

        report = build_weekly_report(
            entries,
            now=NOW,
        )

        highlights = report[
            "highlights"
        ]

        self.assertEqual(
            highlights[
                "dominantMood"
            ]["value"],
            "confident",
        )

        self.assertEqual(
            highlights[
                "topRecurringTheme"
            ]["value"],
            "Discipline",
        )

        self.assertEqual(
            highlights[
                "biggestChallenge"
            ]["value"],
            "Consistency",
        )

        self.assertEqual(
            highlights[
                "notableProgress"
            ]["value"],
            "Improved focus",
        )

        self.assertEqual(
            highlights[
                "repeatedConcern"
            ]["count"],
            2,
        )

        self.assertEqual(
            report["status"],
            "READY",
        )

    def test_repeated_concern_requires_repetition(
        self,
    ):
        entries = [
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-07-20T10:00:00"
                    "+00:00"
                ),
                challenges=[
                    "Time management"
                ],
            ),
            analyzed_entry(
                number=2,
                created_at=(
                    "2026-07-21T10:00:00"
                    "+00:00"
                ),
                challenges=[
                    "Nutrition"
                ],
            ),
        ]

        report = build_weekly_report(
            entries,
            now=NOW,
        )

        self.assertIsNone(
            report["highlights"][
                "repeatedConcern"
            ]
        )

    def test_latest_reflection_prompt_used(
        self,
    ):
        entries = [
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-07-20T10:00:00"
                    "+00:00"
                ),
                reflection_prompt=(
                    "Older prompt"
                ),
            ),
            analyzed_entry(
                number=2,
                created_at=(
                    "2026-07-21T10:00:00"
                    "+00:00"
                ),
                reflection_prompt=(
                    "Latest prompt"
                ),
            ),
        ]

        report = build_weekly_report(
            entries,
            now=NOW,
        )

        self.assertEqual(
            report[
                "reflectionPrompt"
            ],
            "Latest prompt",
        )

    def test_unanalyzed_entries_are_counted(
        self,
    ):
        entries = [
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-07-20T10:00:00"
                    "+00:00"
                ),
            ),
            {
                "createdAt": (
                    "2026-07-21T11:00:00"
                    "+00:00"
                ),
                "analysisStatus": (
                    "PENDING"
                ),
            },
        ]

        report = build_weekly_report(
            entries,
            now=NOW,
        )

        coverage = report[
            "coverage"
        ]

        self.assertEqual(
            coverage["totalEntries"],
            2,
        )

        self.assertEqual(
            coverage[
                "analyzedEntries"
            ],
            1,
        )

        self.assertEqual(
            coverage[
                "unanalyzedEntries"
            ],
            1,
        )

        self.assertEqual(
            coverage[
                "analysisCompletionPercent"
            ],
            50,
        )

        self.assertEqual(
            report["status"],
            "PARTIAL",
        )

    def test_empty_report_contract(
        self,
    ):
        report = build_monthly_report(
            [],
            now=NOW,
        )

        self.assertEqual(
            report["status"],
            "EMPTY",
        )

        self.assertEqual(
            report["topThemes"],
            [],
        )

        self.assertIsNone(
            report["highlights"][
                "dominantMood"
            ]
        )

        self.assertIsInstance(
            report[
                "reflectionPrompt"
            ],
            str,
        )

    def test_output_excludes_private_fields(
        self,
    ):
        entry = analyzed_entry(
            number=1,
            created_at=(
                "2026-07-20T10:00:00"
                "+00:00"
            ),
            themes=["Discipline"],
        )

        entry.update({
            "rawText": "private",
            "cleanText": "private",
            "userId": "private-user",
            "jobId": "private-job",
            "s3RawKey": "private-key",
            "s3RawBucket": (
                "private-bucket"
            ),
        })

        serialized = json.dumps(
            build_weekly_report(
                [entry],
                now=NOW,
            )
        )

        for field in [
            "rawText",
            "cleanText",
            "userId",
            "entryId",
            "jobId",
            "s3RawKey",
            "s3RawBucket",
            "imagePreviewUrl",
        ]:
            self.assertNotIn(
                field,
                serialized,
            )


if __name__ == "__main__":
    unittest.main()
