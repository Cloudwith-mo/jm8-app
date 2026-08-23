import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import sys


FUNCTION_ROOT = Path(__file__).resolve().parent.parent / "function"
sys.path.insert(0, str(FUNCTION_ROOT))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw")

import app  # noqa: E402


def authenticated_event(subject):
    return {
        "requestContext": {
            "http": {"method": "GET", "path": "/usage"},
            "authorizer": {"jwt": {"claims": {"sub": subject}}},
        },
    }


def unauthenticated_event(headers=None):
    return {
        "requestContext": {
            "http": {"method": "GET", "path": "/usage"},
        },
        "headers": headers or {},
    }


class ProductionIdentityTests(unittest.TestCase):
    def test_production_accepts_only_valid_cognito_subject(self):
        with patch.dict(os.environ, {"STAGE": "prod"}):
            self.assertEqual(
                app.get_user_id(authenticated_event("  cognito-user  ")),
                "cognito-user",
            )

    def test_production_rejects_header_identity_substitution(self):
        with patch.dict(os.environ, {"STAGE": "prod"}), patch.object(
            app,
            "get_usage_snapshot",
        ) as get_usage_snapshot:
            result = app.lambda_handler(
                unauthenticated_event({"X-User-Id": "attacker"}),
                None,
            )

        self.assertEqual(result["statusCode"], 401)
        self.assertEqual(
            json.loads(result["body"]),
            {
                "error": "Unauthorized",
                "message": "Authentication is required.",
            },
        )
        self.assertNotIn("attacker", result["body"])
        self.assertNotIn("demo-user", result["body"])
        get_usage_snapshot.assert_not_called()

    def test_production_rejects_missing_or_malformed_subject(self):
        for subject in (None, "", " ", True, False, 0, 1, {}, []):
            with self.subTest(subject=subject), patch.dict(
                os.environ,
                {"STAGE": "prod"},
            ):
                result = app.lambda_handler(authenticated_event(subject), None)
                self.assertEqual(result["statusCode"], 401)
                self.assertEqual(json.loads(result["body"])["error"], "Unauthorized")

        malformed_claims_event = authenticated_event("ignored")
        malformed_claims_event["requestContext"]["authorizer"]["jwt"][
            "claims"
        ] = []
        with patch.dict(os.environ, {"STAGE": "prod"}):
            result = app.lambda_handler(malformed_claims_event, None)
        self.assertEqual(result["statusCode"], 401)

    def test_local_development_identity_behavior_is_retained(self):
        with patch.dict(os.environ, {"STAGE": "dev"}):
            self.assertEqual(
                app.get_user_id(
                    unauthenticated_event({"x-user-id": "local-user"})
                ),
                "local-user",
            )
            self.assertEqual(
                app.get_user_id(unauthenticated_event()),
                "demo-user",
            )


if __name__ == "__main__":
    unittest.main()
