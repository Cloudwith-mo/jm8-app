from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from billing_customer_store import (
    BillingCustomerStoreError,
    get_billing_user_for_customer,
)
from billing_identity import build_billing_user_reference
from billing_policy import (
    BILLING_OFFER_PRO_MONTHLY,
    BillingPolicyError,
    load_stripe_webhook_config,
)
from entitlement_policy import (
    SOURCE_STRIPE,
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_EXPIRED,
    STATUS_PAST_DUE,
    STATUS_TRIALING,
)
from entitlement_store import (
    EntitlementConflictError,
    EntitlementStoreError,
    create_entitlement_record,
    get_entitlement_record,
    replace_entitlement_record,
)
from stripe_secret_loader import (
    StripeSecretLoadError,
    load_stripe_runtime_environment,
)
from usage_policy import PLAN_PRO


SIGNATURE_TOLERANCE_SECONDS = 300
SUPPORTED_EVENT_TYPES = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}

TERMINAL_ENTITLEMENT_STATUSES = {
    STATUS_CANCELED,
    STATUS_EXPIRED,
}


class BillingWebhookError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


def _error(
    code: str,
    message: str,
    *,
    status_code: int,
    retryable: bool = False,
) -> None:
    raise BillingWebhookError(
        code,
        message,
        status_code=status_code,
        retryable=retryable,
    )


