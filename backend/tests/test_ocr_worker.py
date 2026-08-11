import os
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError


os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw")


from ocr_worker import (  # noqa: E402
    OcrInputError,
    OcrRetryableError,
    lambda_handler,
)


def image_entry(**overrides):
    item = {
        "entryId": "entry_ocr123456",
        "sourceType": "image",
        "status": "OCR_PENDING",
        "ocrStatus": "PENDING",
        "ocrAttemptCount": 0,
        "s3RawBucket": "journalm8-test-raw",
        "s3RawKey": "users/first-user/entry_ocr123456/page.jpg",
    }
    item.update(overrides)
    return item


def client_error(code, operation):
    return ClientError(
        {"Error": {"Code": code, "Message": code}},
        operation,
    )


class OcrWorkerTests(unittest.TestCase):
    @patch("ocr_worker.update_entry_ocr_result")
    @patch("ocr_worker.extract_text_from_s3_image")
    @patch("ocr_worker.begin_ocr_attempt")
    @patch("ocr_worker.s3.head_object")
    @patch("ocr_worker.get_entry_by_id")
    def test_upload_is_verified_before_attempt_is_consumed(
        self,
        get_entry,
        head_object,
        begin_attempt,
        extract_text,
        update_result,
    ):
        get_entry.return_value = image_entry()
        begin_attempt.return_value = image_entry(
            status="OCR_PROCESSING",
            ocrStatus="PROCESSING",
            ocrAttemptCount=1,
        )
        extract_text.return_value = {
            "cleanText": "Journal text",
            "lineCount": 1,
            "wordCount": 2,
            "rawBlockCount": 3,
        }
        update_result.return_value = image_entry(
            status="OCR_COMPLETED",
            ocrStatus="COMPLETED",
            ocrAttemptCount=1,
            ocrLineCount=1,
            ocrWordCount=2,
        )

        result = lambda_handler(
            {"userId": "first-user", "entryId": "entry_ocr123456"},
            None,
        )

        head_object.assert_called_once()
        begin_attempt.assert_called_once()
        self.assertEqual(result["ocrStatus"], "COMPLETED")

    @patch("ocr_worker.mark_ocr_failed")
    @patch("ocr_worker.begin_ocr_attempt")
    @patch("ocr_worker.s3.head_object")
    @patch("ocr_worker.get_entry_by_id", return_value=image_entry())
    def test_missing_upload_does_not_consume_attempt(
        self,
        _get_entry,
        head_object,
        begin_attempt,
        mark_failed,
    ):
        head_object.side_effect = client_error("NoSuchKey", "HeadObject")

        with self.assertRaises(OcrInputError):
            lambda_handler(
                {"userId": "first-user", "entryId": "entry_ocr123456"},
                None,
            )

        begin_attempt.assert_not_called()
        mark_failed.assert_called_once()

    @patch("ocr_worker.mark_ocr_failed")
    @patch("ocr_worker.begin_ocr_attempt")
    @patch("ocr_worker.s3.head_object")
    @patch("ocr_worker.get_entry_by_id", return_value=image_entry())
    def test_temporary_s3_failure_is_retryable_without_consuming_attempt(
        self,
        _get_entry,
        head_object,
        begin_attempt,
        mark_failed,
    ):
        head_object.side_effect = client_error("SlowDown", "HeadObject")

        with self.assertRaises(OcrRetryableError):
            lambda_handler(
                {"userId": "first-user", "entryId": "entry_ocr123456"},
                None,
            )

        begin_attempt.assert_not_called()
        mark_failed.assert_not_called()

    @patch("ocr_worker.mark_ocr_failed")
    @patch("ocr_worker.extract_text_from_s3_image")
    @patch("ocr_worker.begin_ocr_attempt")
    @patch("ocr_worker.s3.head_object")
    @patch("ocr_worker.get_entry_by_id", return_value=image_entry())
    def test_temporary_textract_failure_leaves_attempt_processing_for_retry(
        self,
        _get_entry,
        _head_object,
        begin_attempt,
        extract_text,
        mark_failed,
    ):
        begin_attempt.return_value = image_entry(
            status="OCR_PROCESSING",
            ocrStatus="PROCESSING",
            ocrAttemptCount=1,
        )
        extract_text.side_effect = client_error(
            "ThrottlingException",
            "DetectDocumentText",
        )

        with self.assertRaises(OcrRetryableError):
            lambda_handler(
                {"userId": "first-user", "entryId": "entry_ocr123456"},
                None,
            )

        mark_failed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
