from journal_analyzer import analyze_journal_entry
from ocr import extract_text_from_s3_image
import base64
import json
import os
from typing import Any
from storage import (
    create_text_entry,
    list_entries,
    get_entry_by_id,
    update_entry_analysis,
    create_upload_url,
    update_entry_ocr_result,
    update_entry_status,
    update_entry_review,
)


def lambda_handler(event, context):
    try:
        method, path = get_method_and_path(event)

        if method == "OPTIONS":
            return response(204, {})

        user_id = get_user_id(event)

        if method == "POST" and path == "/entries":
            body = parse_body(event)
            text = body.get("text", "").strip()

            if not text:
                return response(400, {"error": "Text is required."})

            entry = create_text_entry(user_id=user_id, text=text)

            return response(201, {
                "message": "Entry created.",
                "entry": entry
            })

        if method == "GET" and path == "/entries":
            query = event.get("queryStringParameters") or {}
            limit = int(query.get("limit", 25))

            entries = list_entries(user_id=user_id, limit=limit)

            return response(200, {
                "count": len(entries),
                "entries": entries
            })

        if method == "GET" and path.startswith("/entries/"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(user_id=user_id, entry_id=entry_id)

            if not entry:
                return response(404, {"error": "Entry not found."})

            return response(200, {"entry": entry})

        if method == "POST" and path.startswith("/entries/") and path.endswith("/analyze"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(user_id=user_id, entry_id=entry_id)

            if not entry:
                return response(404, {"error": "Entry not found."})

            text = entry.get("cleanText") or entry.get("rawText") or ""

            if not text:
                return response(400, {
                    "error": "Entry has no text to analyze yet.",
                    "entryStatus": entry.get("status")
                })

            analysis = analyze_journal_entry(text)
            updated_entry = update_entry_analysis(
                user_id=user_id,
                entry_id=entry_id,
                analysis=analysis
            )

            return response(200, {
                "message": "Entry analyzed.",
                "entry": updated_entry
            })

        if method == "POST" and path == "/upload-url":
            body = parse_body(event)

            file_name = body.get("fileName", "journal-upload.jpg")
            content_type = body.get("contentType", "image/jpeg")

            upload = create_upload_url(
                user_id=user_id,
                file_name=file_name,
                content_type=content_type
            )

            return response(201, {
                "message": "Upload URL created.",
                "upload": upload
            })

        if method == "POST" and path.startswith("/entries/") and path.endswith("/ocr"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(user_id=user_id, entry_id=entry_id)

            if not entry:
                return response(404, {"error": "Entry not found."})

            if entry.get("sourceType") != "image":
                return response(400, {
                    "error": "OCR only works on image entries.",
                    "sourceType": entry.get("sourceType")
                })

            bucket = entry.get("s3RawBucket")
            key = entry.get("s3RawKey")

            if not bucket or not key:
                return response(400, {
                    "error": "Entry does not have S3 raw file information."
                })

            update_entry_status(user_id=user_id, entry_id=entry_id, status="OCR_PROCESSING")

            try:
                ocr_result = extract_text_from_s3_image(bucket=bucket, key=key)

                updated_entry = update_entry_ocr_result(
                    user_id=user_id,
                    entry_id=entry_id,
                    ocr_result=ocr_result
                )

                return response(200, {
                    "message": "OCR completed.",
                    "entry": updated_entry
                })

            except Exception as exc:
                update_entry_status(
                    user_id=user_id,
                    entry_id=entry_id,
                    status="OCR_FAILED",
                    error_message=str(exc)
                )

                return response(500, {
                    "error": "OCRFailed",
                    "message": str(exc)
                })

        if method == "PUT" and path.startswith("/entries/") and path.endswith("/review"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(user_id=user_id, entry_id=entry_id)

            if not entry:
                return response(404, {"error": "Entry not found."})

            body = parse_body(event)
            clean_text = (body.get("cleanText") or body.get("text") or "").strip()

            if not clean_text:
                return response(400, {
                    "error": "cleanText is required."
                })

            updated_entry = update_entry_review(
                user_id=user_id,
                entry_id=entry_id,
                clean_text=clean_text
            )

            return response(200, {
                "message": "Entry review saved.",
                "entry": updated_entry
            })

        return response(404, {
            "error": "Route not found.",
            "method": method,
            "path": path
        })

    except Exception as exc:
        print("ERROR:", str(exc))
        print("EVENT:", json.dumps(event))

        return response(500, {
            "error": "InternalServerError",
            "message": str(exc)
        })


def get_method_and_path(event: dict[str, Any]) -> tuple[str, str]:
    # API Gateway HTTP API v2 event
    if "requestContext" in event and "http" in event["requestContext"]:
        http = event["requestContext"]["http"]
        return http.get("method", "GET"), http.get("path", "/")

    # Direct Lambda invoke fallback from Phase 1
    return "POST", "/entries/analyze-direct"


def get_user_id(event: dict[str, Any]) -> str:
    headers = event.get("headers") or {}

    # Temporary until Cognito in Phase 3.
    # Use this header when testing:
    # x-user-id: demo-user
    return headers.get("x-user-id") or headers.get("X-User-Id") or "demo-user"


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")

    if body is None:
        return event

    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, str):
        return json.loads(body or "{}")

    if isinstance(body, dict):
        return body

    return {}


def response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "content-type,x-user-id",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
        },
        "body": json.dumps(body)
    }
