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
from insights_trends import (  # noqa: E402
    build_mood_insights,
    build_theme_insights,
)


def analyzed_entry(
    *,
    number: int,
    created_at: str,
    mood: str = "reflective",
    sentiment: str = "neutral",
    themes: list[str] | None = None,
) -> dict:
    return {
        "entryId": (
            f"entry-{number}"
        ),
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
        },
    }


def api_event(
    path: str,
) -> dict:
    return {
        "requestContext": {
            "http": {
                "method": "GET",
                "path": path,
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


class InsightsTrendTests(
    unittest.TestCase
):
    def test_empty_theme_insights(
        self,
    ):
        result = build_theme_insights(
            []
        )

        self.assertEqual(
            result["themes"],
            [],
        )

        self.assertEqual(
            result["emergingThemes"],
            [],
        )

        self.assertEqual(
            result[
                "monthlyBreakdown"
            ],
            [],
        )

    def test_theme_rankings_and_trends(
        self,
    ):
        entries = [
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-01-01T10:00:00"
                    "+00:00"
                ),
                themes=["Discipline"],
            ),
            analyzed_entry(
                number=2,
                created_at=(
                    "2026-01-02T10:00:00"
                    "+00:00"
                ),
                themes=["Discipline"],
            ),
            analyzed_entry(
                number=3,
                created_at=(
                    "2026-02-01T10:00:00"
                    "+00:00"
                ),
                themes=["Discipline"],
            ),
            analyzed_entry(
                number=4,
                created_at=(
                    "2026-02-02T10:00:00"
                    "+00:00"
                ),
                themes=["Resilience"],
            ),
            analyzed_entry(
                number=5,
                created_at=(
                    "2026-02-03T10:00:00"
                    "+00:00"
                ),
                themes=["Resilience"],
            ),
            analyzed_entry(
                number=6,
                created_at=(
                    "2026-02-04T10:00:00"
                    "+00:00"
                ),
                themes=["Resilience"],
            ),
            analyzed_entry(
                number=7,
                created_at=(
                    "2026-02-05T10:00:00"
                    "+00:00"
                ),
                themes=["Resilience"],
            ),
        ]

        result = build_theme_insights(
            entries
        )

        by_value = {
            item["value"]: item
            for item in result["themes"]
        }

        self.assertEqual(
            by_value[
                "Resilience"
            ]["trend"],
            "RISING",
        )

        self.assertEqual(
            by_value[
                "Discipline"
            ]["trend"],
            "COOLING",
        )

        self.assertEqual(
            result[
                "emergingThemes"
            ][0]["value"],
            "Resilience",
        )

    def test_duplicate_theme_counts_once(
        self,
    ):
        result = build_theme_insights([
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-01-01T10:00:00"
                    "+00:00"
                ),
                themes=[
                    "Discipline",
                    "discipline",
                    "DISCIPLINE",
                ],
            )
        ])

        self.assertEqual(
            result["themes"][0][
                "count"
            ],
            1,
        )

    def test_theme_monthly_breakdown(
        self,
    ):
        result = build_theme_insights([
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-01-01T10:00:00"
                    "+00:00"
                ),
                themes=["Career"],
            ),
            analyzed_entry(
                number=2,
                created_at=(
                    "2026-02-01T10:00:00"
                    "+00:00"
                ),
                themes=["Fitness"],
            ),
        ])

        self.assertEqual(
            [
                month["period"]
                for month in result[
                    "monthlyBreakdown"
                ]
            ],
            [
                "2026-01",
                "2026-02",
            ],
        )

    def test_empty_mood_insights(
        self,
    ):
        result = build_mood_insights(
            []
        )

        self.assertIsNone(
            result["dominantMood"]
        )

        self.assertIsNone(
            result[
                "recentDominantMood"
            ]
        )

        self.assertFalse(
            result[
                "moodShift"
            ]["changed"]
        )

    def test_mood_distribution_and_shift(
        self,
    ):
        entries = []

        for number in range(
            1,
            6,
        ):
            entries.append(
                analyzed_entry(
                    number=number,
                    created_at=(
                        "2026-01-"
                        f"{number:02d}"
                        "T10:00:00+00:00"
                    ),
                    mood="anxious",
                    sentiment="negative",
                )
            )

        for number in range(
            6,
            11,
        ):
            entries.append(
                analyzed_entry(
                    number=number,
                    created_at=(
                        "2026-02-"
                        f"{number:02d}"
                        "T10:00:00+00:00"
                    ),
                    mood="confident",
                    sentiment="positive",
                )
            )

        result = build_mood_insights(
            entries
        )

        self.assertEqual(
            result[
                "previousDominantMood"
            ]["value"],
            "anxious",
        )

        self.assertEqual(
            result[
                "recentDominantMood"
            ]["value"],
            "confident",
        )

        self.assertTrue(
            result[
                "moodShift"
            ]["changed"]
        )

        self.assertEqual(
            len(
                result[
                    "monthlyBreakdown"
                ]
            ),
            2,
        )

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_theme_api_is_user_scoped(
        self,
        list_entries,
    ):
        list_entries.return_value = [
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-01-01T10:00:00"
                    "+00:00"
                ),
                themes=["Discipline"],
            )
        ]

        response = lambda_handler(
            api_event(
                "/insights/themes"
            ),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )

        list_entries.assert_called_once_with(
            user_id="private-user",
        )

        self.assertIn(
            "themes",
            body,
        )

        self.assertNotIn(
            "private-user",
            json.dumps(body),
        )

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_mood_api_is_user_scoped(
        self,
        list_entries,
    ):
        list_entries.return_value = [
            analyzed_entry(
                number=1,
                created_at=(
                    "2026-01-01T10:00:00"
                    "+00:00"
                ),
                mood="calm",
                sentiment="positive",
            )
        ]

        response = lambda_handler(
            api_event(
                "/insights/moods"
            ),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )

        list_entries.assert_called_once_with(
            user_id="private-user",
        )

        self.assertIn(
            "moods",
            body,
        )

        self.assertNotIn(
            "private-user",
            json.dumps(body),
        )

    def test_deployment_scripts_include_routes(
        self,
    ):
        create_api = Path(
            "bin/create-api"
        ).read_text()

        secure_api = Path(
            "bin/secure-api"
        ).read_text()

        for route in [
            '"GET /insights/themes"',
            '"GET /insights/moods"',
        ]:
            self.assertIn(
                route,
                create_api,
            )

            self.assertIn(
                route,
                secure_api,
            )

    def test_outputs_exclude_private_fields(
        self,
    ):
        entry = analyzed_entry(
            number=1,
            created_at=(
                "2026-01-01T10:00:00"
                "+00:00"
            ),
            themes=["Discipline"],
        )

        entry.update({
            "rawText": "private",
            "cleanText": "private",
            "userId": "private-user",
            "s3RawKey": "private-key",
        })

        serialized = json.dumps({
            "themes": (
                build_theme_insights(
                    [entry]
                )
            ),
            "moods": (
                build_mood_insights(
                    [entry]
                )
            ),
        })

        for field in [
            "rawText",
            "cleanText",
            "userId",
            "entryId",
            "s3RawKey",
            "s3RawBucket",
        ]:
            self.assertNotIn(
                field,
                serialized,
            )


if __name__ == "__main__":
    unittest.main()
