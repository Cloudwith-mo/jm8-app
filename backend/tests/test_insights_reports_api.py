import json
import os
import unittest
from datetime import (
    datetime,
    timezone,
)
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


def api_event(
    path: str,
    *,
    period: str | None = None,
) -> dict:
    event = {
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

    if period is not None:
        event[
            "queryStringParameters"
        ] = {
            "period": period,
        }

    return event


class InsightsReportApiTests(
    unittest.TestCase
):
    @patch(
        "app.build_weekly_report"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_weekly_api_is_user_scoped(
        self,
        list_entries,
        build_report,
    ):
        entries = [
            {
                "createdAt": (
                    "2026-07-21T10:00:00"
                    "+00:00"
                ),
            },
        ]

        list_entries.return_value = (
            entries
        )

        build_report.return_value = {
            "reportType": "WEEKLY",
        }

        response = lambda_handler(
            api_event(
                "/reports/weekly",
                period="2026-W30",
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

        build_report.assert_called_once_with(
            entries,
            period="2026-W30",
        )

        self.assertEqual(
            body["report"][
                "reportType"
            ],
            "WEEKLY",
        )

    @patch(
        "app.build_monthly_report"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_monthly_api_is_user_scoped(
        self,
        list_entries,
        build_report,
    ):
        entries = [
            {
                "createdAt": (
                    "2026-07-21T10:00:00"
                    "+00:00"
                ),
            },
        ]

        list_entries.return_value = (
            entries
        )

        build_report.return_value = {
            "reportType": "MONTHLY",
        }

        response = lambda_handler(
            api_event(
                "/reports/monthly",
                period="2026-07",
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

        build_report.assert_called_once_with(
            entries,
            period="2026-07",
        )

        self.assertEqual(
            body["report"][
                "reportType"
            ],
            "MONTHLY",
        )

    @patch(
        "app.build_weekly_report"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_default_period_is_forwarded(
        self,
        list_entries,
        build_report,
    ):
        list_entries.return_value = []

        build_report.return_value = {
            "reportType": "WEEKLY",
        }

        response = lambda_handler(
            api_event(
                "/reports/weekly"
            ),
            None,
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )

        build_report.assert_called_once_with(
            [],
            period=None,
        )

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_invalid_week_period_returns_400(
        self,
        list_entries,
    ):
        list_entries.return_value = []

        response = lambda_handler(
            api_event(
                "/reports/weekly",
                period="not-a-week",
            ),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "InvalidReportPeriod",
        )

        self.assertEqual(
            body["reportType"],
            "WEEKLY",
        )

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_invalid_month_period_returns_400(
        self,
        list_entries,
    ):
        list_entries.return_value = []

        response = lambda_handler(
            api_event(
                "/reports/monthly",
                period="2026-99",
            ),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "InvalidReportPeriod",
        )

        self.assertEqual(
            body["reportType"],
            "MONTHLY",
        )

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_output_excludes_private_fields(
        self,
        list_entries,
    ):
        now = datetime.now(
            timezone.utc
        ).isoformat()

        list_entries.return_value = [
            {
                "entryId": (
                    "private-entry"
                ),
                "userId": (
                    "private-user"
                ),
                "rawText": "private",
                "cleanText": "private",
                "jobId": (
                    "private-job"
                ),
                "s3RawKey": (
                    "private-key"
                ),
                "createdAt": now,
                "analysisStatus": (
                    "COMPLETED"
                ),
                "analysis": {
                    "status": (
                        "ANALYZED"
                    ),
                    "mood": "calm",
                    "sentiment": (
                        "positive"
                    ),
                    "themes": [
                        "Discipline",
                    ],
                },
            },
        ]

        response = lambda_handler(
            api_event(
                "/reports/weekly"
            ),
            None,
        )

        serialized = response["body"]

        self.assertEqual(
            response["statusCode"],
            200,
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
            "private-user",
            "private-entry",
        ]:
            self.assertNotIn(
                field,
                serialized,
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
            '"GET /reports/weekly"',
            '"GET /reports/monthly"',
        ]:
            self.assertIn(
                route,
                create_api,
            )

            self.assertIn(
                route,
                secure_api,
            )


if __name__ == "__main__":
    unittest.main()
