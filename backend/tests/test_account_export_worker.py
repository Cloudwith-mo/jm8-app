import io
import json
import os
import sys
import unittest
import zipfile
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

import account_export_worker as worker  # noqa: E402
from account_deletion_guard import (  # noqa: E402
    AccountDeletionInProgress,
    DeletionGuardUnavailable,
)


EXPORT_ID = "exp_20260828T121314Z_aaaaaaaaaaaaaaaa"


class AccountExportWorkerTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch.object(worker, "ensure_user_mutation_allowed")
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def _job(self):
        return {
            "exportId": EXPORT_ID, "userId": "user-a", "status": "QUEUED",
            "accountProfile": {"subject": "user-a", "email": "private@example.com"},
        }

    def test_capacity_contract_fits_ephemeral_storage_and_single_put(self):
        self.assertEqual(worker.MAX_SOURCE_BYTES, 2 * 1024**3)
        self.assertEqual(worker.MAX_ARCHIVE_BYTES, 3 * 1024**3)
        self.assertEqual(worker.S3_SINGLE_PUT_OBJECT_LIMIT_BYTES, 5_000_000_000)
        self.assertLess(
            worker.MAX_SOURCE_BYTES + worker.MAX_ARCHIVE_BYTES,
            worker.CONFIGURED_EPHEMERAL_STORAGE_BYTES,
        )
        self.assertLess(
            worker.MAX_ARCHIVE_BYTES,
            worker.S3_SINGLE_PUT_OBJECT_LIMIT_BYTES,
        )

    @patch.object(worker, "release_active_lock")
    @patch.object(worker, "update_export_status")
    @patch.object(worker, "_query_user_records")
    @patch.object(worker, "get_export_job")
    @patch.object(worker.s3, "put_object")
    @patch.object(worker.s3, "get_object")
    def test_zip_has_explicit_contents_and_no_internal_fields(
        self, get_object, put_object, get_job, query, update, release
    ):
        get_job.return_value = self._job()
        query.return_value = [{
            "PK": "USER#user-a", "SK": "ENTRY#secret", "GSI1PK": "secret",
            "entityType": "ENTRY", "entryId": "entry-1", "sourceType": "image",
            "rawText": "journal text", "cleanText": "corrected text",
            "s3RawBucket": worker.RAW_BUCKET,
            "s3RawKey": "users/user-a/uploads/entry-1/page.jpg",
            "originalFileName": "../page.jpg",
        }, {
            "PK": "USER#user-a", "SK": "ASK_HISTORY#secret", "entityType": "ASK_HISTORY",
            "createdAt": "2026-01-01T00:00:00Z",
            "answer": {"question": "question", "answer": "answer", "status": "COMPLETED"},
        }, {
            "PK": "USER#user-a", "SK": "USAGE#2026-01", "entityType": "MONTHLY_USAGE",
            "period": "2026-01", "askCount": 2, "reservationId": "excluded",
        }, {
            "PK": "USER#user-a", "SK": "ACCOUNT_EXPORT_ACTIVE", "entityType": "ACCOUNT_EXPORT_ACTIVE",
        }, {
            "PK": "USER#user-a", "SK": "ENTRY#token#GEN#digest#CHUNK#00000000#chunk_id",
            "entityType": "SEMANTIC_CHUNK", "text": "derived chunk must not export",
        }, {
            "PK": "USER#user-a",
            "SK": "PRODUCT_MILESTONE#ActivatedUser",
            "entityType": "PRODUCT_MILESTONE",
            "metricName": "ActivatedUser",
            "metricValue": 1,
        }, {
            "PK": "DELETED_SUBJECT#digest", "SK": "SEMANTIC_MEMORY_GUARD",
            "entityType": "SEMANTIC_MEMORY_DELETION_GUARD",
        }]
        get_object.return_value = {"ContentLength": 5, "Body": io.BytesIO(b"image")}
        update.side_effect = lambda user_id, export_id, status, **values: {"status": status, **values}
        captured: dict[str, bytes] = {}
        put_object.side_effect = lambda **kwargs: captured.update(zip=kwargs["Body"].read())

        result = worker.run_export("user-a", EXPORT_ID)
        self.assertEqual(result["status"], "COMPLETED")
        release.assert_called_once_with("user-a", EXPORT_ID)
        with zipfile.ZipFile(io.BytesIO(captured["zip"])) as archive:
            names = set(archive.namelist())
            self.assertTrue({
                "manifest.json", "profile.json", "entries.json", "ask-history.json",
                "usage.json", "entitlement.json", "subscription.json", "images/page.jpg",
            }.issubset(names))
            entries = json.loads(archive.read("entries.json"))
            all_json = "\n".join(archive.read(name).decode("utf-8") for name in names if name.endswith(".json"))
            self.assertEqual(entries[0]["text"], "journal text")
            self.assertEqual(entries[0]["imageArchivePath"], "images/page.jpg")
            for forbidden in (
                "GSI1PK", "ACCOUNT_EXPORT_ACTIVE", "reservationId", "s3RawKey",
                "stripeCustomerId", "derived chunk must not export",
                "SEMANTIC_CHUNK", "SEMANTIC_MEMORY_DELETION_GUARD",
                "PRODUCT_MILESTONE", "ActivatedUser",
            ):
                self.assertNotIn(forbidden, all_json)

    def test_export_source_documents_semantic_memory_exclusion_contract(self):
        source = Path(worker.__file__).read_text(encoding="utf-8")
        self.assertIn("derived retrieval index", source)
        self.assertIn("reconstructed from the", source)
        self.assertIn("Deletion guards are operational", source)
        self.assertIn("neither is included in account exports", source)

    @patch.object(worker.s3, "get_object")
    def test_missing_image_becomes_warning(self, get_object):
        get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "GetObject",
        )
        with __import__("tempfile").TemporaryDirectory() as temporary:
            count, size, warnings = worker._download_images(
                "user-a", [({
                    "entryId": "e1", "sourceType": "image", "s3RawBucket": worker.RAW_BUCKET,
                    "s3RawKey": "users/user-a/uploads/e1/page.jpg",
                }, {})], __import__("pathlib").Path(temporary)
            )
        self.assertEqual((count, size), (0, 0))
        self.assertEqual(warnings[0]["code"], "MissingImage")

    @patch.object(worker.s3, "get_object")
    def test_access_denied_fails_instead_of_omitting(self, get_object):
        get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
            "GetObject",
        )
        with __import__("tempfile").TemporaryDirectory() as temporary:
            with self.assertRaises(worker.ExportSecurityError):
                worker._download_images(
                    "user-a", [({
                        "entryId": "e1", "sourceType": "image", "s3RawBucket": worker.RAW_BUCKET,
                        "s3RawKey": "users/user-a/uploads/e1/page.jpg",
                    }, {})], __import__("pathlib").Path(temporary)
                )

    def test_image_object_limit_fails_before_download(self):
        with __import__("tempfile").TemporaryDirectory() as temporary, patch.object(
            worker, "MAX_IMAGE_OBJECTS", 0
        ), patch.object(worker.s3, "get_object") as get_object:
            with self.assertRaises(worker.ExportTooLarge):
                worker._download_images(
                    "user-a", [({
                        "sourceType": "image", "s3RawBucket": worker.RAW_BUCKET,
                        "s3RawKey": "users/user-a/uploads/e1/page.jpg",
                    }, {})], __import__("pathlib").Path(temporary)
                )
            get_object.assert_not_called()

    def test_oversized_source_is_non_retryable_export_too_large(self):
        with __import__("tempfile").TemporaryDirectory() as temporary, patch.object(
            worker, "MAX_SOURCE_BYTES", 4
        ), patch.object(worker.s3, "get_object") as get_object:
            get_object.return_value = {"ContentLength": 5, "Body": io.BytesIO(b"image")}
            with self.assertRaises(worker.ExportTooLarge) as raised:
                worker._download_images(
                    "user-a", [({
                        "sourceType": "image", "s3RawBucket": worker.RAW_BUCKET,
                        "s3RawKey": "users/user-a/uploads/e1/page.jpg",
                    }, {})], __import__("pathlib").Path(temporary)
                )

        with patch.object(worker, "fail_export") as fail:
            worker.record_failure(
                "user-a",
                EXPORT_ID,
                f"{type(raised.exception).__module__}.{type(raised.exception).__name__}",
            )
        fail.assert_called_once_with(
            "user-a", EXPORT_ID, "ExportTooLarge",
            "The account data is too large for one export.", False,
        )

    def test_oversized_archive_is_non_retryable_export_too_large(self):
        with __import__("tempfile").TemporaryDirectory() as temporary, patch.object(
            worker, "MAX_ARCHIVE_BYTES", 1
        ):
            root = __import__("pathlib").Path(temporary)
            (root / "images").mkdir()
            (root / "profile.json").write_text("{}")
            with self.assertRaises(worker.ExportTooLarge) as raised:
                worker._build_zip(root, root / "export.zip")

        with patch.object(worker, "fail_export") as fail:
            worker.record_failure(
                "user-a",
                EXPORT_ID,
                f"{type(raised.exception).__module__}.{type(raised.exception).__name__}",
            )
        fail.assert_called_once_with(
            "user-a", EXPORT_ID, "ExportTooLarge",
            "The account data is too large for one export.", False,
        )

    @patch.object(worker.table, "query")
    def test_record_limit_fails_before_resource_exhaustion(self, query):
        query.return_value = {"Items": [{"entityType": "ENTRY"}] * (worker.MAX_USER_RECORDS + 1)}
        with self.assertRaises(worker.ExportTooLarge):
            worker._query_user_records("user-a")

    @patch.object(worker, "fail_export")
    def test_terminal_failure_is_sanitized_and_releases_via_store(self, fail):
        worker.record_failure("user-a", EXPORT_ID, "Some.Internal.Exception")
        fail.assert_called_once_with(
            "user-a", EXPORT_ID, "ExportFailed", "The export could not be completed.", True
        )

    def test_final_guard_blocks_archive_and_status_writes_after_assembly(self):
        for guard_error in (
            AccountDeletionInProgress(),
            DeletionGuardUnavailable(),
        ):
            with (
                self.subTest(error=type(guard_error).__name__),
                patch.object(worker, "get_export_job", return_value=self._job()),
                patch.object(worker, "_query_user_records", return_value=[]) as query,
                patch.object(worker, "_build_zip", wraps=worker._build_zip) as build_zip,
                patch.object(worker.s3, "put_object") as put_object,
                patch.object(worker, "update_export_status") as update,
                patch.object(worker, "release_active_lock") as release,
                patch.object(
                    worker,
                    "ensure_user_mutation_allowed",
                    side_effect=guard_error,
                ),
                self.assertRaises(type(guard_error)),
            ):
                update.side_effect = (
                    lambda _user_id, _export_id, status, **values:
                    {"status": status, **values}
                )
                worker.run_export("user-a", EXPORT_ID)

            query.assert_called_once()
            build_zip.assert_called()
            put_object.assert_not_called()
            self.assertEqual(
                [call.args[2] for call in update.call_args_list],
                ["RUNNING"],
            )
            release.assert_not_called()

    def test_failure_writer_also_fails_closed_after_deletion_starts(self):
        for guard_error in (
            AccountDeletionInProgress(),
            DeletionGuardUnavailable(),
        ):
            with (
                self.subTest(error=type(guard_error).__name__),
                patch.object(
                    worker,
                    "ensure_user_mutation_allowed",
                    side_effect=guard_error,
                ),
                patch.object(worker, "fail_export") as fail_export,
            ):
                if isinstance(guard_error, AccountDeletionInProgress):
                    result = worker.record_failure("user-a", EXPORT_ID)
                    self.assertEqual(result["status"], "ABORTED")
                else:
                    with self.assertRaises(DeletionGuardUnavailable):
                        worker.record_failure("user-a", EXPORT_ID)
                fail_export.assert_not_called()

    def test_guard_blocks_completion_if_deletion_starts_after_archive_upload(self):
        for guard_error in (
            AccountDeletionInProgress(),
            DeletionGuardUnavailable(),
        ):
            with (
                self.subTest(error=type(guard_error).__name__),
                patch.object(worker, "get_export_job", return_value=self._job()),
                patch.object(worker, "_query_user_records", return_value=[]),
                patch.object(worker.s3, "put_object") as put_object,
                patch.object(worker, "update_export_status") as update,
                patch.object(worker, "release_active_lock") as release,
                patch.object(worker, "fail_export") as fail_export,
                patch.object(
                    worker,
                    "ensure_user_mutation_allowed",
                    side_effect=[None, guard_error],
                ),
                self.assertRaises(type(guard_error)),
            ):
                update.side_effect = (
                    lambda _user_id, _export_id, status, **values:
                    {"status": status, **values}
                )
                worker.run_export("user-a", EXPORT_ID)

            put_object.assert_called_once()
            self.assertEqual(
                [call.args[2] for call in update.call_args_list],
                ["RUNNING"],
            )
            release.assert_not_called()
            fail_export.assert_not_called()


if __name__ == "__main__":
    unittest.main()
