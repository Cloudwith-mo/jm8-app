from typing import Any

from account_deletion_guard import ensure_user_mutation_allowed
from storage import record_ocr_workflow_failure


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("Workflow failure input must be an object.")

    user_id = str(event.get("userId") or "").strip()
    entry_id = str(event.get("entryId") or "").strip()
    workflow_error = event.get("workflowError") or {}

    if not user_id:
        raise ValueError("userId is required.")

    if not entry_id:
        raise ValueError("entryId is required.")

    error_name = str(
        workflow_error.get("Error")
        or "OCRWorkflowFailed"
    )

    cause = str(
        workflow_error.get("Cause")
        or "The OCR workflow failed."
    )

    ensure_user_mutation_allowed(user_id)
    updated_entry = record_ocr_workflow_failure(
        user_id=user_id,
        entry_id=entry_id,
        workflow_error=error_name,
        workflow_cause=cause,
    )

    if not updated_entry:
        raise ValueError("Entry not found while recording failure.")

    print({
        "event": "ocr_workflow_failure_recorded",
        "entryId": entry_id,
        "workflowError": error_name,
    })

    return {
        "message": "OCR workflow failure recorded.",
        "entryId": entry_id,
        "status": updated_entry.get("status"),
        "ocrStatus": updated_entry.get("ocrStatus"),
        "failureReason": updated_entry.get("failureReason"),
    }
