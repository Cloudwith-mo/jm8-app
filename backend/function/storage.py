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
            "updatedAt = :updatedAt"
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

