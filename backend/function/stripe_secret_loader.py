from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import (
    Any,
    Mapping,
)

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
)

from billing_policy import (
    STRIPE_SECRET_KEY_ENV,
    STRIPE_SECRET_KEY_PATTERN,
    STRIPE_WEBHOOK_SECRET_ENV,
    STRIPE_WEBHOOK_SECRET_PATTERN,
)


STRIPE_SECRET_ARN_ENV = (
    "STRIPE_SECRET_ARN"
)

STRIPE_SECRET_FIELD = (
    STRIPE_SECRET_KEY_ENV
)

STRIPE_SECRET_CACHE_TTL_SECONDS = 300

STRIPE_SECRET_ARN_PATTERN = re.compile(
    (
        r"^arn:[^:\s]+:"
        r"secretsmanager:"
        r"[^:\s]+:"
        r"\d{12}:"
        r"secret:[^\s]{1,512}$"
    )
)

RETRYABLE_SECRET_ERRORS = {
    "DecryptionFailure",
    "InternalServiceError",
    "InternalServiceErrorException",
    "RequestTimeout",
    "RequestTimeoutException",
    "ServiceUnavailable",
    "ThrottlingException",
    "TooManyRequestsException",
}

_SECRET_CACHE: dict[str, tuple[dict[str, str], float]] = {}

_SECRET_CACHE_LOCK = (
    threading.RLock()
)


class StripeSecretLoadError(
    RuntimeError
):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
    ):
        super().__init__(message)

        self.code = str(
            code
            or "StripeSecretLoadError"
        )

        self.message = str(
            message
        )

        self.retryable = bool(
            retryable
        )


def clear_stripe_secret_cache(
) -> None:
    with _SECRET_CACHE_LOCK:
        _SECRET_CACHE.clear()


def normalize_stripe_secret_arn(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip()

    if not (
        STRIPE_SECRET_ARN_PATTERN
        .fullmatch(normalized)
    ):
        raise StripeSecretLoadError(
            "InvalidStripeSecretArn",
            (
                "The Stripe secret reference "
                "is not configured correctly."
            ),
            retryable=False,
        )

    return normalized


def _client_error_code(
    error: ClientError,
) -> str:
    return str(
        error.response.get(
            "Error",
            {},
        ).get(
            "Code",
            "SecretsManagerError",
        )
    )


def _validate_secret_key(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip()

    if not (
        STRIPE_SECRET_KEY_PATTERN
        .fullmatch(normalized)
    ):
        raise StripeSecretLoadError(
            "InvalidStripeSecretValue",
            (
                "The Stripe secret value "
                "is invalid."
            ),
            retryable=False,
        )

    return normalized


def _validate_webhook_secret(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if not STRIPE_WEBHOOK_SECRET_PATTERN.fullmatch(normalized):
        raise StripeSecretLoadError(
            "InvalidStripeWebhookSecret",
            "The Stripe webhook secret is invalid.",
            retryable=False,
        )
    return normalized


def _parse_secret_string(secret_string: Any) -> dict[str, str]:
    if not isinstance(
        secret_string,
        str,
    ):
        raise StripeSecretLoadError(
            "InvalidStripeSecretValue",
            (
                "The Stripe secret value "
                "is invalid."
            ),
            retryable=False,
        )

    try:
        payload = json.loads(
            secret_string
        )

    except json.JSONDecodeError as error:
        raise StripeSecretLoadError(
            "InvalidStripeSecretValue",
            (
                "The Stripe secret value "
                "is invalid."
            ),
            retryable=False,
        ) from error

    if not isinstance(
        payload,
        Mapping,
    ):
        raise StripeSecretLoadError(
            "InvalidStripeSecretValue",
            (
                "The Stripe secret value "
                "is invalid."
            ),
            retryable=False,
        )

    return {
        STRIPE_SECRET_KEY_ENV: _validate_secret_key(payload.get(STRIPE_SECRET_FIELD)),
        STRIPE_WEBHOOK_SECRET_ENV: _validate_webhook_secret(payload.get(STRIPE_WEBHOOK_SECRET_ENV)),
    }


def _cached_secret(
    secret_arn: str,
    *,
    now: float,
) -> dict[str, str] | None:
    cached = _SECRET_CACHE.get(
        secret_arn
    )

    if cached is None:
        return None

    secrets, expires_at = cached

    if expires_at <= now:
        _SECRET_CACHE.pop(
            secret_arn,
            None,
        )

        return None

    return secrets


def _secret_region(
    secret_arn: str,
) -> str:
    parts = secret_arn.split(
        ":",
        5,
    )

    if len(parts) < 6:
        raise StripeSecretLoadError(
            "InvalidStripeSecretArn",
            (
                "The Stripe secret reference "
                "is not configured correctly."
            ),
            retryable=False,
        )

    return parts[3]


def retrieve_stripe_secret_key(
    secret_arn: Any,
    *,
    client_resource=None,
) -> str:
    return retrieve_stripe_secrets(
        secret_arn,
        client_resource=client_resource,
    )[STRIPE_SECRET_KEY_ENV]


def retrieve_stripe_secrets(
    secret_arn: Any,
    *,
    client_resource=None,
    refresh: bool = False,
) -> dict[str, str]:
    normalized_arn = (
        normalize_stripe_secret_arn(
            secret_arn
        )
    )

    now = time.monotonic()

    with _SECRET_CACHE_LOCK:
        cached = None if refresh else _cached_secret(normalized_arn, now=now)

        if cached is not None:
            return dict(cached)

        client = (
            client_resource
            if client_resource is not None
            else boto3.client(
                "secretsmanager",
                region_name=(
                    _secret_region(
                        normalized_arn
                    )
                ),
            )
        )

        try:
            result = (
                client.get_secret_value(
                    SecretId=(
                        normalized_arn
                    )
                )
            )

        except ClientError as error:
            code = _client_error_code(
                error
            )

            raise StripeSecretLoadError(
                code,
                (
                    "JM8 could not retrieve "
                    "the Stripe secret."
                ),
                retryable=(
                    code
                    in RETRYABLE_SECRET_ERRORS
                ),
            ) from error

        except BotoCoreError as error:
            raise StripeSecretLoadError(
                "StripeSecretConnectionError",
                (
                    "JM8 could not retrieve "
                    "the Stripe secret."
                ),
                retryable=True,
            ) from error

        secrets = _parse_secret_string(result.get("SecretString"))

        _SECRET_CACHE[
            normalized_arn
        ] = (
            secrets,
            (
                now
                + (
                    STRIPE_SECRET_CACHE_TTL_SECONDS
                )
            ),
        )

        return dict(secrets)


def load_stripe_runtime_environment(
    environ: (
        Mapping[str, str] | None
    ) = None,
    *,
    client_resource=None,
    refresh: bool = False,
) -> dict[str, str]:
    source = dict(
        environ
        if environ is not None
        else os.environ
    )

    direct_secret = str(
        source.get(
            STRIPE_SECRET_KEY_ENV
        )
        or ""
    ).strip()

    if direct_secret:
        return source

    secret_arn = (
        normalize_stripe_secret_arn(
            source.get(
                STRIPE_SECRET_ARN_ENV
            )
        )
    )

    source.update(retrieve_stripe_secrets(
        secret_arn,
        client_resource=(
            client_resource
        ),
        refresh=refresh,
    ))

    return source
