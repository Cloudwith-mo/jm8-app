import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ocr import extract_text_from_s3_image
from storage import (
    OcrStateError,
    begin_ocr_attempt,
    get_entry_by_id,
    get_ocr_retry_state,
    mark_ocr_failed,
    update_entry_ocr_result,
)


AWS_REGION = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "us-east-1"
)
s3 = boto3.client("s3", region_name=AWS_REGION)


class OcrInputError(RuntimeError):
    """Permanent OCR input failure that must not be retried automatically."""


class OcrRetryableError(RuntimeError):
    """Temporary AWS failure that Step Functions may retry."""


RETRYABLE_AWS_ERROR_CODES = {
    "InternalServerError",
    "LimitExceededException",
    "ProvisionedThroughputExceededException",
    "RequestTimeout",
    "RequestTimeoutException",
    "ServiceUnavailable",
    "SlowDown",
    "Throttling",
    "ThrottlingException",
}

MISSING_OBJECT_ERROR_CODES = {
    "404",
    "NoSuchKey",
    "NotFound",
}


def _client_error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code") or "")


def _ensure_upload_is_ready(bucket: str, key: str) -> None:
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = _client_error_code(exc)

        if code in MISSING_OBJECT_ERROR_CODES:
            raise OcrInputError(
                "The journal image upload is missing or incomplete. "
                "Upload the image again before retrying OCR."
            ) from exc

        if code in RETRYABLE_AWS_ERROR_CODES:
            raise OcrRetryableError(
                f"S3 temporarily could not validate the upload ({code})."
            ) from exc

        raise OcrInputError(
            f"The journal image cannot be read from S3 ({code or 'Unknown'})."
        ) from exc
    except BotoCoreError as exc:
        raise OcrRetryableError(
            "S3 temporarily could not validate the journal image."
        ) from exc


def _raise_classified_ocr_error(exc: Exception) -> None:
    if isinstance(exc, OcrRetryableError):
        raise exc

    if isinstance(exc, OcrInputError):
        raise exc

    if isinstance(exc, ClientError):
        code = _client_error_code(exc)

        if code in RETRYABLE_AWS_ERROR_CODES:
            raise OcrRetryableError(
                f"Textract temporarily failed ({code})."
            ) from exc

        raise OcrInputError(
            f"Textract rejected the journal image ({code or 'Unknown'})."
        ) from exc

    if isinstance(exc, BotoCoreError):
        raise OcrRetryableError(
            "Textract temporarily could not process the journal image."
        ) from exc

    raise OcrInputError(str(exc)) from exc


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Runs one OCR attempt for an image-based journal entry.

    Expected input:
    {
        "userId": "cognito-sub",
        "entryId": "entry_123",
        "force": false
    }
    """
    if not isinstance(event, dict):
        raise ValueError("Workflow input must be a JSON object.")

    user_id = str(event.get("userId") or "").strip()
    entry_id = str(event.get("entryId") or "").strip()
    force = event.get("force") is True

    if not user_id:
        raise ValueError("userId is required.")

    if not entry_id:
        raise ValueError("entryId is required.")

    entry = get_entry_by_id(
        user_id=user_id,
        entry_id=entry_id,
    )

    if not entry:
        raise ValueError("Entry not found.")

    if entry.get("sourceType") != "image":
        raise ValueError("OCR only works on image entries.")

    bucket = entry.get("s3RawBucket")
    key = entry.get("s3RawKey")

    if not bucket or not key:
        raise ValueError("Entry does not have S3 raw file information.")

    # Do not consume an OCR attempt until the browser upload is visible in S3.
    try:
        _ensure_upload_is_ready(bucket=bucket, key=key)
    except OcrInputError as exc:
        mark_ocr_failed(
            user_id=user_id,
            entry_id=entry_id,
            failure_reason=str(exc),
        )
        raise

    try:
        begin_ocr_attempt(
            user_id=user_id,
            entry_id=entry_id,
            force=force,
        )
    except OcrStateError as exc:
        raise RuntimeError(str(exc)) from exc

    try:
        ocr_result = extract_text_from_s3_image(
            bucket=bucket,
            key=key,
        )

        updated_entry = update_entry_ocr_result(
            user_id=user_id,
            entry_id=entry_id,
            ocr_result=ocr_result,
        )

        retry_state = get_ocr_retry_state(updated_entry)

        result = {
            "message": "OCR workflow completed.",
            "entryId": entry_id,
            "status": updated_entry.get("status"),
            "ocrStatus": updated_entry.get("ocrStatus"),
            "attemptCount": retry_state["attemptCount"],
            "lineCount": updated_entry.get("ocrLineCount", 0),
            "wordCount": updated_entry.get("ocrWordCount", 0),
            "retry": retry_state,
        }

        print({
            "event": "ocr_workflow_completed",
            "entryId": entry_id,
            "attemptCount": retry_state["attemptCount"],
        })

        return result

    except Exception as exc:
        try:
            _raise_classified_ocr_error(exc)
        except OcrRetryableError:
            # Leave the entry PROCESSING. A Step Functions retry resumes the
            # same logical attempt without incrementing its counter.
            print({
                "event": "ocr_workflow_retryable_failure",
                "entryId": entry_id,
                "errorType": type(exc).__name__,
            })
            raise
        except OcrInputError as classified_exc:
            exc = classified_exc

        failed_entry = mark_ocr_failed(
            user_id=user_id,
            entry_id=entry_id,
            failure_reason=str(exc),
        )

        retry_state = (
            get_ocr_retry_state(failed_entry)
            if failed_entry
            else {}
        )

        print({
            "event": "ocr_workflow_failed",
            "entryId": entry_id,
            "error": str(exc),
            "retry": retry_state,
        })

        raise OcrInputError(
            f"OCR failed for entry {entry_id}: {exc}"
        ) from exc
