"""Exact-pool Cognito identity deletion with idempotent verification."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError


class DeletionCognitoError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


RETRYABLE_CODES = {
    "InternalErrorException",
    "LimitExceededException",
    "TooManyRequestsException",
}


def _code(error: ClientError) -> str:
    return str((error.response.get("Error") or {}).get("Code") or "")


def resolve_username(client: Any, *, user_pool_id: str, subject: str) -> str | None:
    if not user_pool_id:
        raise DeletionCognitoError("CognitoPoolNotConfigured", retryable=False)
    escaped = subject.replace("\\", "\\\\").replace('"', '\\"')
    try:
        users = []
        token = None
        while True:
            arguments = {
                "UserPoolId": user_pool_id,
                "Filter": f'sub = "{escaped}"',
                "Limit": 2,
            }
            if token:
                arguments["PaginationToken"] = token
            response = client.list_users(**arguments)
            users.extend(response.get("Users") or [])
            if len(users) > 1:
                break
            token = response.get("PaginationToken")
            if not token:
                break
    except (ClientError, BotoCoreError) as error:
        retryable = isinstance(error, BotoCoreError) or _code(error) in RETRYABLE_CODES
        raise DeletionCognitoError(
            "CognitoLookupUnavailable", retryable=retryable
        ) from None
    if not users:
        return None
    if len(users) != 1 or not users[0].get("Username"):
        raise DeletionCognitoError("CognitoIdentityInvariant", retryable=False)
    return str(users[0]["Username"])


def delete_identity(client: Any, *, user_pool_id: str, subject: str) -> None:
    username = resolve_username(
        client, user_pool_id=user_pool_id, subject=subject
    )
    if username is None:
        return
    try:
        try:
            client.admin_user_global_sign_out(
                UserPoolId=user_pool_id,
                Username=username,
            )
        except ClientError as error:
            if _code(error) != "UserNotFoundException":
                raise
        client.admin_delete_user(
            UserPoolId=user_pool_id,
            Username=username,
        )
        try:
            client.admin_get_user(
                UserPoolId=user_pool_id,
                Username=username,
            )
        except ClientError as error:
            if _code(error) == "UserNotFoundException":
                return
            raise
        raise DeletionCognitoError("CognitoDeletionUnverified", retryable=True)
    except DeletionCognitoError:
        raise
    except (ClientError, BotoCoreError) as error:
        if isinstance(error, ClientError) and _code(error) == "UserNotFoundException":
            return
        retryable = isinstance(error, BotoCoreError) or (
            isinstance(error, ClientError) and _code(error) in RETRYABLE_CODES
        )
        raise DeletionCognitoError(
            "CognitoDeletionUnavailable", retryable=retryable
        ) from None


def identity_is_absent(client: Any, *, user_pool_id: str, subject: str) -> bool:
    return resolve_username(
        client, user_pool_id=user_pool_id, subject=subject
    ) is None
