import json
import unittest
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

from insights_ask_context import (
    AskContextInputError,
    MAX_SOURCE_SIGNALS,
    build_ask_context,
)


FIXED_NOW = datetime(
    2026,
    7,
    22,
    12,
    0,
    tzinfo=timezone.utc,
)


def analyzed_entry(
    *,
    entry_id: str,
    created_at: str,
    source_type: str = "typed",
    mood: str = "reflective",
    sentiment: str = "neutral",
    secondary_moods: (
        list[str] | None
    ) = None,
    themes: list[str] | None = None,
    emerging_topics: (
        list[str] | None
    ) = None,
    key_insights: (
        list[str] | None
    ) = None,
    challenges: (
        list[str] | None
    ) = None,
    goals: list[str] | None = None,
    growth_signals: (
        list[str] | None
    ) = None,
    behavior_patterns: (
        list[str] | None
    ) = None,
    people_and_topics: (
        list[str] | None
    ) = None,
    next_step: str = "",
) -> dict:
    return {
        "entryId": entry_id,
        "createdAt": created_at,
        "sourceType": source_type,
        "analysisStatus": (
            "COMPLETED"
        ),
        "analysis": {
            "status": "ANALYZED",
            "mood": mood,
            "sentiment": sentiment,
            "secondaryMoods": (
                secondary_moods or []
            ),
            "themes": themes or [],
            "emergingTopics": (
                emerging_topics or []
            ),
            "keyInsights": (
                key_insights or []
            ),
            "challenges": (
                challenges or []
            ),
            "goals": goals or [],
            "growthSignals": (
                growth_signals or []
            ),
            "behaviorPatterns": (
                behavior_patterns or []
            ),
            "peopleAndTopics": (
                people_and_topics or []
            ),
            "nextStep": next_step,
        },
    }


