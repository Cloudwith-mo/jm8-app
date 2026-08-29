import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "function"))


os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")
os.environ.setdefault("EXPORT_BUCKET", "journalm8-test-exports-000000000000")
os.environ.setdefault("ACCOUNT_EXPORT_WORKFLOW_ARN", "arn:aws:states:us-east-1:000000000000:stateMachine:test")

import account_export_api as api  # noqa: E402
import app  # noqa: E402


EXPORT_ID = "exp_20260828T121314Z_aaaaaaaaaaaaaaaa"


def event(method="GET", path="/account/exports", claims=None):
    return {
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {"jwt": {"claims": claims or {}}},
        },
        "headers": {"x-user-id": "dev-fallback-must-not-work"},
        "body": json.dumps({"requestToken": "550e8400-e29b-41d4-a716-446655440000"}),
    }


class AccountExportApiTests(unittest.TestCase):
    def test_profile_includes_only_explicitly_verified_email(self):
        for verified in (True, "true"):
            with self.subTest(verified=verified):
                profile = api._profile_from_claims({
                    "sub": "jwt-user",
                    "email": "private@example.com",
                    "email_verified": verified,
                })
                self.assertEqual(profile["email"], "private@example.com")

        for unverified in (False, "false", "True", 1, None):
            with self.subTest(unverified=unverified):
                profile = api._profile_from_claims({
                    "sub": "jwt-user",
                    "email": "private@example.com",
                    "email_verified": unverified,
                })
                self.assertNotIn("email", profile)

    @patch.object(app, "list_account_exports")
    def test_export_routes_require_verified_cognito_claims(self, list_exports):
        result = app.lambda_handler(event(), None)
        self.assertEqual(result["statusCode"], 401)
        list_exports.assert_not_called()

    def test_post_rejects_non_object_json(self):
        request = event("POST", claims={"sub": "jwt-user", "email": "user@example.com"})
        request["body"] = "[]"
        result = app.lambda_handler(request, None)
        self.assertEqual(result["statusCode"], 400)
        self.assertEqual(json.loads(result["body"])["error"], "InvalidRequest")

    @patch.object(app, "list_account_exports")
    def test_authenticated_route_uses_jwt_subject(self, list_exports):
        list_exports.return_value = (200, {"exports": []})
        result = app.lambda_handler(event(claims={"sub": "jwt-user"}), None)
        self.assertEqual(result["statusCode"], 200)
        list_exports.assert_called_once_with("jwt-user")

    @patch.object(api.step_functions, "start_execution")
    @patch.object(api, "create_or_replay_export")
    def test_post_starts_exact_server_configured_workflow(self, create, start):
        create.return_value = ({"exportId": EXPORT_ID, "status": "QUEUED", "createdAt": "now"}, False)
        status, payload = api.create_account_export(
            "jwt-user", {
                "sub": "jwt-user", "email": "private@example.com",
                "email_verified": True,
            },
            {"requestToken": "550e8400-e29b-41d4-a716-446655440000", "userId": "attacker"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["export"]["exportId"], EXPORT_ID)
        call = start.call_args.kwargs
        self.assertEqual(call["stateMachineArn"], api.WORKFLOW_ARN)
        self.assertEqual(json.loads(call["input"])["userId"], "jwt-user")
        self.assertNotIn("email", call["input"])

    @patch.object(api, "get_export_job")
    def test_malformed_and_nonowned_ids_are_indistinguishable(self, get_job):
        with self.assertRaises(api.AccountExportApiError) as malformed:
            api.get_account_export("user-a", "../bad")
        self.assertEqual(malformed.exception.status_code, 404)
        get_job.assert_not_called()
        get_job.return_value = None
        with self.assertRaises(api.AccountExportApiError) as missing:
            api.get_account_export("user-a", EXPORT_ID)
        self.assertEqual(missing.exception.status_code, 404)

    @patch.object(api.s3, "generate_presigned_url")
    @patch.object(api.s3, "head_object")
    @patch.object(api, "get_export_job")
    def test_owner_get_receives_only_fifteen_minute_url(self, get_job, head, presign):
        get_job.return_value = {
            "exportId": EXPORT_ID, "status": "COMPLETED", "createdAt": "now",
            "expiresAt": "2999-01-01T00:00:00Z", "fileName": "jm8-export-20260828T121314Z.zip",
            "bucket": api.EXPORT_BUCKET,
            "objectKey": f"exports/user-a/{EXPORT_ID}/jm8-export.zip",
        }
        presign.return_value = "https://signed.example"
        _, payload = api.get_account_export("user-a", EXPORT_ID)
        self.assertEqual(payload["export"]["downloadUrl"], "https://signed.example")
        self.assertEqual(presign.call_args.kwargs["ExpiresIn"], 900)
        self.assertNotIn("objectKey", payload["export"])

    @patch.object(api, "mark_export_expired")
    @patch.object(api.s3, "head_object")
    @patch.object(api, "get_export_job")
    def test_missing_package_transitions_to_expired(self, get_job, head, mark):
        get_job.return_value = {
            "exportId": EXPORT_ID, "status": "COMPLETED", "expiresAt": "2999-01-01T00:00:00Z",
            "fileName": "jm8-export-20260828T121314Z.zip",
            "bucket": api.EXPORT_BUCKET,
            "objectKey": f"exports/user-a/{EXPORT_ID}/jm8-export.zip",
        }
        head.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "HeadObject",
        )
        mark.return_value = {"exportId": EXPORT_ID, "status": "EXPIRED"}
        _, payload = api.get_account_export("user-a", EXPORT_ID)
        self.assertEqual(payload["export"]["status"], "EXPIRED")
        self.assertNotIn("downloadUrl", payload["export"])


if __name__ == "__main__":
    unittest.main()
