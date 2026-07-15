import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key


TABLE_NAME = os.environ["TABLE_NAME"]
RAW_BUCKET = os.environ["RAW_BUCKET"]

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)
s3 = boto3.client("s3")

MAX_OCR_ATTEMPTS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_entry_id() -> str:
    return f"entry_{uuid.uuid4().hex[:12]}"


def user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def entry_sk(created_at: str, entry_id: str) -> str:
    return f"ENTRY#{created_at}#{entry_id}"


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


def update_entry_analysis(user_id: str, entry_id: str, analysis: dict) -> dict:
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
            "analysisStatus = :analysisStatus, "
            "analysis = :analysis, "
            "updatedAt = :updatedAt"
        ),
        ExpressionAttributeNames={
            "#status": "status"
        },
        ExpressionAttributeValues={
            ":status": "ANALYZED",
            ":analysisStatus": "COMPLETED",
            ":analysis": analysis,
            ":updatedAt": now
        }
    )

    return get_entry_by_id(user_id, entry_id)


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


def update_entry_review(user_id: str, entry_id: str, clean_text: str) -> dict:
    entry = get_entry_by_id(user_id, entry_id)

    if not entry:
        return {}

    now = utc_now()
    word_count = len(clean_text.split())

    table.update_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"]
        },
        UpdateExpression=(
            "SET #status = :status, "
            "cleanText = :cleanText, "
            "reviewStatus = :reviewStatus, "
            "reviewedAt = :reviewedAt, "
            "wordCount = :wordCount, "
            "analysisStatus = :analysisStatus, "
            "updatedAt = :updatedAt "
            "REMOVE analysis"
        ),
        ExpressionAttributeNames={
            "#status": "status"
        },
        ExpressionAttributeValues={
            ":status": "REVIEWED",
            ":cleanText": clean_text,
            ":reviewStatus": "COMPLETED",
            ":reviewedAt": now,
            ":wordCount": word_count,
            ":analysisStatus": "NOT_ANALYZED",
            ":updatedAt": now
        }
    )

    return get_entry_by_id(user_id, entry_id)


def delete_entry(user_id: str, entry_id: str) -> dict:
    """
    Deletes a journal entry metadata record from DynamoDB and attempts to delete
    the original S3 image if one exists.
    """
    result = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"ENTRY#{entry_id}")
    )

    items = result.get("Items", [])

    if not items:
        raise ValueError("Entry not found")

    entry = items[0]

    if entry.get("PK") != f"USER#{user_id}":
        raise ValueError("Entry not found")

    table.delete_item(
        Key={
            "PK": entry["PK"],
            "SK": entry["SK"],
        }
    )

    bucket = entry.get("s3RawBucket")
    key = entry.get("s3RawKey")

    if bucket and key:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass

    return {
        "entryId": entry_id,
        "deleted": True,
        "deletedImage": bool(bucket and key),
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
            "attemptCount": retry_state["attemptCount"],
            "maxAttempts": retry_state["maxAttempts"],
            "remainingAttempts": retry_state["remainingAttempts"],
            "canRetry": retry_state["canRetry"],
            "lastAttemptAt": clean_entry.get("ocrLastAttemptAt"),
            "processingStartedAt": clean_entry.get("ocrStartedAt"),
            "completedAt": clean_entry.get("ocrCompletedAt"),
            "failedAt": clean_entry.get("ocrFailedAt"),
            "createdAt": clean_entry.get("createdAt"),
            "updatedAt": clean_entry.get("updatedAt"),
        })

    return jobs
