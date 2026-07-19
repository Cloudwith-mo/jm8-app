import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError


TABLE_NAME = os.environ["TABLE_NAME"]
RAW_BUCKET = os.environ["RAW_BUCKET"]

dynamodb = boto3.resource("dynamodb")
dynamodb_client = boto3.client("dynamodb")
table = dynamodb.Table(TABLE_NAME)
s3 = boto3.client("s3")
type_serializer = TypeSerializer()

MAX_OCR_ATTEMPTS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_entry_id() -> str:
    return f"entry_{uuid.uuid4().hex[:12]}"


def user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def entry_sk(created_at: str, entry_id: str) -> str:
    return f"ENTRY#{created_at}#{entry_id}"


def analysis_history_prefix(
    entry_id: str,
) -> str:
    return f"ANALYSIS#{entry_id}#"


def analysis_history_sk(
    entry_id: str,
    completed_at: str,
    version_id: str,
) -> str:
    return (
        f"{analysis_history_prefix(entry_id)}"
        f"{completed_at}#{version_id}"
    )


def new_analysis_version_id(
    prefix: str = "analysis",
) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def clean_for_dynamodb(value):
    if isinstance(value, list):
        return [
            clean_for_dynamodb(item)
            for item in value
        ]

    if isinstance(value, dict):
        return {
            key: clean_for_dynamodb(item)
            for key, item in value.items()
        }

    if isinstance(value, float):
        return Decimal(str(value))

    return value


def serialize_attribute_map(
    values: dict,
) -> dict:
    cleaned = clean_for_dynamodb(values)

    return {
        key: type_serializer.serialize(value)
        for key, value in cleaned.items()
    }


def build_analysis_history_item(
    *,
    user_id: str,
    entry_id: str,
    analysis: dict,
    version_id: str,
    analysis_source: str,
    completed_at: str,
    captured_at: str,
) -> dict:
    safe_source = " ".join(
        str(analysis_source or "interactive").split()
    )[:60]

    return {
        "PK": user_pk(user_id),
        "SK": analysis_history_sk(
            entry_id,
            completed_at,
            version_id,
        ),
        "entityType": "ENTRY_ANALYSIS_VERSION",
        "userId": user_id,
        "entryId": entry_id,
        "analysisVersionId": version_id,
        "analysisSource": (
            safe_source or "interactive"
        ),
        "analysisStatus": "COMPLETED",
        "analysisSchemaVersion": str(
            analysis.get("schemaVersion")
            or "unversioned"
        ),
        "analysisPromptVersion": str(
            analysis.get("promptVersion")
            or "unversioned"
        ),
        "analysisModelId": str(
            analysis.get("modelId")
            or "unversioned"
        ),
        "analysisCompletedAt": completed_at,
        "createdAt": captured_at,
        "analysis": analysis,
    }


def build_legacy_analysis_history_item(
    *,
    entry: dict,
    user_id: str,
    entry_id: str,
    captured_at: str,
    version_id: str,
) -> dict | None:
    previous_analysis = entry.get("analysis")

    if not isinstance(previous_analysis, dict):
        return None

    if not previous_analysis:
        return None

    if entry.get("analysisVersionId"):
        return None

    legacy_analysis = dict(previous_analysis)

    legacy_analysis.setdefault(
        "schemaVersion",
        entry.get("analysisSchemaVersion")
        or "unversioned",
    )

    legacy_analysis.setdefault(
        "promptVersion",
        entry.get("analysisPromptVersion")
        or "unversioned",
    )

    legacy_analysis.setdefault(
        "modelId",
        entry.get("analysisModelId")
        or "unversioned",
    )

    completed_at = str(
        legacy_analysis.get("analyzedAt")
        or entry.get("analysisCompletedAt")
        or entry.get("updatedAt")
        or entry.get("createdAt")
        or captured_at
    )

    return build_analysis_history_item(
        user_id=user_id,
        entry_id=entry_id,
        analysis=legacy_analysis,
        version_id=version_id,
        analysis_source="legacy_snapshot",
        completed_at=completed_at,
        captured_at=captured_at,
    )


