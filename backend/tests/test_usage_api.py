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
from usage_read import (  # noqa: E402
    UsageReadUnavailableError,
)


def api_event():
    return {
        "requestContext": {
            "http": {
                "method": "GET",
                "path": "/usage",
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


def usage_snapshot():
    return {
        "usageVersion": "1.0",
        "generatedAt": (
            "2026-07-22"
            "T12:00:00+00:00"
        ),
        "period": {
            "key": "2026-07",
            "startsAt": (
                "2026-07-01"
                "T00:00:00+00:00"
            ),
            "endsAt": (
                "2026-08-01"
                "T00:00:00+00:00"
            ),
            "resetsAt": (
                "2026-08-01"
                "T00:00:00+00:00"
            ),
        },
        "plan": {
            "id": "FREE",
            "label": "Free",
        },
        "operations": {
            "askJm8": {
                "used": 2,
                "reserved": 1,
                "failed": 3,
                "limit": 5,
                "remaining": 2,
                "allowed": True,
            },
            "entryAnalysis": {
                "used": 4,
                "reserved": 0,
                "failed": 2,
                "limit": 10,
                "remaining": 6,
                "allowed": True,
            },
        },
    }


class UsageApiTests(
    unittest.TestCase
):
    @patch(
        "app.get_usage_snapshot"
    )
    def test_usage_is_scoped_to_jwt_user(
        self,
        get_snapshot,
    ):
        get_snapshot.return_value = (
            usage_snapshot()
        )

        response = lambda_handler(
            api_event(),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )

        get_snapshot.assert_called_once_with(
            "private-user"
        )

        self.assertEqual(
            body["usage"]["plan"]["id"],
            "FREE",
        )

        self.assertEqual(
            body["usage"][
                "operations"
            ]["askJm8"]["remaining"],
            2,
        )

    @patch(
        "app.get_usage_snapshot"
    )
    def test_usage_failure_returns_safe_503(
        self,
        get_snapshot,
    ):
        get_snapshot.side_effect = (
            UsageReadUnavailableError(
                retryable=True
            )
        )

        response = lambda_handler(
            api_event(),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            503,
        )

        self.assertEqual(
            body["error"],
            "UsageTrackingUnavailable",
        )

        self.assertTrue(
            body["retryable"]
        )

        self.assertNotIn(
            "private-user",
            response["body"],
        )

    @patch(
        "app.get_usage_snapshot"
    )
    def test_public_response_has_no_private_fields(
        self,
        get_snapshot,
    ):
        get_snapshot.return_value = (
            usage_snapshot()
        )

        response = lambda_handler(
            api_event(),
            None,
        )

        for forbidden in (
            '"PK"',
            '"SK"',
            "userId",
            "reservationId",
            "askJm8Consumed",
            "entryAnalysisConsumed",
            "rawText",
            "cleanText",
            "private-user",
        ):
            self.assertNotIn(
                forbidden,
                response["body"],
            )

    def test_deployment_scripts_include_usage_route(
        self,
    ):
        create_api = Path(
            "bin/create-api"
        ).read_text()

        secure_api = Path(
            "bin/secure-api"
        ).read_text()

        route = (
            '"GET /usage"'
        )

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
