from typing import Any

from ocr import extract_text_from_s3_image
from storage import (
    OcrStateError,
    begin_ocr_attempt,
    get_entry_by_id,
    get_ocr_retry_state,
    mark_ocr_failed,
    update_entry_ocr_result,
)


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

        raise RuntimeError(
            f"OCR failed for entry {entry_id}: {exc}"
        ) from exc