def build_analysis_history_items(
    *,
    entry: dict,
    user_id: str,
    entry_id: str,
    analysis: dict,
    analysis_source: str,
    captured_at: str,
    new_version_id: str,
    legacy_version_id: str,
) -> list[dict]:
    history_items: list[dict] = []

    legacy_item = (
        build_legacy_analysis_history_item(
            entry=entry,
            user_id=user_id,
            entry_id=entry_id,
            captured_at=captured_at,
            version_id=legacy_version_id,
        )
    )

    if legacy_item:
        history_items.append(legacy_item)

    completed_at = str(
        analysis.get("analyzedAt")
        or captured_at
    )

    history_items.append(
        build_analysis_history_item(
            user_id=user_id,
            entry_id=entry_id,
            analysis=analysis,
            version_id=new_version_id,
            analysis_source=analysis_source,
            completed_at=completed_at,
            captured_at=captured_at,
        )
    )

    return history_items


def list_entry_analysis_versions(
    user_id: str,
    entry_id: str,
    limit: int = 20,
) -> list[dict]:
    safe_limit = max(
        1,
        min(int(limit or 20), 50),
    )

    result = table.query(
        KeyConditionExpression=(
            Key("PK").eq(user_pk(user_id))
            & Key("SK").begins_with(
                analysis_history_prefix(entry_id)
            )
        ),
        ScanIndexForward=False,
        ConsistentRead=True,
        Limit=safe_limit,
    )

    return clean_for_json(
        result.get("Items", [])
    )


def list_entry_analysis_version_keys(
    user_id: str,
    entry_id: str,
) -> list[dict]:
    keys: list[dict] = []

    query_arguments = {
        "KeyConditionExpression": (
            Key("PK").eq(user_pk(user_id))
            & Key("SK").begins_with(
                analysis_history_prefix(entry_id)
            )
        ),
        "ProjectionExpression": "PK, SK",
        "ConsistentRead": True,
    }

    while True:
        result = table.query(**query_arguments)

        keys.extend(
            {
                "PK": item["PK"],
                "SK": item["SK"],
            }
            for item in result.get("Items", [])
        )

        last_key = result.get(
            "LastEvaluatedKey"
        )

        if not last_key:
            break

        query_arguments[
            "ExclusiveStartKey"
        ] = last_key

    return keys


def clean_for_json(value):
    if isinstance(value, list):
        return [clean_for_json(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_for_json(val) for key, val in value.items()}
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    return value


def attach_image_preview_url(entry: dict) -> dict:
    """
    Adds a temporary signed GET URL for private S3 journal images.
    The URL is safe to return to the frontend because it expires.
    """
    clean_entry = clean_for_json(entry)

    bucket = clean_entry.get("s3RawBucket")
    key = clean_entry.get("s3RawKey")

    if bucket and key:
        clean_entry["imagePreviewUrl"] = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket,
                "Key": key
            },
            ExpiresIn=3600
        )

    return clean_entry


def attach_image_preview_urls(entries: list[dict]) -> list[dict]:
    return [attach_image_preview_url(entry) for entry in entries]


def create_text_entry(user_id: str, text: str) -> dict:
    now = utc_now()
    entry_id = new_entry_id()

    item = {
        "PK": user_pk(user_id),
        "SK": entry_sk(now, entry_id),
        "GSI1PK": f"ENTRY#{entry_id}",
        "GSI1SK": user_pk(user_id),
        "entityType": "ENTRY",
        "entryId": entry_id,
        "userId": user_id,
        "sourceType": "typed",
        "status": "REVIEWED",
        "analysisStatus": "NOT_ANALYZED",
        "rawText": text,
        "wordCount": len(text.split()),
        "createdAt": now,
        "updatedAt": now
    }

    table.put_item(Item=item)
    return clean_for_json(item)


def list_entries(user_id: str, limit: int = 25) -> list[dict]:
    result = table.query(
        KeyConditionExpression=Key("PK").eq(user_pk(user_id)) & Key("SK").begins_with("ENTRY#"),
        ScanIndexForward=False,
        Limit=limit
    )

    return attach_image_preview_urls(result.get("Items", []))


