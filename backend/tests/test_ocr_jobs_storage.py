import os
import unittest
from unittest.mock import patch


os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw")


from storage import (  # noqa: E402
    decode_ocr_jobs_cursor,
    encode_ocr_jobs_cursor,
    list_ocr_jobs,
    user_pk,
)


def entry(entry_id, *, source_type="image", ocr_status="FAILED"):
    return {
        "PK": user_pk("first-user"),
        "SK": f"ENTRY#2026-08-05T12:00:00+00:00#{entry_id}",
        "entryId": entry_id,
        "sourceType": source_type,
        "status": f"OCR_{ocr_status}",
        "ocrStatus": ocr_status,
        "ocrAttemptCount": 1,
    }


class OcrJobsStorageTests(unittest.TestCase):
    @patch("storage.attach_image_preview_url", side_effect=lambda item: item)
    @patch("storage.table.query")
    def test_filters_across_mixed_dynamodb_pages(self, query, _preview):
        first_last_key = {
            "PK": user_pk("first-user"),
            "SK": "ENTRY#2026-08-04T00:00:00+00:00#entry_completed",
        }
        query.side_effect = [
            {
                "Items": [
                    entry("entry_text", source_type="text"),
                    entry("entry_completed", ocr_status="COMPLETED"),
                ],
                "LastEvaluatedKey": first_last_key,
            },
            {
                "Items": [entry("entry_failed")],
            },
        ]

        page = list_ocr_jobs(
            "first-user",
            "FAILED",
            limit=10,
        )

        self.assertEqual(page["count"], 1)
        self.assertEqual(page["items"][0]["entryId"], "entry_failed")
        self.assertIsNone(page["nextCursor"])
        self.assertEqual(query.call_count, 2)
        self.assertEqual(
            query.call_args_list[1].kwargs["ExclusiveStartKey"],
            first_last_key,
        )

    @patch("storage.attach_image_preview_url", side_effect=lambda item: item)
    @patch("storage.table.query")
    def test_limit_returns_cursor_at_last_processed_item(self, query, _preview):
        first = entry("entry_first")
        second = entry("entry_second")
        query.return_value = {"Items": [first, second]}

        page = list_ocr_jobs("first-user", "ALL", limit=1)

        self.assertEqual(page["count"], 1)
        self.assertEqual(page["items"][0]["entryId"], "entry_first")
        decoded = decode_ocr_jobs_cursor(
            page["nextCursor"],
            user_id="first-user",
            status_filter="ALL",
        )
        self.assertEqual(decoded, {"PK": first["PK"], "SK": first["SK"]})

    def test_cursor_is_bound_to_user_and_filter(self):
        key = {
            "PK": user_pk("first-user"),
            "SK": "ENTRY#2026-08-05T12:00:00+00:00#entry_first",
        }
        cursor = encode_ocr_jobs_cursor(
            user_id="first-user",
            status_filter="FAILED",
            last_evaluated_key=key,
        )

        with self.assertRaises(ValueError):
            decode_ocr_jobs_cursor(
                cursor,
                user_id="second-user",
                status_filter="FAILED",
            )

        with self.assertRaises(ValueError):
            decode_ocr_jobs_cursor(
                cursor,
                user_id="first-user",
                status_filter="COMPLETED",
            )

    def test_malformed_cursor_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_ocr_jobs_cursor(
                "not-a-cursor",
                user_id="first-user",
                status_filter="ALL",
            )


if __name__ == "__main__":
    unittest.main()
