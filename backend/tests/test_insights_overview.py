import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault(
    "AWS_ACCESS_KEY_ID",
    "testing",
)

os.environ.setdefault(
    "AWS_SECRET_ACCESS_KEY",
    "testing",
)

os.environ.setdefault(
    "AWS_DEFAULT_REGION",
    "us-east-1",
)

os.environ.setdefault(
    "AWS_EC2_METADATA_DISABLED",
    "true",
)

os.environ.setdefault(
    "TABLE_NAME",
    "journalm8-test-main",
)

os.environ.setdefault(
    "RAW_BUCKET",
    "journalm8-test-raw",
)


from app import lambda_handler  # noqa: E402
from insights_overview import (  # noqa: E402
    build_insights_overview,
)


def analyzed_entry(
    *,
    entry_id: str,
    created_at: str,
    mood: str,
    sentiment: str,
    themes: list[str] | None = None,
    challenges: list[str] | None = None,
    goals: list[str] | None = None,
    growth_signals: list[str] | None = None,
    behavior_patterns: list[str] | None = None,
    reflection_prompt: str = "",
) -> dict:
    return {
        "entryId": entry_id,
        "createdAt": created_at,
        "analysisCompletedAt": (
            created_at
        ),
        "analysisStatus": "COMPLETED",
        "analysis": {
            "status": "ANALYZED",
            "mood": mood,
            "sentiment": sentiment,
            "themes": themes or [],
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
            "reflectionPrompt": (
                reflection_prompt
            ),
        },
    }


class InsightsOverviewTests(
    unittest.TestCase
):
    def test_empty_overview(self):
        overview = (
            build_insights_overview([])
        )

        self.assertEqual(
            overview["coverage"],
            {
                "totalEntries": 0,
                "analyzedEntries": 0,
                "unanalyzedEntries": 0,
                "analysisCompletionPercent": 0,
                "firstEntryAt": None,
                "latestEntryAt": None,
            },
        )

        self.assertIsNone(
            overview["dominantMood"]
        )

        self.assertEqual(
            overview["topThemes"],
            [],
        )

    def test_overview_aggregates_entries(
        self,
    ):
        entries = [
            analyzed_entry(
                entry_id="entry-one",
                created_at=(
                    "2026-01-01T10:00:00"
                    "+00:00"
                ),
                mood="determined",
                sentiment="positive",
                themes=[
                    "discipline",
                    "career",
                ],
                challenges=["Focus"],
                goals=["Launch JM8"],
                growth_signals=[
                    "Kept showing up"
                ],
                behavior_patterns=[
                    "Plans before acting"
                ],
            ),
            analyzed_entry(
                entry_id="entry-two",
                created_at=(
                    "2026-02-01T10:00:00"
                    "+00:00"
                ),
                mood="determined",
                sentiment="mixed_positive",
                themes=[
                    "discipline",
                    "learning",
                ],
                challenges=["Focus"],
                goals=["Launch JM8"],
                growth_signals=[
                    "Kept showing up"
                ],
                behavior_patterns=[
                    "Reviews progress"
                ],
            ),
            {
                "entryId": "entry-three",
                "createdAt": (
                    "2026-03-01T10:00:00"
                    "+00:00"
                ),
                "analysisStatus": (
                    "NOT_ANALYZED"
                ),
            },
        ]

        overview = (
            build_insights_overview(
                entries
            )
        )

        self.assertEqual(
            overview["coverage"][
                "totalEntries"
            ],
            3,
        )

        self.assertEqual(
            overview["coverage"][
                "analyzedEntries"
            ],
            2,
        )

        self.assertEqual(
            overview["coverage"][
                "analysisCompletionPercent"
            ],
            67,
        )

        self.assertEqual(
            overview["dominantMood"],
            {
                "value": "determined",
                "count": 2,
                "sharePercent": 100,
            },
        )

        self.assertEqual(
            overview["topThemes"][0],
            {
                "value": "discipline",
                "count": 2,
                "sharePercent": 100,
            },
        )

        self.assertEqual(
            overview[
                "topChallenges"
            ][0]["value"],
            "Focus",
        )

        self.assertEqual(
            overview[
                "notableProgress"
            ][0]["value"],
            "Kept showing up",
        )

    def test_duplicate_value_counts_once_per_entry(
        self,
    ):
        overview = (
            build_insights_overview([
                analyzed_entry(
                    entry_id="entry-one",
                    created_at=(
                        "2026-01-01T10:00:00"
                        "+00:00"
                    ),
                    mood="reflective",
                    sentiment="neutral",
                    themes=[
                        "discipline",
                        "Discipline",
                        "discipline",
                    ],
                )
            ])
        )

        self.assertEqual(
            overview["topThemes"][0][
                "count"
            ],
            1,
        )

    def test_latest_reflection_prompt_is_used(
        self,
    ):
        overview = (
            build_insights_overview([
                analyzed_entry(
                    entry_id="older",
                    created_at=(
                        "2026-01-01T10:00:00"
                        "+00:00"
                    ),
                    mood="reflective",
                    sentiment="neutral",
                    reflection_prompt=(
                        "Older prompt"
                    ),
                ),
                analyzed_entry(
                    entry_id="newer",
                    created_at=(
                        "2026-02-01T10:00:00"
                        "+00:00"
                    ),
                    mood="hopeful",
                    sentiment="positive",
                    reflection_prompt=(
                        "Newest prompt"
                    ),
                ),
            ])
        )

        self.assertEqual(
            overview[
                "reflectionPrompt"
            ],
            "Newest prompt",
        )

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_api_returns_user_scoped_overview(
        self,
        list_overview_entries,
    ):
        list_overview_entries.return_value = [
            analyzed_entry(
                entry_id="entry-one",
                created_at=(
                    "2026-01-01T10:00:00"
                    "+00:00"
                ),
                mood="calm",
                sentiment="positive",
            )
        ]

        event = {
            "requestContext": {
                "http": {
                    "method": "GET",
                    "path": (
                        "/insights/overview"
                    ),
                },
                "authorizer": {
                    "jwt": {
                        "claims": {
                            "sub": (
                                "private-user"
                            ),
                        },
                    },
                },
            },
        }

        response = lambda_handler(
            event,
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )

        list_overview_entries.assert_called_once_with(
            user_id="private-user",
        )

        self.assertIn(
            "overview",
            body,
        )

        serialized = json.dumps(body)

        self.assertNotIn(
            "private-user",
            serialized,
        )

        self.assertNotIn(
            "rawText",
            serialized,
        )

    def test_deployment_scripts_include_secured_route(
        self,
    ):
        create_api = Path(
            "bin/create-api"
        ).read_text()

        secure_api = Path(
            "bin/secure-api"
        ).read_text()

        route = (
            '"GET /insights/overview"'
        )

        self.assertIn(
            route,
            create_api,
        )

        self.assertIn(
            route,
            secure_api,
        )

    def test_storage_query_excludes_private_text(
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

        private_fields = [
            "rawText",
            "cleanText",
            "userId",
            "s3RawKey",
            "s3RawBucket",
        ]

        for field in private_fields:
            self.assertNotIn(
                field,
                section,
            )


if __name__ == "__main__":
    unittest.main()