def get_entry_by_id(user_id: str, entry_id: str) -> dict | None:
    result = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"ENTRY#{entry_id}") & Key("GSI1SK").eq(user_pk(user_id)),
        Limit=1
    )

    items = result.get("Items", [])
    if not items:
        return None

    return attach_image_preview_url(items[0])


def update_entry_analysis(
    user_id: str,
    entry_id: str,
    analysis: dict,
    *,
    analysis_source: str = "interactive",
) -> dict:
    entry = get_entry_by_id(
        user_id,
        entry_id,
    )

    if not entry:
        return {}

    now = utc_now()

    completed_at = str(
        analysis.get("analyzedAt")
        or now
    )

    new_version_id = (
        new_analysis_version_id()
    )

    legacy_version_id = (
        new_analysis_version_id("legacy")
    )

    history_items = (
        build_analysis_history_items(
            entry=entry,
            user_id=user_id,
            entry_id=entry_id,
            analysis=analysis,
            analysis_source=analysis_source,
            captured_at=now,
            new_version_id=new_version_id,
            legacy_version_id=legacy_version_id,
        )
    )

    transaction_items = [
        {
            "Put": {
                "TableName": TABLE_NAME,
                "Item": serialize_attribute_map(
                    history_item
                ),
                "ConditionExpression": (
                    "attribute_not_exists(PK) "
                    "AND attribute_not_exists(SK)"
                ),
            },
        }
        for history_item in history_items
    ]

    safe_source = " ".join(
        str(
            analysis_source
            or "interactive"
        ).split()
    )[:60]

    expression_values = {
        ":status": "ANALYZED",
        ":analysisStatus": "COMPLETED",
        ":lastAttemptStatus": "COMPLETED",
        ":lastAttemptAt": now,
        ":completedAt": completed_at,
        ":schemaVersion": str(
            analysis.get("schemaVersion")
            or ""
        ),
        ":promptVersion": str(
            analysis.get("promptVersion")
            or ""
        ),
        ":modelId": str(
            analysis.get("modelId")
            or ""
        ),
        ":analysis": analysis,
        ":analysisVersionId": new_version_id,
        ":analysisSource": (
            safe_source or "interactive"
        ),
        ":zero": 0,
        ":one": 1,
        ":historyAdded": len(history_items),
        ":updatedAt": now,
    }

    transaction_items.append({
        "Update": {
            "TableName": TABLE_NAME,
            "Key": serialize_attribute_map({
                "PK": entry["PK"],
                "SK": entry["SK"],
            }),
            "UpdateExpression": (
                "SET #status = :status, "
                "analysisStatus = :analysisStatus, "
                "analysisLastAttemptStatus = "
                ":lastAttemptStatus, "
                "analysisLastAttemptAt = "
                ":lastAttemptAt, "
                "analysisCompletedAt = :completedAt, "
                "analysisSchemaVersion = "
                ":schemaVersion, "
                "analysisPromptVersion = "
                ":promptVersion, "
                "analysisModelId = :modelId, "
                "analysis = :analysis, "
                "analysisVersionId = "
                ":analysisVersionId, "
                "analysisSource = :analysisSource, "
                "analysisAttemptCount = "
                "if_not_exists("
                "analysisAttemptCount, :zero"
                ") + :one, "
                "analysisVersionCount = "
                "if_not_exists("
                "analysisVersionCount, :zero"
                ") + :historyAdded, "
                "updatedAt = :updatedAt "
                "REMOVE analysisFailureCode, "
                "analysisFailureMessage, "
                "analysisFailedAt"
            ),
            "ConditionExpression": (
                "attribute_exists(PK) "
                "AND attribute_exists(SK)"
            ),
            "ExpressionAttributeNames": {
                "#status": "status",
            },
            "ExpressionAttributeValues": (
                serialize_attribute_map(
                    expression_values
                )
            ),
        },
    })

    dynamodb_client.transact_write_items(
        TransactItems=transaction_items,
        ClientRequestToken=new_version_id,
    )

    result = table.get_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"],
        },
        ConsistentRead=True,
    )

    updated_entry = result.get("Item") or {}

    return attach_image_preview_url(
        updated_entry
    )