class AskContextTests(
    unittest.TestCase
):
    def test_question_is_normalized(
        self,
    ):
        context = build_ask_context(
            [],
            question=(
                "  What   keeps "
                "returning?  "
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(
            context["question"],
            "What keeps returning?",
        )

    def test_short_question_rejected(
        self,
    ):
        with self.assertRaises(
            AskContextInputError
        ) as raised:
            build_ask_context(
                [],
                question="  ? ",
                now=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidQuestion",
        )

    def test_long_question_rejected(
        self,
    ):
        with self.assertRaises(
            AskContextInputError
        ) as raised:
            build_ask_context(
                [],
                question="x" * 501,
                now=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidQuestion",
        )

    def test_invalid_scope_date_rejected(
        self,
    ):
        with self.assertRaises(
            AskContextInputError
        ) as raised:
            build_ask_context(
                [],
                question=(
                    "What changed?"
                ),
                start_date=(
                    "2026/01/01"
                ),
                now=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidStartDate",
        )

    def test_future_scope_rejected(
        self,
    ):
        with self.assertRaises(
            AskContextInputError
        ) as raised:
            build_ask_context(
                [],
                question=(
                    "What changed?"
                ),
                end_date="2027-01-01",
                now=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "FutureDateRange",
        )

    def test_reversed_scope_rejected(
        self,
    ):
        with self.assertRaises(
            AskContextInputError
        ) as raised:
            build_ask_context(
                [],
                question=(
                    "What changed?"
                ),
                start_date=(
                    "2026-06-01"
                ),
                end_date="2026-05-01",
                now=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidDateRange",
        )

    def test_scope_is_inclusive(
        self,
    ):
        entries = [
            analyzed_entry(
                entry_id="start",
                created_at=(
                    "2026-01-01T01:00:00"
                    "+00:00"
                ),
            ),
            analyzed_entry(
                entry_id="end",
                created_at=(
                    "2026-01-31T23:00:00"
                    "+00:00"
                ),
            ),
            analyzed_entry(
                entry_id="outside",
                created_at=(
                    "2026-02-01T00:00:00"
                    "+00:00"
                ),
            ),
        ]

        context = build_ask_context(
            entries,
            question=(
                "What happened in January?"
            ),
            start_date="2026-01-01",
            end_date="2026-01-31",
            now=FIXED_NOW,
        )

        self.assertEqual(
            context["coverage"][
                "totalEntries"
            ],
            2,
        )

        self.assertEqual(
            context["coverage"][
                "analyzedEntries"
            ],
            2,
        )

    def test_unanalyzed_entries_are_counted(
        self,
    ):
        entries = [
            analyzed_entry(
                entry_id="analyzed",
                created_at=(
                    "2026-03-01T10:00:00"
                    "+00:00"
                ),
            ),
            {
                "entryId": "pending",
                "createdAt": (
                    "2026-03-02T10:00:00"
                    "+00:00"
                ),
                "sourceType": "image",
                "analysisStatus": (
                    "NOT_ANALYZED"
                ),
            },
        ]

        context = build_ask_context(
            entries,
            question=(
                "What patterns appeared?"
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(
            context["coverage"][
                "totalEntries"
            ],
            2,
        )

        self.assertEqual(
            context["coverage"][
                "analyzedEntries"
            ],
            1,
        )

        self.assertEqual(
            context["coverage"][
                "unanalyzedEntries"
            ],
            1,
        )

        self.assertEqual(
            context["coverage"][
                "analysisCompletionPercent"
            ],
            50,
        )

        self.assertEqual(
            context["contextStatus"],
            "PARTIAL",
        )

    def test_aggregate_values_count_once_per_entry(
        self,
    ):
        entries = [
            analyzed_entry(
                entry_id="one",
                created_at=(
                    "2026-04-01T10:00:00"
                    "+00:00"
                ),
                themes=[
                    "discipline",
                    "Discipline",
                    "discipline",
                ],
            ),
            analyzed_entry(
                entry_id="two",
                created_at=(
                    "2026-04-02T10:00:00"
                    "+00:00"
                ),
                themes=[
                    "discipline",
                ],
            ),
        ]

        context = build_ask_context(
            entries,
            question=(
                "Which themes returned?"
            ),
            now=FIXED_NOW,
        )

        top_theme = context[
            "aggregateSignals"
        ]["themes"][0]

        self.assertEqual(
            top_theme["value"],
            "discipline",
        )

        self.assertEqual(
            top_theme["count"],
            2,
        )

        self.assertEqual(
            top_theme[
                "sharePercent"
            ],
            100,
        )

    def test_timeline_is_chronological(
        self,
    ):
        entries = [
            analyzed_entry(
                entry_id="newer",
                created_at=(
                    "2026-05-01T10:00:00"
                    "+00:00"
                ),
            ),
            analyzed_entry(
                entry_id="older",
                created_at=(
                    "2026-03-01T10:00:00"
                    "+00:00"
                ),
            ),
            analyzed_entry(
                entry_id="middle",
                created_at=(
                    "2026-04-01T10:00:00"
                    "+00:00"
                ),
            ),
        ]

        context = build_ask_context(
            entries,
            question=(
                "How did I change?"
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(
            [
                item["period"]
                for item
                in context["timeline"]
            ],
            [
                "2026-03",
                "2026-04",
                "2026-05",
            ],
        )

    def test_question_relevance_prioritizes_matching_source(
        self,
    ):
        entries = [
            analyzed_entry(
                entry_id="career",
                created_at=(
                    "2026-02-01T10:00:00"
                    "+00:00"
                ),
                themes=["career"],
                challenges=[
                    (
                        "Career transition "
                        "uncertainty"
                    ),
                ],
            ),
            analyzed_entry(
                entry_id="health",
                created_at=(
                    "2026-06-01T10:00:00"
                    "+00:00"
                ),
                themes=["health"],
                challenges=[
                    (
                        "Maintaining a "
                        "sleep routine"
                    ),
                ],
            ),
        ]

        context = build_ask_context(
            entries,
            question=(
                "What career challenge "
                "kept returning?"
            ),
            now=FIXED_NOW,
        )

        first_source = context[
            "sourceSignals"
        ][0]

        self.assertIn(
            (
                "Career transition "
                "uncertainty"
            ),
            first_source[
                "challenges"
            ],
        )

        self.assertGreater(
            first_source[
                "relevanceScore"
            ],
            0,
        )

    def test_source_signals_are_capped(
        self,
    ):
        entries = [
            analyzed_entry(
                entry_id=f"entry-{day}",
                created_at=(
                    "2026-06-"
                    f"{day:02d}"
                    "T10:00:00+00:00"
                ),
                behavior_patterns=[
                    f"Pattern {day}",
                ],
            )
            for day in range(
                1,
                31,
            )
        ]

        context = build_ask_context(
            entries,
            question=(
                "What behavior patterns "
                "kept returning?"
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(
            len(
                context[
                    "sourceSignals"
                ]
            ),
            MAX_SOURCE_SIGNALS,
        )

        self.assertTrue(
            context["coverage"][
                "contextTruncated"
            ]
        )

        self.assertEqual(
            context["coverage"][
                "sourceSignalsAvailable"
            ],
            30,
        )

    def test_source_type_is_preserved(
        self,
    ):
        context = build_ask_context(
            [
                analyzed_entry(
                    entry_id="scan",
                    created_at=(
                        "2026-06-01"
                        "T10:00:00+00:00"
                    ),
                    source_type="image",
                ),
            ],
            question=(
                "What did I write about?"
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(
            context[
                "sourceSignals"
            ][0]["sourceType"],
            "image",
        )

    def test_empty_context_contract(
        self,
    ):
        context = build_ask_context(
            [],
            question=(
                "What challenge "
                "kept returning?"
            ),
            now=FIXED_NOW,
        )

        self.assertEqual(
            context["contextStatus"],
            "EMPTY",
        )

        self.assertEqual(
            context["coverage"][
                "totalEntries"
            ],
            0,
        )

        self.assertEqual(
            context["sourceSignals"],
            [],
        )

        self.assertEqual(
            context["timeline"],
            [],
        )

        for values in context[
            "aggregateSignals"
        ].values():
            self.assertEqual(
                values,
                [],
            )

    def test_output_excludes_private_fields(
        self,
    ):
        entry = analyzed_entry(
            entry_id="private-entry",
            created_at=(
                "2026-06-01T10:00:00"
                "+00:00"
            ),
            source_type="typed",
            key_insights=[
                "A safe derived insight",
            ],
        )

        entry.update({
            "userId": "private-user",
            "rawText": (
                "private raw transcript"
            ),
            "cleanText": (
                "private reviewed transcript"
            ),
            "jobId": "private-job",
            "s3RawKey": "private-key",
            "s3RawBucket": (
                "private-bucket"
            ),
            "imagePreviewUrl": (
                "private-image"
            ),
        })

        context = build_ask_context(
            [entry],
            question=(
                "What did I learn?"
            ),
            now=FIXED_NOW,
        )

        serialized = json.dumps(
            context
        )

        forbidden = [
            "entryId",
            "userId",
            "rawText",
            "cleanText",
            "jobId",
            "s3RawKey",
            "s3RawBucket",
            "imagePreviewUrl",
            "private-entry",
            "private-user",
            "private raw transcript",
            "private reviewed transcript",
            "private-job",
            "private-key",
            "private-bucket",
            "private-image",
        ]

        for value in forbidden:
            with self.subTest(
                value=value
            ):
                self.assertNotIn(
                    value,
                    serialized,
                )

    def test_context_builder_has_no_bedrock_dependency(
        self,
    ):
        source = Path(
            "function/"
            "insights_ask_context.py"
        ).read_text().casefold()

        for value in [
            "import boto3",
            "bedrock",
            "converse(",
            "invoke_model",
        ]:
            with self.subTest(
                value=value
            ):
                self.assertNotIn(
                    value,
                    source,
                )

    def test_storage_projection_includes_safe_source_type(
        self,
    ):
        storage = Path(
            "function/storage.py"
        ).read_text()

        section = storage.split(
            (
                "def "
                "list_insights_overview_entries"
            ),
            1,
        )[1].split(
            "\ndef ",
            1,
        )[0]

        self.assertIn(
            '"#sourceType"',
            section,
        )

        self.assertIn(
            '"sourceType"',
            section,
        )

        for private_field in [
            "rawText",
            "cleanText",
            "userId",
            "s3RawKey",
            "s3RawBucket",
            "imagePreviewUrl",
        ]:
            with self.subTest(
                private_field=(
                    private_field
                )
            ):
                self.assertNotIn(
                    private_field,
                    section,
                )


if __name__ == "__main__":
    unittest.main()
