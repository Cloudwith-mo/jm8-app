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
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")

from account_export_contract import (  # noqa: E402
    collision_safe_filename,
    is_valid_export_id,
    new_export_id,
    normalize_request_token,
    request_token_digest,
    serialize_entry,
    serialize_public_job,
    serialize_subscription,
    validate_export_object_key,
    validate_export_file_name,
    validate_source_image,
)


class AccountExportContractTests(unittest.TestCase):
    def test_tokens_are_normalized_before_sha256(self):
        token = "550E8400-E29B-41D4-A716-446655440000"
        self.assertEqual(normalize_request_token(f" {token} "), token.lower())
        self.assertEqual(request_token_digest(token), request_token_digest(token.lower()))
        with self.assertRaises(ValueError):
            normalize_request_token("short")

    def test_export_ids_are_opaque_sortable_and_validated(self):
        value = new_export_id(datetime(2026, 8, 28, 12, 13, 14, tzinfo=timezone.utc))
        self.assertRegex(value, r"^exp_20260828T121314Z_[a-z0-9]{16}$")
        self.assertTrue(is_valid_export_id(value))
        for invalid in ("", "exp_other", "../exp_20260828T121314Z_aaaaaaaaaaaaaaaa", 1):
            self.assertFalse(is_valid_export_id(invalid))

    def test_object_boundaries_reject_cross_user_and_traversal(self):
        export_id = "exp_20260828T121314Z_aaaaaaaaaaaaaaaa"
        key = f"exports/user-a/{export_id}/jm8-export.zip"
        self.assertEqual(validate_export_object_key("user-a", export_id, key), key)
        with self.assertRaises(ValueError):
            validate_export_object_key("user-b", export_id, key)
        with self.assertRaises(ValueError):
            validate_source_image("user-a", "raw", "raw", "users/user-b/uploads/x/a.jpg")
        with self.assertRaises(ValueError):
            validate_source_image("user-a", "raw", "other", "users/user-a/uploads/x/a.jpg")
        self.assertEqual(
            validate_export_file_name("jm8-export-20260828T121314Z.zip"),
            "jm8-export-20260828T121314Z.zip",
        )
        with self.assertRaises(ValueError):
            validate_export_file_name('bad"; filename="other.zip')

    def test_collision_safe_names_remove_paths(self):
        used: set[str] = set()
        self.assertEqual(collision_safe_filename("../../page one.jpg", used), "page-one.jpg")
        self.assertEqual(collision_safe_filename("page one.jpg", used), "page-one-2.jpg")

    def test_public_and_archive_serializers_are_allowlists(self):
        public = serialize_public_job({
            "PK": "USER#secret", "SK": "internal", "userId": "secret",
            "objectKey": "secret", "requestToken": "secret", "executionArn": "secret",
            "downloadUrl": "secret", "exportId": "exp_x", "status": "QUEUED",
            "error": {"code": "X", "message": "safe", "retryable": True, "cause": "secret"},
        })
        self.assertEqual(set(public), {"exportId", "status", "error"})
        self.assertEqual(set(public["error"]), {"code", "message", "retryable"})

        entry = serialize_entry({"entryId": "e1", "text": "journal", "PK": "secret", "s3RawKey": "secret"})
        self.assertEqual(entry, {"entryId": "e1", "text": "journal"})
        subscription = serialize_subscription({
            "plan": "PRO", "status": "ACTIVE", "stripeCustomerId": "cus_secret",
            "priceId": "price_secret", "idempotencyKey": "secret",
        })
        self.assertEqual(subscription, {"plan": "PRO", "status": "ACTIVE"})


if __name__ == "__main__":
    unittest.main()