def mark_entry_analysis_failed(
    user_id: str,
    entry_id: str,
    *,
    failure_code: str,
    failure_message: str,
) -> dict:
    """
    Records a failed analysis attempt without deleting an existing
    successful analysis.
    """
    entry = get_entry_by_id(user_id, entry_id)

    if not entry:
        return {}

    now = utc_now()

    has_previous_analysis = (
        isinstance(entry.get("analysis"), dict)
        and str(
            entry.get("analysisStatus") or ""
        ).upper() == "COMPLETED"
    )

    analysis_status = (
        "COMPLETED"
        if has_previous_analysis
        else "FAILED"
    )

    safe_message = " ".join(
        str(failure_message or "").split()
    )[:240]

    result = table.update_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"],
        },
        UpdateExpression=(
            "SET analysisStatus = :analysisStatus, "
            "analysisLastAttemptStatus = :lastAttemptStatus, "
            "analysisLastAttemptAt = :lastAttemptAt, "
            "analysisFailureCode = :failureCode, "
            "analysisFailureMessage = :failureMessage, "
            "analysisFailedAt = :failedAt, "
            "analysisAttemptCount = "
            "if_not_exists(analysisAttemptCount, :zero) + :one, "
            "updatedAt = :updatedAt"
        ),
        ExpressionAttributeValues={
            ":analysisStatus": analysis_status,
            ":lastAttemptStatus": "FAILED",
            ":lastAttemptAt": now,
            ":failureCode": failure_code,
            ":failureMessage": safe_message,
            ":failedAt": now,
            ":zero": 0,
            ":one": 1,
            ":updatedAt": now,
        },
        ReturnValues="ALL_NEW",
    )

    attributes = result.get("Attributes") or {}

    return attach_image_preview_url(
        attributes
    )


def create_upload_url(user_id: str, file_name: str, content_type: str) -> dict:
    now = utc_now()
    entry_id = new_entry_id()

    safe_file_name = file_name.replace("/", "_").replace("\\", "_")
    s3_key = f"users/{user_id}/uploads/{entry_id}/{safe_file_name}"

    upload_url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": RAW_BUCKET,
            "Key": s3_key,
            "ContentType": content_type
        },
        ExpiresIn=900
    )

    item = {
        "PK": user_pk(user_id),
        "SK": entry_sk(now, entry_id),
        "GSI1PK": f"ENTRY#{entry_id}",
        "GSI1SK": user_pk(user_id),
        "entityType": "ENTRY",
        "entryId": entry_id,
        "userId": user_id,
        "sourceType": "image",
        "status": "UPLOAD_URL_CREATED",
        "analysisStatus": "NOT_ANALYZED",
        "s3RawBucket": RAW_BUCKET,
        "s3RawKey": s3_key,
        "originalFileName": safe_file_name,
        "contentType": content_type,
        "createdAt": now,
        "updatedAt": now
    }

    table.put_item(Item=item)

    return {
        "entryId": entry_id,
        "bucket": RAW_BUCKET,
        "s3Key": s3_key,
        "uploadUrl": upload_url,
        "expiresInSeconds": 900
    }


class OcrStateError(ValueError):
    """Raised when an OCR job cannot enter the requested state."""


def get_ocr_retry_state(
    entry: dict,
    max_attempts: int = MAX_OCR_ATTEMPTS,
) -> dict:
    attempt_count = int(entry.get("ocrAttemptCount") or 0)
    job_status = derive_ocr_job_status(entry)
    remaining_attempts = max(max_attempts - attempt_count, 0)

    return {
        "jobStatus": job_status,
        "attemptCount": attempt_count,
        "maxAttempts": max_attempts,
        "remainingAttempts": remaining_attempts,
        "canRetry": job_status == "FAILED" and remaining_attempts > 0,
    }


