"""Assemble a private account export outside the synchronous API path."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from account_export_contract import (
    collision_safe_filename,
    export_object_prefix,
    isoformat_utc,
    is_valid_export_id,
    serialize_ask_history,
    serialize_entitlement,
    serialize_subscription,
    serialize_usage,
    utc_now,
    validate_export_object_key,
    validate_source_image,
)
from account_export_store import (
    fail_export,
    get_export_job,
    release_active_lock,
    update_export_status,
)
from storage import RAW_BUCKET, table, user_pk


EXPORT_BUCKET = os.environ["EXPORT_BUCKET"]
MAX_USER_RECORDS = 10_000
MAX_IMAGE_OBJECTS = 5_000
MAX_SOURCE_BYTES = 2 * 1024**3
MAX_ARCHIVE_BYTES = 3 * 1024**3
CONFIGURED_EPHEMERAL_STORAGE_BYTES = int(
    os.environ.get("ACCOUNT_EXPORT_EPHEMERAL_STORAGE_MB", "6144")
) * 1024**2
S3_SINGLE_PUT_OBJECT_LIMIT_BYTES = 5_000_000_000
EXPORT_RETENTION_SECONDS = 24 * 60 * 60
s3 = boto3.client("s3")

# Downloads and the separately-built ZIP coexist in /tmp. Keep their combined
# ceilings strictly below configured ephemeral storage, and keep the completed
# archive strictly below S3's single-request PutObject ceiling.
if MAX_SOURCE_BYTES + MAX_ARCHIVE_BYTES >= CONFIGURED_EPHEMERAL_STORAGE_BYTES:
    raise RuntimeError("Account export limits exceed configured ephemeral storage")
if MAX_ARCHIVE_BYTES >= S3_SINGLE_PUT_OBJECT_LIMIT_BYTES:
    raise RuntimeError("Account export archive limit exceeds single PutObject capacity")


class ExportTooLarge(RuntimeError):
    pass


class ExportSecurityError(RuntimeError):
    pass


class ExportSourceError(RuntimeError):
    pass


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _query_user_records(user_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    arguments: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(user_pk(user_id)),
        "ConsistentRead": True,
    }
    while True:
        response = table.query(**arguments)
        items.extend(response.get("Items", []))
        if len(items) > MAX_USER_RECORDS:
            raise ExportTooLarge("record limit exceeded")
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        arguments["ExclusiveStartKey"] = last_key


def _entry_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    mappings = {
        "entryId": "entryId", "date": "date", "createdAt": "createdAt",
        "updatedAt": "updatedAt", "sourceType": "sourceType",
        "rawText": "text", "cleanText": "ocrTranscript",
        "correctedText": "userCorrection", "reviewStatus": "reviewState",
        "status": "status", "analysis": "analysis",
        "analysisStatus": "analysisStatus", "analysisCompletedAt": "analysisCompletedAt",
        "analysisVersionId": "analysisVersionId", "analysisSchemaVersion": "analysisSchemaVersion",
        "analysisPromptVersion": "analysisPromptVersion", "analysisModelId": "analysisModelId",
    }
    for source, destination in mappings.items():
        if source in item:
            payload[destination] = item[source]
    return payload


def _ask_payload(item: dict[str, Any]) -> dict[str, Any]:
    answer = item.get("answer") if isinstance(item.get("answer"), dict) else {}
    return serialize_ask_history({
        "question": answer.get("question"),
        "answer": answer.get("answer"),
        "createdAt": item.get("createdAt"),
        "completedAt": answer.get("generatedAt"),
        "status": answer.get("status"),
        "contextReferences": answer.get("evidence"),
    })


def _analysis_version_payload(item: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "analysisVersionId", "analysisSource", "analysisStatus",
        "analysisSchemaVersion", "analysisPromptVersion", "analysisModelId",
        "analysisCompletedAt", "createdAt", "analysis",
    )
    return {key: item[key] for key in allowed if key in item}


def _download_images(
    user_id: str,
    entries: list[tuple[dict[str, Any], dict[str, Any]]],
    images_dir: Path,
) -> tuple[int, int, list[dict[str, str]]]:
    image_count = 0
    source_bytes = 0
    warnings: list[dict[str, str]] = []
    used_names: set[str] = set()
    for raw, public in entries:
        if raw.get("sourceType") != "image":
            continue
        if image_count >= MAX_IMAGE_OBJECTS:
            raise ExportTooLarge("image object limit exceeded")
        try:
            key = validate_source_image(
                user_id, RAW_BUCKET, raw.get("s3RawBucket"), raw.get("s3RawKey")
            )
        except ValueError as exc:
            raise ExportSecurityError("invalid image reference") from exc
        archive_name = collision_safe_filename(
            raw.get("originalFileName") or Path(key).name, used_names
        )
        destination = images_dir / archive_name
        try:
            response = s3.get_object(Bucket=RAW_BUCKET, Key=key)
            size = int(response.get("ContentLength") or 0)
            if source_bytes + size > MAX_SOURCE_BYTES:
                raise ExportTooLarge("source byte limit exceeded")
            with destination.open("wb") as output:
                shutil.copyfileobj(response["Body"], output)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
                warnings.append({
                    "code": "MissingImage",
                    "message": f"An image for entry {raw.get('entryId', 'unknown')} was unavailable.",
                })
                continue
            if code in {"AccessDenied", "403"} or status == 403:
                raise ExportSecurityError("image access denied") from None
            raise ExportSourceError("image download failed") from None
        source_bytes += destination.stat().st_size
        if source_bytes > MAX_SOURCE_BYTES:
            raise ExportTooLarge("source byte limit exceeded")
        image_count += 1
        public["imageArchivePath"] = f"images/{archive_name}"
    return image_count, source_bytes, warnings


def _build_zip(root: Path, archive_path: Path) -> int:
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED,
        compresslevel=6, allowZip64=True,
    ) as archive:
        archive.writestr("images/", b"")
        for path in sorted(root.rglob("*")):
            if path.is_file() and path != archive_path:
                archive.write(path, path.relative_to(root).as_posix())
    size = archive_path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise ExportTooLarge("archive byte limit exceeded")
    return size


def run_export(user_id: str, export_id: str) -> dict[str, Any]:
    if not user_id or not is_valid_export_id(export_id):
        raise ExportSecurityError("invalid workflow input")
    job = get_export_job(user_id, export_id)
    if not job or job.get("userId") != user_id or job.get("status") not in {"QUEUED", "RUNNING"}:
        raise ExportSecurityError("invalid export job")
    started_at = isoformat_utc(utc_now())
    update_export_status(user_id, export_id, "RUNNING", startedAt=started_at)

    with tempfile.TemporaryDirectory(prefix="jm8-export-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        images_dir = root / "images"
        images_dir.mkdir(mode=0o700)
        records = _query_user_records(user_id)
        entry_pairs = [(_item, _entry_payload(_item)) for _item in records if _item.get("entityType") == "ENTRY"]
        history_by_entry: dict[str, list[dict[str, Any]]] = {}
        for item in records:
            if item.get("entityType") == "ENTRY_ANALYSIS_VERSION" and item.get("entryId"):
                history_by_entry.setdefault(str(item["entryId"]), []).append(
                    _analysis_version_payload(item)
                )
        for raw, public in entry_pairs:
            versions = history_by_entry.get(str(raw.get("entryId") or ""), [])
            if versions:
                public["analysisHistory"] = versions
        ask_history = [_ask_payload(item) for item in records if item.get("entityType") == "ASK_HISTORY"]
        usage = [serialize_usage(item) for item in records if item.get("entityType") == "MONTHLY_USAGE"]
        entitlement_items = [item for item in records if item.get("entityType") == "USER_ENTITLEMENT"]
        entitlement = serialize_entitlement(entitlement_items[-1]) if entitlement_items else {}
        subscription = serialize_subscription(entitlement_items[-1]) if entitlement_items else {}

        image_count, source_bytes, warnings = _download_images(user_id, entry_pairs, images_dir)
        entries = [public for _, public in entry_pairs]
        profile = job.get("accountProfile") if isinstance(job.get("accountProfile"), dict) else {"subject": user_id}
        _write_json(root / "profile.json", profile)
        _write_json(root / "entries.json", entries)
        _write_json(root / "ask-history.json", ask_history)
        _write_json(root / "usage.json", usage)
        _write_json(root / "entitlement.json", entitlement)
        _write_json(root / "subscription.json", subscription)

        generated_at = isoformat_utc(utc_now())
        manifest = {
            "schemaVersion": "1.0", "exportId": export_id,
            "generatedAt": generated_at, "operator": "Muhammad Adeyemi",
            "product": "JM8",
            "includedFiles": [
                "manifest.json", "profile.json", "entries.json", "ask-history.json",
                "usage.json", "entitlement.json", "subscription.json", "images/",
            ],
            "recordCounts": {
                "entries": len(entries), "askHistory": len(ask_history),
                "usagePeriods": len(usage), "images": image_count,
            },
            "archiveSizeBytes": 0, "sourceDataBytes": source_bytes,
            "warnings": warnings,
            "retentionNotice": "This download package expires 24 hours after it is created.",
        }
        archive_path = root / f"jm8-export-{utc_now().strftime('%Y%m%dT%H%M%SZ')}.zip"
        for _ in range(3):
            _write_json(root / "manifest.json", manifest)
            size = _build_zip(root, archive_path)
            if manifest["archiveSizeBytes"] == size:
                break
            manifest["archiveSizeBytes"] = size
        size = archive_path.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            raise ExportTooLarge("archive byte limit exceeded")

        file_name = archive_path.name
        key = f"{export_object_prefix(user_id, export_id)}{file_name}"
        validate_export_object_key(user_id, export_id, key)
        with archive_path.open("rb") as body:
            s3.put_object(
                Bucket=EXPORT_BUCKET, Key=key, Body=body,
                ContentType="application/zip",
                ContentDisposition=f'attachment; filename="{file_name}"',
                ServerSideEncryption="AES256",
            )
        completed = utc_now()
        completed_job = update_export_status(
            user_id, export_id, "COMPLETED",
            completedAt=isoformat_utc(completed),
            expiresAt=isoformat_utc(completed + timedelta(seconds=EXPORT_RETENTION_SECONDS)),
            fileName=file_name, fileSizeBytes=size,
            entryCount=len(entries), imageCount=image_count,
            askHistoryCount=len(ask_history), warningCount=len(warnings),
            bucket=EXPORT_BUCKET, objectKey=key,
        )
        release_active_lock(user_id, export_id)
        print(json.dumps({
            "event": "AccountExportCompleted", "exportId": export_id,
            "status": "COMPLETED", "entryCount": len(entries),
            "imageCount": image_count, "warningCount": len(warnings),
            "fileSizeBytes": size,
        }))
        return {"exportId": export_id, "status": completed_job["status"]}


def record_failure(user_id: str, export_id: str, error_code: str = "ExportFailed") -> dict[str, str]:
    if not user_id or not is_valid_export_id(export_id):
        raise ExportSecurityError("invalid workflow input")
    # PutObject is atomic: a failed upload leaves no readable destination
    # object. Multipart uploads, if introduced, are aborted by the scoped role
    # and the bucket lifecycle safety rule.
    if error_code.endswith("ExportTooLarge"):
        fail_export(user_id, export_id, "ExportTooLarge", "The account data is too large for one export.", False)
    else:
        fail_export(user_id, export_id, "ExportFailed", "The export could not be completed.", True)
    print(json.dumps({"event": "AccountExportFailed", "exportId": export_id, "status": "FAILED", "errorCode": error_code}))
    return {"exportId": export_id, "status": "FAILED"}


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    action = event.get("action", "RUN")
    user_id = event.get("userId")
    export_id = event.get("exportId")
    if action == "RECORD_FAILURE":
        return record_failure(str(user_id or ""), str(export_id or ""), str(event.get("errorCode") or "ExportFailed"))
    try:
        return run_export(str(user_id or ""), str(export_id or ""))
    except ExportTooLarge:
        raise
