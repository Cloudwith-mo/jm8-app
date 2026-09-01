import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "function"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import account_deletion_contract as contract  # noqa: E402


class AccountDeletionContractTests(unittest.TestCase):
    def test_request_ids_are_opaque_and_strictly_validated(self):
        request_id = contract.new_deletion_request_id()
        self.assertRegex(request_id, r"^del_[a-f0-9]{32}$")
        self.assertTrue(contract.is_valid_deletion_request_id(request_id))
        for invalid in (
            "",
            "del_short",
            "del_20260830T120000Z_aaaaaaaaaaaaaaaa",
            "../del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            1,
        ):
            self.assertFalse(contract.is_valid_deletion_request_id(invalid))

    def test_timestamps_tokens_subjects_and_confirmation_are_normalized(self):
        instant = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(contract.isoformat_utc(instant), "2026-08-30T12:00:00Z")
        token = "550E8400-E29B-41D4-A716-446655440000"
        self.assertEqual(
            contract.request_token_digest(token),
            contract.request_token_digest(token.lower()),
        )
        self.assertEqual(
            contract.subject_digest("subject-a"),
            contract.subject_digest(" subject-a "),
        )
        self.assertNotIn("subject-a", contract.subject_digest("subject-a"))
        contract.validate_confirmation(contract.CONFIRMATION_VALUE)
        with self.assertRaises(ValueError):
            contract.validate_confirmation("yes")
        with self.assertRaises(ValueError):
            contract.request_token_digest("short")
        self.assertEqual(
            contract.safe_failure_code("secret AWS message: denied"),
            "DeletionFailed",
        )

    def test_public_response_is_json_safe_strict_and_discloses_retention(self):
        item = {
            "PK": "ACCOUNT_DELETION#del_secret",
            "SK": "REQUEST",
            "requestId": "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "subjectDigest": "secret-subject-digest",
            "status": "FAILED",
            "requestedAt": "2026-08-30T12:00:00Z",
            "destructiveStartedAt": "2026-08-30T12:00:30Z",
            "failedAt": "2026-08-30T12:01:00Z",
            "failureCode": "DeletionWorkflowStartFailed",
            "retryable": True,
            "workflowExecutionArn": "arn:aws:states:secret",
            "accountExportTtlEpoch": 1_999_999_999,
            "email": "secret@example.com",
            "requestToken": "secret-request-token",
            "stripeCustomerId": "cus_secret",
            "claims": {"email": "secret@example.com"},
            "journalText": "secret journal content",
        }

        public = contract.serialize_public_deletion_request(item)

        self.assertEqual(
            set(public),
            {
                "requestId",
                "status",
                "requestedAt",
                "destructiveStartedAt",
                "failedAt",
                "failure",
                "residualRetention",
            },
        )
        self.assertEqual(public["failure"], {
            "code": "DeletionWorkflowStartFailed",
            "retryable": True,
        })
        retention = public["residualRetention"]
        self.assertIn("30 days", retention["cloudWatchLogs"])
        self.assertIn("30 days", retention["s3RetainedVersions"])
        self.assertIn("35 days", retention["dynamodbPointInTimeRecovery"])
        self.assertIn("legal", retention["stripeFinancialRecords"])
        serialized = json.dumps(public)
        for secret in (
            "secret-subject-digest",
            "secret@example.com",
            "secret-request-token",
            "cus_secret",
            "secret journal content",
            "arn:aws:states:secret",
            "ACCOUNT_DELETION#del_secret",
        ):
            self.assertNotIn(secret, serialized)

    def test_public_projection_rejects_malformed_values_and_malicious_extras(self):
        public = contract.serialize_public_deletion_request({
            "requestId": "../../raw-cognito-subject",
            "status": "IN_PROGRESS",
            "requestedAt": {"email": "user@example.com"},
            "destructiveStartedAt": "not-a-timestamp",
            "PK": "USER#raw-cognito-subject",
            "stripeCustomerId": "cus_secret",
            "requestTokenDigest": "private-token-digest",
            "journalContent": "private journal text",
        })
        self.assertEqual(public, {
            "status": "IN_PROGRESS",
            "residualRetention": contract.RESIDUAL_RETENTION,
        })
        serialized = json.dumps(public)
        for sensitive in (
            "raw-cognito-subject",
            "user@example.com",
            "cus_secret",
            "private-token-digest",
            "private journal text",
        ):
            self.assertNotIn(sensitive, serialized)


if __name__ == "__main__":
    unittest.main()