def queue_ocr_job(
    user_id: str,
    entry_id: str,
    force: bool = False,
    is_retry: bool = False,
) -> dict:
    """
    Atomically moves an image entry into the OCR queue.

    The updatedAt condition prevents two simultaneous API requests
    from starting duplicate workflow executions for the same entry.
    """
    entry = get_entry_by_id(user_id, entry_id)

    if not entry:
        raise OcrStateError("Entry not found.")

    if entry.get("sourceType") != "image":
        raise OcrStateError("OCR only works on image entries.")

    retry_state = get_ocr_retry_state(entry)
    current_status = str(entry.get("status") or "").upper()
    ocr_status = str(entry.get("ocrStatus") or "").upper()

    if current_status in {"OCR_PENDING", "OCR_PROCESSING"}:
        raise OcrStateError("OCR is already queued or processing.")

    if ocr_status in {"PENDING", "PROCESSING"}:
        raise OcrStateError("OCR is already queued or processing.")

    if retry_state["jobStatus"] == "FAILED" and not (
        is_retry or force
    ):
        raise OcrStateError(
            "Failed OCR jobs must use the retry endpoint."
        )

    if retry_state["jobStatus"] == "COMPLETED" and not force:
        raise OcrStateError("OCR has already completed for this entry.")

    if retry_state["remainingAttempts"] <= 0 and not force:
        raise OcrStateError("Maximum OCR attempt limit reached.")

    bucket = entry.get("s3RawBucket")
    key = entry.get("s3RawKey")

    if not bucket or not key:
        raise OcrStateError(
            "Entry does not have S3 raw file information."
        )

    now = utc_now()
    expected_updated_at = entry.get("updatedAt")

    values = {
        ":status": "OCR_PENDING",
        ":ocrStatus": "PENDING",
        ":ocrQueuedAt": now,
        ":updatedAt": now,
    }

    if expected_updated_at:
        condition_expression = "updatedAt = :expectedUpdatedAt"
        values[":expectedUpdatedAt"] = expected_updated_at
    else:
        condition_expression = "attribute_not_exists(updatedAt)"

    try:
        table.update_item(
            Key={
                "PK": entry["PK"],
                "SK": entry["SK"],
            },
            UpdateExpression=(
                "SET #status = :status, "
                "ocrStatus = :ocrStatus, "
                "ocrQueuedAt = :ocrQueuedAt, "
                "updatedAt = :updatedAt "
                "REMOVE failureReason, "
                "errorMessage, "
                "ocrFailedAt, "
                "ocrCompletedAt, "
                "ocrWorkflowError, "
                "ocrWorkflowCause, "
                "ocrWorkflowFailedAt"
            ),
            ConditionExpression=condition_expression,
            ExpressionAttributeNames={
                "#status": "status",
            },
            ExpressionAttributeValues=values,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code == "ConditionalCheckFailedException":
            raise OcrStateError(
                "OCR state changed while the job was being queued. "
                "Refresh and try again."
            ) from exc

        raise

    return get_entry_by_id(user_id, entry_id)


def begin_ocr_attempt(
    user_id: str,
    entry_id: str,
    force: bool = False,
) -> dict:
    entry = get_entry_by_id(user_id, entry_id)

    if not entry:
        raise OcrStateError("Entry not found.")

    if entry.get("sourceType") != "image":
        raise OcrStateError("OCR only works on image entries.")

    retry_state = get_ocr_retry_state(entry)
    current_status = str(entry.get("status") or "").upper()
    ocr_status = str(entry.get("ocrStatus") or "").upper()

    if current_status == "OCR_PROCESSING" or ocr_status == "PROCESSING":
        raise OcrStateError("OCR is already processing for this entry.")

    if retry_state["jobStatus"] == "COMPLETED" and not force:
        raise OcrStateError("OCR has already completed for this entry.")

    if retry_state["remainingAttempts"] <= 0 and not force:
        raise OcrStateError("Maximum OCR attempt limit reached.")

    now = utc_now()
    next_attempt = retry_state["attemptCount"] + 1

    table.update_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"],
        },
        UpdateExpression=(
            "SET #status = :status, "
            "ocrStatus = :ocrStatus, "
            "ocrAttemptCount = :ocrAttemptCount, "
            "ocrStartedAt = :ocrStartedAt, "
            "ocrLastAttemptAt = :ocrLastAttemptAt, "
            "updatedAt = :updatedAt "
            "REMOVE failureReason, ocrFailedAt, errorMessage"
        ),
        ExpressionAttributeNames={
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":status": "OCR_PROCESSING",
            ":ocrStatus": "PROCESSING",
            ":ocrAttemptCount": next_attempt,
            ":ocrStartedAt": now,
            ":ocrLastAttemptAt": now,
            ":updatedAt": now,
        },
    )

    return get_entry_by_id(user_id, entry_id)


