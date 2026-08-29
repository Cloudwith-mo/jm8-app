"""Public and internal contracts for asynchronous JM8 account exports."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any


EXPORT_ID_PATTERN = re.compile(
    r"^exp_[0-9]{8}T[0-9]{6}Z_[a-z0-9]{16}$"
)
EXPORT_FILE_PATTERN = re.compile(r"^jm8-export-[0-9]{8}T[0-9]{6}Z\.zip$")
REQUEST_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
EXPORT_STATUSES = {"QUEUED", "RUNNING", "COMPLETED", "FAILED", "EXPIRED"}
ACTIVE_STATUSES = {"QUEUED", "RUNNING"}
PUBLIC_JOB_FIELDS = (
    "exportId",
    "status",
    "createdAt",
    "startedAt",
    "completedAt",
    "expiresAt",
    "fileName",
    "fileSizeBytes",
    "entryCount",
    "imageCount",
    "askHistoryCount",
    "warningCount",
    "error",
)
PUBLIC_ERROR_FIELDS = ("code", "message", "retryable")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_export_id(now: datetime | None = None) -> str:
    instant = (now or utc_now()).astimezone(timezone.utc)
    timestamp = instant.strftime("%Y%m%dT%H%M%SZ")
    return f"exp_{timestamp}_{secrets.token_hex(8)}"


def is_valid_export_id(value: object) -> bool:
    return isinstance(value, str) and EXPORT_ID_PATTERN.fullmatch(value) is not None


def validate_export_file_name(value: object) -> str:
    if not isinstance(value, str) or EXPORT_FILE_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid export file name")
    return value


def normalize_request_token(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("requestToken is required")
    normalized = value.strip().lower()
    if REQUEST_TOKEN_PATTERN.fullmatch(normalized) is None:
        raise ValueError("requestToken is invalid")
    return normalized


def request_token_digest(value: object) -> str:
    normalized = normalize_request_token(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def account_export_sk(export_id: str) -> str:
    return f"ACCOUNT_EXPORT#{export_id}"


def export_object_prefix(user_id: str, export_id: str) -> str:
    if not user_id or not is_valid_export_id(export_id):
        raise ValueError("invalid export destination")
    return f"exports/{user_id}/{export_id}/"


def validate_export_object_key(user_id: str, export_id: str, key: object) -> str:
    if not isinstance(key, str) or not key.startswith(export_object_prefix(user_id, export_id)):
        raise ValueError("invalid export destination")
    path = PurePosixPath(key)
    if ".." in path.parts or not key.endswith(".zip"):
        raise ValueError("invalid export destination")
    return key


def validate_source_image(user_id: str, raw_bucket: str, bucket: object, key: object) -> str:
    expected_prefix = f"users/{user_id}/uploads/"
    if bucket != raw_bucket or not isinstance(key, str) or not key.startswith(expected_prefix):
        raise ValueError("invalid image reference")
    path = PurePosixPath(key)
    if ".." in path.parts or path.name in {"", ".", ".."}:
        raise ValueError("invalid image reference")
    return key


def sanitize_archive_filename(value: object, fallback: str = "image") -> str:
    name = PurePosixPath(str(value or "").replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    if not name:
        name = fallback
    stem, dot, suffix = name.rpartition(".")
    if dot and stem:
        stem = stem[:100]
        suffix = suffix[:16]
        return f"{stem}.{suffix}"
    return name[:116]


def collision_safe_filename(value: object, used: set[str]) -> str:
    safe = sanitize_archive_filename(value)
    candidate = safe
    stem, dot, suffix = safe.rpartition(".")
    base = stem if dot and stem else safe
    extension = f".{suffix}" if dot and stem else ""
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{base}-{counter}{extension}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def public_error(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {key: value[key] for key in PUBLIC_ERROR_FIELDS if key in value}
    return result or None


def serialize_public_job(item: dict[str, Any]) -> dict[str, Any]:
    result = {key: item[key] for key in PUBLIC_JOB_FIELDS if key in item}
    if "error" in result:
        error = public_error(result["error"])
        if error is None:
            result.pop("error", None)
        else:
            result["error"] = error
    return result


def _allow(item: object, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {field: item[field] for field in fields if field in item}


def serialize_entry(item: object) -> dict[str, Any]:
    return _allow(item, (
        "entryId", "date", "createdAt", "updatedAt", "sourceType", "text",
        "ocrText", "ocrTranscript", "correctedText", "reviewState", "status",
        "analysis", "analysisStatus", "analysisCompletedAt", "analysisVersionId",
        "analysisSchemaVersion", "analysisPromptVersion", "analysisModelId",
        "imageArchivePath",
    ))


def serialize_ask_history(item: object) -> dict[str, Any]:
    return _allow(item, (
        "question", "answer", "createdAt", "completedAt", "status",
        "contextReferences", "entryReferences",
    ))


def serialize_usage(item: object) -> dict[str, Any]:
    return _allow(item, (
        "month", "period", "entryAnalysisCount", "askCount", "ocrCount",
        "entryAnalysesUsed", "askQuestionsUsed", "uploadsUsed", "updatedAt",
    ))


def serialize_entitlement(item: object) -> dict[str, Any]:
    return _allow(item, (
        "plan", "status", "accessStartsAt", "accessEndsAt",
        "cancelAtPeriodEnd", "allowances", "updatedAt",
    ))


def serialize_subscription(item: object) -> dict[str, Any]:
    return _allow(item, (
        "plan", "status", "accessStartsAt", "accessEndsAt", "currentPeriodStart",
        "currentPeriodEnd", "cancelAtPeriodEnd", "canceledAt", "updatedAt",
    ))
