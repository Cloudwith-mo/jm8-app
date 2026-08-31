"""Retry-safe deletion and verification of user-owned S3 prefixes."""

from __future__ import annotations

import re
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError


SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
DELETION_PREFIX_PATTERN = re.compile(
    r"^(?:users/[A-Za-z0-9_-]{1,128}/uploads/|exports/[A-Za-z0-9_-]{1,128}/)$"
)
MAX_DELETE_BATCH = 1_000


class DeletionS3Error(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def user_prefix(subject: str, *, export: bool) -> str:
    if not isinstance(subject, str) or SUBJECT_PATTERN.fullmatch(subject) is None:
        raise DeletionS3Error("InvalidDeletionSubject", retryable=False)
    return f"exports/{subject}/" if export else f"users/{subject}/uploads/"


def _delete_batch(client: Any, bucket: str, identifiers: list[dict[str, str]]) -> None:
    for offset in range(0, len(identifiers), MAX_DELETE_BATCH):
        batch = identifiers[offset:offset + MAX_DELETE_BATCH]
        response = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": batch, "Quiet": True},
        )
        if response.get("Errors"):
            raise DeletionS3Error("S3ObjectDeletionIncomplete", retryable=True)


def delete_prefix(client: Any, *, bucket: str, prefix: str) -> None:
    if not bucket or DELETION_PREFIX_PATTERN.fullmatch(prefix) is None:
        raise DeletionS3Error("InvalidS3DeletionScope", retryable=False)
    try:
        key_marker = None
        version_marker = None
        while True:
            arguments: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
            if key_marker:
                arguments["KeyMarker"] = key_marker
            if version_marker:
                arguments["VersionIdMarker"] = version_marker
            page = client.list_object_versions(**arguments)
            identifiers = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for family in ("Versions", "DeleteMarkers")
                for item in page.get(family, [])
                if str(item.get("Key") or "").startswith(prefix)
            ]
            _delete_batch(client, bucket, identifiers)
            if not page.get("IsTruncated"):
                break
            key_marker = page.get("NextKeyMarker")
            version_marker = page.get("NextVersionIdMarker")

        continuation = None
        while True:
            arguments = {"Bucket": bucket, "Prefix": prefix}
            if continuation:
                arguments["ContinuationToken"] = continuation
            page = client.list_objects_v2(**arguments)
            identifiers = [
                {"Key": item["Key"]}
                for item in page.get("Contents", [])
                if str(item.get("Key") or "").startswith(prefix)
            ]
            _delete_batch(client, bucket, identifiers)
            if not page.get("IsTruncated"):
                break
            continuation = page.get("NextContinuationToken")
    except DeletionS3Error:
        raise
    except (ClientError, BotoCoreError):
        raise DeletionS3Error("S3DeletionUnavailable", retryable=True) from None


def prefix_is_empty(client: Any, *, bucket: str, prefix: str) -> bool:
    if not bucket or DELETION_PREFIX_PATTERN.fullmatch(prefix) is None:
        raise DeletionS3Error("InvalidS3DeletionScope", retryable=False)
    try:
        versions = client.list_object_versions(
            Bucket=bucket,
            Prefix=prefix,
            MaxKeys=1,
        )
        if versions.get("Versions") or versions.get("DeleteMarkers"):
            return False
        current = client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            MaxKeys=1,
        )
        return not bool(current.get("Contents"))
    except (ClientError, BotoCoreError):
        raise DeletionS3Error("S3VerificationUnavailable", retryable=True) from None