def update_entry_ocr_result(user_id: str, entry_id: str, ocr_result: dict) -> dict:
    entry = get_entry_by_id(user_id, entry_id)

    if not entry:
        return {}

    now = utc_now()

    table.update_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"]
        },
        UpdateExpression=(
            "SET #status = :status, "
            "cleanText = :cleanText, "
            "ocrStatus = :ocrStatus, "
            "ocrCompletedAt = :ocrCompletedAt, "
            "ocrLineCount = :ocrLineCount, "
            "ocrWordCount = :ocrWordCount, "
            "ocrRawBlockCount = :ocrRawBlockCount, "
            "updatedAt = :updatedAt "
            "REMOVE failureReason, ocrFailedAt, errorMessage"
        ),
        ExpressionAttributeNames={
            "#status": "status"
        },
        ExpressionAttributeValues={
            ":status": "OCR_COMPLETED",
            ":cleanText": ocr_result.get("cleanText", ""),
            ":ocrStatus": "COMPLETED",
            ":ocrCompletedAt": now,
            ":ocrLineCount": ocr_result.get("lineCount", 0),
            ":ocrWordCount": ocr_result.get("wordCount", 0),
            ":ocrRawBlockCount": ocr_result.get("rawBlockCount", 0),
            ":updatedAt": now
        }
    )

    return get_entry_by_id(user_id, entry_id)


def mark_ocr_failed(
    user_id: str,
    entry_id: str,
    failure_reason: str,
) -> dict:
    entry = get_entry_by_id(user_id, entry_id)

    if not entry:
        return {}

    now = utc_now()

    table.update_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"],
        },
        UpdateExpression=(
            "SET #status = :status, "
            "ocrStatus = :ocrStatus, "
            "failureReason = :failureReason, "
            "errorMessage = :errorMessage, "
            "ocrFailedAt = :ocrFailedAt, "
            "updatedAt = :updatedAt"
        ),
        ExpressionAttributeNames={
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":status": "OCR_FAILED",
            ":ocrStatus": "FAILED",
            ":failureReason": failure_reason,
            ":errorMessage": failure_reason,
            ":ocrFailedAt": now,
            ":updatedAt": now,
        },
    )

    return get_entry_by_id(user_id, entry_id)


def record_ocr_workflow_failure(
    user_id: str,
    entry_id: str,
    workflow_error: str,
    workflow_cause: str,
) -> dict:
    """
    Records a terminal Step Functions failure.

    If the OCR worker already stored a more specific Textract error,
    that original failure reason is preserved.
    """
    entry = get_entry_by_id(user_id, entry_id)

    if not entry:
        return {}

    now = utc_now()
    error_name = str(workflow_error or "OCRWorkflowFailed")[:500]
    cause = str(workflow_cause or "")[:2000]

    failure_reason = str(
        entry.get("failureReason")
        or cause
        or error_name
        or "OCR workflow failed."
    )[:2000]

    table.update_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"],
        },
        UpdateExpression=(
            "SET #status = :status, "
            "ocrStatus = :ocrStatus, "
            "failureReason = :failureReason, "
            "errorMessage = :failureReason, "
            "ocrFailedAt = if_not_exists(ocrFailedAt, :now), "
            "ocrWorkflowError = :workflowError, "
            "ocrWorkflowCause = :workflowCause, "
            "ocrWorkflowFailedAt = :now, "
            "updatedAt = :now"
        ),
        ExpressionAttributeNames={
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":status": "OCR_FAILED",
            ":ocrStatus": "FAILED",
            ":failureReason": failure_reason,
            ":workflowError": error_name,
            ":workflowCause": cause,
            ":now": now,
        },
    )

    return get_entry_by_id(user_id, entry_id)


