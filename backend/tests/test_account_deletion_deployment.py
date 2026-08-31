import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "function"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")

import app  # noqa: E402


REQUEST_ID = "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def event(method, path, claims=None, body=None):
    return {
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {"jwt": {"claims": claims or {}}},
        },
        "headers": {"x-user-id": "dev-fallback-must-not-authorize"},
        "body": json.dumps(body or {}),
    }


class AccountDeletionDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create_api = (ROOT / "bin" / "create-api").read_text()
        cls.secure_api = (ROOT / "bin" / "secure-api").read_text()
        cls.contract = (
            ROOT.parent / "docs" / "JM8_USER_DATA_CONTRACT.md"
        ).read_text()

    def test_routes_are_declared_once_and_require_jwt_authorization(self):
        for route in (
            "POST /account/deletion-requests",
            "GET /account/deletion-requests/{requestId}",
        ):
            self.assertEqual(self.create_api.count(
                f'create_route_if_missing "{route}"'
            ), 1)
            self.assertEqual(self.secure_api.count(
                f'secure_route "{route}"'
            ), 1)
            self.assertNotIn(f'public_route "{route}"', self.secure_api)

    @patch.object(app, "create_account_deletion_request")
    def test_post_route_requires_verified_claims_and_uses_cognito_subject(self, create):
        unauthorized = app.lambda_handler(event(
            "POST",
            "/account/deletion-requests",
            body={
                "confirmation": "DELETE_MY_ACCOUNT",
                "requestToken": "550e8400-e29b-41d4-a716-446655440000",
            },
        ), None)
        self.assertEqual(unauthorized["statusCode"], 401)
        create.assert_not_called()

        create.return_value = (202, {
            "deletionRequest": {"requestId": REQUEST_ID, "status": "REQUESTED"},
            "replayed": False,
        })
        claims = {
            "sub": "verified-subject",
            "auth_time": str(int(time.time())),
            "email": "private@example.com",
        }
        body = {
            "confirmation": "DELETE_MY_ACCOUNT",
            "requestToken": "550e8400-e29b-41d4-a716-446655440000",
        }
        accepted = app.lambda_handler(event(
            "POST",
            "/account/deletion-requests",
            claims=claims,
            body=body,
        ), None)
        self.assertEqual(accepted["statusCode"], 202)
        create.assert_called_once_with("verified-subject", claims, body)

    @patch.object(app, "get_account_deletion_request")
    def test_get_status_uses_same_verified_subject(self, get_request):
        get_request.return_value = (200, {
            "deletionRequest": {"requestId": REQUEST_ID, "status": "REQUESTED"},
        })
        result = app.lambda_handler(event(
            "GET",
            f"/account/deletion-requests/{REQUEST_ID}",
            claims={"sub": "verified-subject"},
        ), None)
        self.assertEqual(result["statusCode"], 200)
        get_request.assert_called_once_with("verified-subject", REQUEST_ID)

    def test_production_post_is_unavailable_until_workflow_is_configured(self):
        result = app.lambda_handler(event(
            "POST",
            "/account/deletion-requests",
            claims={
                "sub": "verified-subject",
                "auth_time": str(int(time.time())),
            },
            body={
                "confirmation": "DELETE_MY_ACCOUNT",
                "requestToken": "550e8400-e29b-41d4-a716-446655440000",
            },
        ), None)
        self.assertEqual(result["statusCode"], 503)
        payload = json.loads(result["body"])
        self.assertEqual(payload["error"], "AccountDeletionUnavailable")
        self.assertTrue(payload["retryable"])

    def test_phase_3c3a_contains_no_destructive_service_operations(self):
        source = "\n".join(
            (ROOT / "function" / name).read_text()
            for name in (
                "account_deletion_contract.py",
                "account_deletion_store.py",
                "account_deletion_api.py",
                "account_deletion_guard.py",
            )
        ).lower()
        for forbidden in (
            'boto3.client("s3")',
            'boto3.client("cognito-idp")',
            "import stripe",
            "delete_user",
            "admin_delete_user",
            "batch_write_item",
            "delete_item(",
            "delete_object",
            "delete_objects",
            "subscription.delete",
        ):
            self.assertNotIn(forbidden, source)

    def test_documented_contract_preserves_order_and_retention(self):
        self.assertIn("Phase 3C3 account deletion coordination", self.contract)
        self.assertIn("Destructive account deletion execution is not", self.contract)
        self.assertIn("Phase 3C3B/3C3C", self.contract)
        for required in (
            "ACCOUNT_DELETION#<del_request_id>",
            "ACCOUNT_DELETION_SUBJECT#<subject_digest>",
            "CloudWatch",
            "up to 30 days",
            "DynamoDB PITR",
            "up to 35 days",
            "Stripe",
        ):
            self.assertIn(required, self.contract)
        order = (
            "Verify the requester",
            "Block new writes",
            "Cancel future Stripe subscription renewal",
            "Delete every current and noncurrent S3 object version",
            "Delete every item in the `USER#<user_id>` DynamoDB partition",
            "Delete the corresponding `STRIPE_CUSTOMER#<mode>#<customer_id>`",
            "Delete the Cognito identity last",
        )
        positions = [self.contract.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
