import json
import os
import unittest
from unittest.mock import patch


os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw")


from app import lambda_handler  # noqa: E402


def api_event(method, path, *, query=None, user_id="first-user", body=None):
    event = {
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {
                "jwt": {"claims": {"sub": user_id}},
            },
        },
        "queryStringParameters": query,
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def image_entry(*, attempt_count=1, ocr_status="FAILED"):
    return {
        "entryId": "entry_ocr123456",
        "sourceType": "image",
        "status": "OCR_FAILED",
        "ocrStatus": ocr_status,
        "ocrAttemptCount": attempt_count,
    }


class OcrJobsApiRouteTests(unittest.TestCase):
    @patch("app.list_ocr_jobs")
    def test_list_is_user_scoped_filtered_and_paginated(self, list_jobs):
        list_jobs.return_value = {
            "items": [{"entryId": "entry_ocr123456"}],
            "count": 1,
            "limit": 10,
            "nextCursor": "opaque-next-cursor",
        }

        result = lambda_handler(
            api_event(
                "GET",
                "/ocr-jobs",
                query={
                    "status": "failed",
                    "limit": "10",
                    "cursor": "opaque-current-cursor",
                },
                user_id="private-user",
            ),
            None,
        )
        body = json.loads(result["body"])

        self.assertEqual(result["statusCode"], 200)
        list_jobs.assert_called_once_with(
            user_id="private-user",
            status_filter="FAILED",
            limit="10",
            cursor="opaque-current-cursor",
        )
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["limit"], 10)
        self.assertEqual(body["nextCursor"], "opaque-next-cursor")

    @patch("app.list_ocr_jobs")
    def test_invalid_status_returns_400_without_querying(self, list_jobs):
        result = lambda_handler(
            api_event(
                "GET",
                "/ocr-jobs",
                query={"status": "RUNNING"},
            ),
            None,
        )
        body = json.loads(result["body"])

        self.assertEqual(result["statusCode"], 400)
        self.assertEqual(body["error"], "Invalid OCR job status.")
        list_jobs.assert_not_called()

    @patch("app.list_ocr_jobs", side_effect=ValueError("bad cursor"))
    def test_invalid_or_cross_user_cursor_returns_400(self, _list_jobs):
        result = lambda_handler(
            api_event(
                "GET",
                "/ocr-jobs",
                query={"cursor": "invalid"},
            ),
            None,
        )
        body = json.loads(result["body"])

        self.assertEqual(result["statusCode"], 400)
        self.assertEqual(body["error"], "InvalidOCRJobsCursor")
        self.assertFalse(body["retryable"])

    @patch("app.start_ocr_execution")
    @patch("app.queue_ocr_job")
    @patch("app.get_entry_by_id")
    def test_retry_exhaustion_returns_409(
        self,
        get_entry,
        queue_job,
        start_execution,
    ):
        get_entry.return_value = image_entry(attempt_count=3)

        result = lambda_handler(
            api_event(
                "POST",
                "/entries/entry_ocr123456/ocr/retry",
                body={},
            ),
            None,
        )
        body = json.loads(result["body"])

        self.assertEqual(result["statusCode"], 409)
        self.assertEqual(body["error"], "OCRRetryNotAllowed")
        self.assertEqual(body["retry"]["remainingAttempts"], 0)
        self.assertFalse(body["retry"]["canRetry"])
        queue_job.assert_not_called()
        start_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
