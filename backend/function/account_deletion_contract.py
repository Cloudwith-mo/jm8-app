"""Public and internal contracts for asynchronous account deletion."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from typing import Any


DELETION_REQUEST_ID_PATTERN = re.compile(r"^del_[a-f0-9]{32}$")
REQUEST_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
FAILURE_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
DELETION_STATUSES = {"REQUESTED", "IN_PROGRESS", "COMPLETED", "FAILED"}
ACTIVE_DELETION_STATUSES = {"REQUESTED", "IN_PROGRESS"}
CONFIRMATION_VALUE = "DELETE_MY_ACCOUNT"
PUBLIC_REQUEST_FIELDS = (
    "requestId",
    "status",
    "requestedAt",
    "startedAt",
    "destructiveStartedAt",
    "completedAt",
    "failedAt",
)
RESIDUAL_RETENTION = {
    "cloudWatchLogs": "Up to 30 days.",
    "s3RetainedVersions": (
        "Up to 30 days if immediate version removal fails."
    ),
    "dynamodbPointInTimeRecovery": "Up to 35 days.",
    "stripeFinancialRecords": (
        "Retained according to Stripe and applicable legal requirements."
    ),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_deletion_request_id() -> str:
    return f"del_{secrets.token_hex(16)}"


def is_valid_deletion_request_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and DELETION_REQUEST_ID_PATTERN.fullmatch(value) is not None
    )


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


def subject_digest(subject: object) -> str:
    if not isinstance(subject, str):
        raise ValueError("authenticated subject is required")
    normalized = subject.strip()
    if not normalized or len(normalized) > 512:
        raise ValueError("authenticated subject is invalid")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_confirmation(value: object) -> None:
    if value != CONFIRMATION_VALUE:
        raise ValueError("explicit account deletion confirmation is required")


def safe_failure_code(value: object) -> str:
    if isinstance(value, str) and FAILURE_CODE_PATTERN.fullmatch(value):
        return value
    return "DeletionFailed"


def serialize_public_deletion_request(item: object) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"residualRetention": dict(RESIDUAL_RETENTION)}
    result: dict[str, Any] = {}
    if is_valid_deletion_request_id(item.get("requestId")):
        result["requestId"] = item["requestId"]
    if item.get("status") in DELETION_STATUSES:
        result["status"] = item["status"]
    for field in PUBLIC_REQUEST_FIELDS[2:]:
        value = item.get(field)
        if isinstance(value, str) and UTC_TIMESTAMP_PATTERN.fullmatch(value):
            result[field] = value
    if item.get("status") == "FAILED" and item.get("failureCode"):
        result["failure"] = {
            "code": safe_failure_code(item["failureCode"]),
            "retryable": item.get("retryable") is True,
        }
    result["residualRetention"] = dict(RESIDUAL_RETENTION)
    return result