def _raw_body(event: Mapping[str, Any]) -> bytes:
    body = event.get("body")
    if not isinstance(body, str):
        _error(
            "InvalidWebhookBody",
            "The Stripe webhook body is invalid.",
            status_code=400,
        )

    try:
        return (
            base64.b64decode(body, validate=True)
            if event.get("isBase64Encoded") is True
            else body.encode("utf-8")
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise BillingWebhookError(
            "InvalidWebhookBody",
            "The Stripe webhook body is invalid.",
            status_code=400,
            retryable=False,
        ) from exc


def _header(event: Mapping[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    if not isinstance(headers, Mapping):
        return ""
    expected = name.lower()
    for key, value in headers.items():
        if str(key).lower() == expected:
            return str(value or "").strip()
    return ""


def _signature_parts(value: str) -> tuple[int, list[str]]:
    timestamp = None
    signatures: list[str] = []
    for part in value.split(","):
        key, separator, raw_value = part.strip().partition("=")
        if not separator:
            continue
        if key == "t" and timestamp is None:
            try:
                timestamp = int(raw_value)
            except ValueError:
                timestamp = None
        elif key == "v1" and raw_value:
            signatures.append(raw_value)

    if timestamp is None or not signatures:
        _error(
            "InvalidWebhookSignature",
            "The Stripe webhook signature is invalid.",
            status_code=400,
        )
    return timestamp, signatures


def verify_stripe_signature(
    payload: bytes,
    signature_header: str,
    webhook_secret: str,
    *,
    now: int | None = None,
) -> None:
    timestamp, signatures = _signature_parts(signature_header)
    current = int(time.time() if now is None else now)
    if abs(current - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        _error(
            "ExpiredWebhookSignature",
            "The Stripe webhook signature has expired.",
            status_code=400,
        )

    signed_payload = str(timestamp).encode("ascii") + b"." + payload
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in signatures):
        _error(
            "InvalidWebhookSignature",
            "The Stripe webhook signature is invalid.",
            status_code=400,
        )


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        _error(
            "InvalidWebhookEvent",
            f"The Stripe webhook {field} is invalid.",
            status_code=400,
        )
    return normalized


def _metadata(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _stripe_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(
            "InvalidWebhookEvent",
            "The Stripe subscription period is invalid.",
            status_code=400,
        )
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise BillingWebhookError(
            "InvalidWebhookEvent",
            "The Stripe subscription period is invalid.",
            status_code=400,
            retryable=False,
        ) from exc


def _period(subscription: Mapping[str, Any]) -> tuple[str | None, str | None]:
    start = subscription.get("current_period_start")
    end = subscription.get("current_period_end")
    if start is None or end is None:
        items = subscription.get("items")
        data = items.get("data") if isinstance(items, Mapping) else None
        if isinstance(data, list) and data:
            starts = [item.get("current_period_start") for item in data if isinstance(item, Mapping)]
            ends = [item.get("current_period_end") for item in data if isinstance(item, Mapping)]
            starts = [value for value in starts if isinstance(value, (int, float)) and not isinstance(value, bool)]
            ends = [value for value in ends if isinstance(value, (int, float)) and not isinstance(value, bool)]
            start = min(starts) if starts else start
            end = max(ends) if ends else end
    return _stripe_timestamp(start), _stripe_timestamp(end)


def _entitlement_values(
    event_type: str,
    stripe_object: Mapping[str, Any],
) -> dict[str, Any]:
    if event_type == "checkout.session.completed":
        if stripe_object.get("mode") != "subscription":
            _error(
                "InvalidWebhookEvent",
                "The Checkout Session is not a subscription.",
                status_code=400,
            )
        if str(stripe_object.get("payment_status") or "").lower() not in {"paid", "no_payment_required"}:
            _error(
                "IncompleteCheckoutSession",
                "The Checkout Session is not complete.",
                status_code=400,
            )
        return {
            "status": STATUS_ACTIVE,
            "access_starts_at": None,
            "access_ends_at": None,
            "cancel_at_period_end": False,
        }

    raw_status = "canceled" if event_type == "customer.subscription.deleted" else str(stripe_object.get("status") or "").lower()
    status_map = {
        "trialing": STATUS_TRIALING,
        "active": STATUS_ACTIVE,
        "past_due": STATUS_PAST_DUE,
        "unpaid": STATUS_PAST_DUE,
        "canceled": STATUS_CANCELED,
        "incomplete": STATUS_EXPIRED,
        "incomplete_expired": STATUS_EXPIRED,
        "paused": STATUS_EXPIRED,
    }
    if raw_status not in status_map:
        _error(
            "UnsupportedSubscriptionStatus",
            "The Stripe subscription status is not supported.",
            status_code=400,
        )
    starts_at, ends_at = _period(stripe_object)
    if event_type == "customer.subscription.deleted":
        scheduled_cancellation = False
    else:
        scheduled_cancellation = (
            stripe_object.get("cancel_at_period_end") is True
            or stripe_object.get("cancel_at") is not None
        )
    return {
        "status": status_map[raw_status],
        "access_starts_at": starts_at,
        "access_ends_at": ends_at,
        "cancel_at_period_end": scheduled_cancellation,
    }


def _save_entitlement(
    user_id: str,
    values: Mapping[str, Any],
    *,
    reader: Callable[..., dict[str, Any] | None],
    creator: Callable[..., dict[str, Any]],
    replacer: Callable[..., dict[str, Any]],
) -> None:
    for _ in range(3):
        current = reader(user_id)
        arguments = {
            "plan": PLAN_PRO,
            "status": values["status"],
            "source": SOURCE_STRIPE,
            "access_starts_at": values["access_starts_at"],
            "access_ends_at": values["access_ends_at"],
            "cancel_at_period_end": values["cancel_at_period_end"],
        }
        try:
            if current is None:
                creator(user_id, **arguments)
            else:
                replacer(
                    user_id,
                    expected_updated_at=current["updatedAt"],
                    **arguments,
                )
            return
        except EntitlementConflictError:
            continue
    _error(
        "EntitlementConflict",
        "The JM8 entitlement changed during webhook processing.",
        status_code=503,
        retryable=True,
    )


def process_billing_webhook(
    event: Mapping[str, Any],
    *,
    now: int | None = None,
    secret_loader=load_stripe_runtime_environment,
    customer_reader=get_billing_user_for_customer,
    entitlement_reader=get_entitlement_record,
    entitlement_creator=create_entitlement_record,
    entitlement_replacer=replace_entitlement_record,
) -> dict[str, Any]:
    payload = _raw_body(event)
    try:
        runtime_environment = secret_loader(None)
        config = load_stripe_webhook_config(runtime_environment)
    except (StripeSecretLoadError, BillingPolicyError) as exc:
        raise BillingWebhookError(
            getattr(exc, "code", "WebhookConfigurationUnavailable"),
            "Stripe webhook verification is unavailable.",
            status_code=500,
            retryable=isinstance(exc, StripeSecretLoadError) and exc.retryable,
        ) from exc

    verify_stripe_signature(
        payload,
        _header(event, "stripe-signature"),
        config["webhookSecret"],
        now=now,
    )
    try:
        stripe_event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BillingWebhookError(
            "InvalidWebhookEvent",
            "The Stripe webhook event is invalid.",
            status_code=400,
            retryable=False,
        ) from exc

    if not isinstance(stripe_event, Mapping):
        _error("InvalidWebhookEvent", "The Stripe webhook event is invalid.", status_code=400)

    event_id = _required_text(stripe_event.get("id"), "identifier")
    event_type = _required_text(stripe_event.get("type"), "type")
    livemode = stripe_event.get("livemode")
    if not isinstance(livemode, bool):
        _error("InvalidWebhookEvent", "The Stripe webhook mode is invalid.", status_code=400)

    if event_type not in SUPPORTED_EVENT_TYPES:
        return {"received": True, "eventId": event_id, "ignored": True}

    data = stripe_event.get("data")
    stripe_object = data.get("object") if isinstance(data, Mapping) else None
    if not isinstance(stripe_object, Mapping):
        _error("InvalidWebhookEvent", "The Stripe webhook object is invalid.", status_code=400)

    customer_id = _required_text(stripe_object.get("customer"), "customer")
    try:
        mapping = customer_reader(customer_id, livemode=livemode)
    except BillingCustomerStoreError as exc:
        raise BillingWebhookError(
            exc.code,
            "The billing customer mapping is unavailable.",
            status_code=503,
            retryable=exc.retryable,
        ) from exc
    if mapping is None:
        _error("BillingCustomerNotFound", "The billing customer mapping was not found.", status_code=409)

    user_reference = mapping["userReference"]
    metadata = _metadata(stripe_object.get("metadata"))
    event_reference = metadata.get("jm8_user_ref")
    if event_type == "checkout.session.completed":
        event_reference = event_reference or stripe_object.get("client_reference_id")
    if event_reference != user_reference or build_billing_user_reference(mapping["userId"]) != user_reference:
        _error("BillingCustomerOwnershipMismatch", "The billing customer ownership could not be verified.", status_code=409)
    if metadata.get("jm8_plan_id") != PLAN_PRO or metadata.get("jm8_offer_id") != BILLING_OFFER_PRO_MONTHLY:
        _error("InvalidBillingMetadata", "The Stripe billing metadata is invalid.", status_code=400)

    if event_type == "customer.subscription.updated":
        current = entitlement_reader(mapping["userId"])
        if (
            isinstance(current, Mapping)
            and current.get("status") in TERMINAL_ENTITLEMENT_STATUSES
        ):
            return {
                "received": True,
                "eventId": event_id,
                "ignored": True,
            }

    values = _entitlement_values(event_type, stripe_object)
    try:
        _save_entitlement(
            mapping["userId"],
            values,
            reader=entitlement_reader,
            creator=entitlement_creator,
            replacer=entitlement_replacer,
        )
    except EntitlementStoreError as exc:
        raise BillingWebhookError(
            exc.code,
            "The JM8 entitlement could not be updated.",
            status_code=503,
            retryable=exc.retryable,
        ) from exc

    return {"received": True, "eventId": event_id, "updated": True}
