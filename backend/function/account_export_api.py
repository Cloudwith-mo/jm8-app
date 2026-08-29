"""Authenticated API coordination for asynchronous account exports."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from account_export_contract import (
    is_valid_export_id,
    serialize_public_job,
    validate_export_file_name,
    validate_export_object_key,
)
from account_export_store import (
    ActiveExportExists,
    ExportStoreUnavailable,
    create_or_replay_export,
    fail_export,
    get_export_job,
    list_export_jobs,
    mark_export_expired,
)


WORKFLOW_ARN = os.environ.get("ACCOUNT_EXPORT_WORKFLOW_ARN", "")
EXPORT_BUCKET = os.environ.get("EXPORT_BUCKET", "")
PRESIGNED_URL_SECONDS = 15 * 60
step_functions = boto3.client("stepfunctions")
_aws_region = os.environ.get("AWS_REGION", "us-east-1")
s3 = boto3.client(
    "s3",
    endpoint_url=f"https://s3.{_aws_region}.amazonaws.com",
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "virtual"},
    ),
)


class AccountExportApiError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.payload = {"error": code, "message": message, "retryable": retryable}


def _not_found() -> AccountExportApiError:
    return AccountExportApiError(404, "ExportNotFound", "The export was not found.")


def _profile_from_claims(claims: dict[str, Any]) -> dict[str, Any]:
    profile = {"subject": str(claims.get("sub") or "")}
    email = claims.get("email")
    email_verified = claims.get("email_verified")
    if (
        isinstance(email, str)
        and email.strip()
        and (email_verified is True or email_verified == "true")
    ):
        profile["email"] = email.strip()[:512]
    name = claims.get("name") or claims.get("preferred_username")
    if isinstance(name, str) and name.strip():
        profile["displayName"] = name.strip()[:200]
    status = claims.get("cognito:user_status")
    if isinstance(status, str) and status.strip():
        profile["accountStatus"] = status.strip()[:50]
    return profile


def create_account_export(
    user_id: str,
    claims: dict[str, Any],
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    try:
        job, replayed = create_or_replay_export(
            user_id=user_id,
            request_token=body.get("requestToken"),
            profile=_profile_from_claims(claims),
        )
    except ValueError as exc:
        raise AccountExportApiError(400, "InvalidRequestToken", str(exc)) from None
    except ActiveExportExists:
        raise AccountExportApiError(
            409, "ActiveExportExists", "An account export is already being prepared."
        ) from None
    except ExportStoreUnavailable:
        raise AccountExportApiError(
            503, "AccountExportUnavailable", "Account export is temporarily unavailable.", True
        ) from None

    if not replayed:
        try:
            step_functions.start_execution(
                stateMachineArn=WORKFLOW_ARN,
                name=job["exportId"],
                input=json.dumps({"action": "RUN", "userId": user_id, "exportId": job["exportId"]}),
            )
        except (ClientError, ValueError):
            try:
                fail_export(user_id, job["exportId"], "ExportStartFailed", "The export could not be started.", True)
            except Exception:
                pass
            raise AccountExportApiError(
                503, "AccountExportUnavailable", "The export could not be started. Please try again.", True
            ) from None

    print(json.dumps({"event": "AccountExportRequested", "status": job["status"], "replayed": replayed}))
    return 202, {"export": serialize_public_job(job), "replayed": replayed}


def list_account_exports(user_id: str) -> tuple[int, dict[str, Any]]:
    try:
        jobs = list_export_jobs(user_id, 10)
    except ClientError:
        raise AccountExportApiError(
            503, "AccountExportUnavailable", "Account exports are temporarily unavailable.", True
        ) from None
    return 200, {"exports": [serialize_public_job(job) for job in jobs]}


def _is_expired(job: dict[str, Any]) -> bool:
    expires_at = job.get("expiresAt")
    if not isinstance(expires_at, str):
        return True
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed <= datetime.now(timezone.utc)


def get_account_export(user_id: str, export_id: object) -> tuple[int, dict[str, Any]]:
    if not is_valid_export_id(export_id):
        raise _not_found()
    try:
        job = get_export_job(user_id, str(export_id))
    except ClientError:
        raise AccountExportApiError(
            503, "AccountExportUnavailable", "The export status is temporarily unavailable.", True
        ) from None
    if not job:
        raise _not_found()

    if job.get("status") != "COMPLETED":
        return 200, {"export": serialize_public_job(job)}

    if _is_expired(job):
        job = mark_export_expired(user_id, str(export_id))
        return 200, {"export": serialize_public_job(job)}

    try:
        key = validate_export_object_key(user_id, str(export_id), job.get("objectKey"))
        file_name = validate_export_file_name(job.get("fileName"))
        if job.get("bucket") != EXPORT_BUCKET:
            raise ValueError("invalid export bucket")
        s3.head_object(Bucket=EXPORT_BUCKET, Key=key)
        download_url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": EXPORT_BUCKET,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{file_name}"',
            },
            ExpiresIn=PRESIGNED_URL_SECONDS,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
            job = mark_export_expired(user_id, str(export_id))
            return 200, {"export": serialize_public_job(job)}
        raise AccountExportApiError(
            503, "AccountExportUnavailable", "The export download is temporarily unavailable.", True
        ) from None
    except ValueError:
        raise AccountExportApiError(
            503, "AccountExportUnavailable", "The export download is unavailable.", True
        ) from None

    payload = serialize_public_job(job)
    payload["downloadUrl"] = download_url
    return 200, {"export": payload}