def update_entry_status(user_id: str, entry_id: str, status: str, error_message: str | None = None) -> dict:
    entry = get_entry_by_id(user_id, entry_id)

    if not entry:
        return {}

    now = utc_now()

    if error_message:
        update_expression = "SET #status = :status, errorMessage = :errorMessage, updatedAt = :updatedAt"
        expression_values = {
            ":status": status,
            ":errorMessage": error_message,
            ":updatedAt": now
        }
    else:
        update_expression = "SET #status = :status, updatedAt = :updatedAt"
        expression_values = {
            ":status": status,
            ":updatedAt": now
        }

    table.update_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"]
        },
        UpdateExpression=update_expression,
        ExpressionAttributeNames={
            "#status": "status"
        },
        ExpressionAttributeValues=expression_values
    )

    return get_entry_by_id(user_id, entry_id)


def update_entry_review(
    user_id: str,
    entry_id: str,
    clean_text: str,
) -> dict:
    entry = get_entry_by_id(
        user_id,
        entry_id,
    )

    if not entry:
        return {}

    now = utc_now()
    word_count = len(clean_text.split())

    legacy_version_id = (
        new_analysis_version_id("legacy")
    )

    legacy_item = (
        build_legacy_analysis_history_item(
            entry=entry,
            user_id=user_id,
            entry_id=entry_id,
            captured_at=now,
            version_id=legacy_version_id,
        )
    )

    history_added = 1 if legacy_item else 0
    transaction_items = []

    if legacy_item:
        transaction_items.append({
            "Put": {
                "TableName": TABLE_NAME,
                "Item": serialize_attribute_map(
                    legacy_item
                ),
                "ConditionExpression": (
                    "attribute_not_exists(PK) "
                    "AND attribute_not_exists(SK)"
                ),
            },
        })

    expression_values = {
        ":status": "REVIEWED",
        ":cleanText": clean_text,
        ":reviewStatus": "COMPLETED",
        ":reviewedAt": now,
        ":wordCount": word_count,
        ":analysisStatus": "NOT_ANALYZED",
        ":historyAdded": history_added,
        ":zero": 0,
        ":updatedAt": now,
    }

    transaction_items.append({
        "Update": {
            "TableName": TABLE_NAME,
            "Key": serialize_attribute_map({
                "PK": entry["PK"],
                "SK": entry["SK"],
            }),
            "UpdateExpression": (
                "SET #status = :status, "
                "cleanText = :cleanText, "
                "reviewStatus = :reviewStatus, "
                "reviewedAt = :reviewedAt, "
                "wordCount = :wordCount, "
                "analysisStatus = :analysisStatus, "
                "analysisVersionCount = "
                "if_not_exists("
                "analysisVersionCount, :zero"
                ") + :historyAdded, "
                "updatedAt = :updatedAt "
                "REMOVE analysis, "
                "analysisVersionId, "
                "analysisSource, "
                "analysisSchemaVersion, "
                "analysisPromptVersion, "
                "analysisModelId, "
                "analysisCompletedAt, "
                "analysisLastAttemptStatus, "
                "analysisLastAttemptAt, "
                "analysisFailureCode, "
                "analysisFailureMessage, "
                "analysisFailedAt"
            ),
            "ConditionExpression": (
                "attribute_exists(PK) "
                "AND attribute_exists(SK)"
            ),
            "ExpressionAttributeNames": {
                "#status": "status",
            },
            "ExpressionAttributeValues": (
                serialize_attribute_map(
                    expression_values
                )
            ),
        },
    })

    request_token = (
        new_analysis_version_id("review")
    )

    dynamodb_client.transact_write_items(
        TransactItems=transaction_items,
        ClientRequestToken=request_token,
    )

    result = table.get_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"],
        },
        ConsistentRead=True,
    )

    updated_entry = result.get("Item") or {}

    return attach_image_preview_url(
        updated_entry
    )


def delete_entry(
    user_id: str,
    entry_id: str,
) -> dict:
    """
    Deletes the journal entry, its immutable analysis history,
    and its original S3 image when present.
    """
    entry = get_entry_by_id(
        user_id,
        entry_id,
    )

    if not entry:
        raise ValueError("Entry not found")

    history_keys = (
        list_entry_analysis_version_keys(
            user_id,
            entry_id,
        )
    )

    with table.batch_writer() as batch:
        for key in history_keys:
            batch.delete_item(Key=key)

        batch.delete_item(
            Key={
                "PK": entry["PK"],
                "SK": entry["SK"],
            }
        )

    bucket = entry.get("s3RawBucket")
    key = entry.get("s3RawKey")

    if bucket and key:
        try:
            s3.delete_object(
                Bucket=bucket,
                Key=key,
            )
        except Exception:
            pass

    return {
        "entryId": entry_id,
        "deleted": True,
        "deletedImage": bool(
            bucket and key
        ),
        "deletedAnalysisVersions": len(
            history_keys
        ),
    }

def derive_ocr_job_status(entry: dict) -> str:
    """
    Converts existing entry/OCR states into a simple job status:
    PENDING, COMPLETED, or FAILED.
    """
    ocr_status = str(entry.get("ocrStatus") or "").upper()
    entry_status = str(entry.get("status") or "").upper()

    if "FAILED" in ocr_status or "FAILED" in entry_status:
        return "FAILED"

    if (
        ocr_status == "COMPLETED"
        or entry_status in {"OCR_COMPLETED", "REVIEWED", "ANALYZED"}
    ):
        return "COMPLETED"

    if (
        ocr_status in {"PENDING", "IN_PROGRESS", "PROCESSING", "STARTED"}
        or entry_status in {
            "UPLOAD_URL_CREATED",
            "OCR_PENDING",
            "OCR_IN_PROGRESS",
            "PROCESSING",
        }
    ):
        return "PENDING"

    return "PENDING"


def list_ocr_jobs(
    user_id: str,
    status_filter: str | None = None,
) -> list[dict]:
    """
    Returns image-based journal entries as OCR job records.

    Pagination is handled internally so this continues working after the
    user's archive grows beyond DynamoDB's single-query response limit.
    """
    query_args = {
        "KeyConditionExpression": (
            Key("PK").eq(f"USER#{user_id}")
            & Key("SK").begins_with("ENTRY#")
        ),
        "ScanIndexForward": False,
    }

    entries: list[dict] = []

    while True:
        result = table.query(**query_args)
        entries.extend(result.get("Items", []))

        last_key = result.get("LastEvaluatedKey")

        if not last_key:
            break

        query_args["ExclusiveStartKey"] = last_key

    requested_status = (status_filter or "ALL").upper()
    jobs: list[dict] = []

    for entry in entries:
        if entry.get("sourceType") != "image":
            continue

        job_status = derive_ocr_job_status(entry)

        if requested_status != "ALL" and job_status != requested_status:
            continue

        clean_entry = attach_image_preview_url(entry)
        retry_state = get_ocr_retry_state(clean_entry)

        jobs.append({
            "entryId": clean_entry.get("entryId"),
            "jobStatus": job_status,
            "status": clean_entry.get("status"),
            "ocrStatus": clean_entry.get("ocrStatus"),
            "reviewStatus": clean_entry.get("reviewStatus"),
            "analysisStatus": clean_entry.get("analysisStatus"),
            "originalFileName": clean_entry.get("originalFileName"),
            "contentType": clean_entry.get("contentType"),
            "s3RawKey": clean_entry.get("s3RawKey"),
            "imagePreviewUrl": clean_entry.get("imagePreviewUrl"),
            "ocrWordCount": clean_entry.get("ocrWordCount", 0),
            "ocrLineCount": clean_entry.get("ocrLineCount", 0),
            "failureReason": clean_entry.get("failureReason"),
            "workflowError": clean_entry.get("ocrWorkflowError"),
            "workflowCause": clean_entry.get("ocrWorkflowCause"),
            "workflowFailedAt": clean_entry.get("ocrWorkflowFailedAt"),
            "attemptCount": retry_state["attemptCount"],
            "maxAttempts": retry_state["maxAttempts"],
            "remainingAttempts": retry_state["remainingAttempts"],
            "canRetry": retry_state["canRetry"],
            "queuedAt": clean_entry.get("ocrQueuedAt"),
            "lastAttemptAt": clean_entry.get("ocrLastAttemptAt"),
            "processingStartedAt": clean_entry.get("ocrStartedAt"),
            "completedAt": clean_entry.get("ocrCompletedAt"),
            "failedAt": clean_entry.get("ocrFailedAt"),
            "createdAt": clean_entry.get("createdAt"),
            "updatedAt": clean_entry.get("updatedAt"),
        })

    return jobs
