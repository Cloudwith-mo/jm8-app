"""Idempotent action engine for destructive account deletion."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from account_deletion_cognito import (
    DeletionCognitoError,
    delete_identity,
    identity_is_absent,
)
from account_deletion_contract import is_valid_deletion_request_id, subject_digest
from account_deletion_dynamodb import (
    DeletionDynamoError,
    delete_user_partition,
    partition_is_empty,
)
from account_deletion_quiescence import (
    DeletionQuiescenceError,
    quiesce_user_work,
)
from account_deletion_s3 import (
    DeletionS3Error,
    delete_prefix,
    prefix_is_empty,
    user_prefix,
)
from account_deletion_store import (
    begin_destructive_deletion,
    DeletionStoreInvariant,
    DeletionStoreUnavailable,
    complete_deletion_request,
    get_billing_recovery,
    get_deletion_audit,
    get_deletion_subject,
    mark_deletion_checkpoint,
    start_deletion_request,
    fail_deletion_request,
    write_billing_recovery,
)
from account_deletion_stripe import (
    DeletionStripeError,
    prepare_and_cancel_subscription,
)
from billing_customer_store import stripe_customer_lookup_key
from billing_customer_store import get_stripe_customer_mapping
from storage import TABLE_NAME, dynamodb_client, table


ACTIONS = (
    "START",
    "QUIESCE",
    "CANCEL_SUBSCRIPTION",
    "DELETE_RAW_OBJECTS",
    "DELETE_EXPORT_OBJECTS",
    "DELETE_APPLICATION_DATA",
    "DELETE_COGNITO_IDENTITY",
    "VERIFY",
    "COMPLETE",
    "FAIL",
)
DESTRUCTIVE_ACTIONS = set(ACTIONS[2:9])
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")
EXPORT_BUCKET = os.environ.get("EXPORT_BUCKET", "")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
s3 = boto3.client("s3")
stepfunctions = boto3.client("stepfunctions")
cognito = boto3.client("cognito-idp")


class AccountDeletionRetryableError(RuntimeError):
    pass


class AccountDeletionInvariantError(RuntimeError):
    pass


@dataclass
class Dependencies:
    table_resource: Any = table
    dynamodb: Any = dynamodb_client
    s3_client: Any = s3
    stepfunctions_client: Any = stepfunctions
    cognito_client: Any = cognito
    stripe_gateway: Any = None
    billing_mapping_reader: Any = get_stripe_customer_mapping
    table_name: str = TABLE_NAME
    raw_bucket: str = RAW_BUCKET
    export_bucket: str = EXPORT_BUCKET
    user_pool_id: str = COGNITO_USER_POOL_ID
    workflow_arns: Mapping[str, str] | None = None


def _raise_classified(error: Exception) -> None:
    retryable = getattr(error, "retryable", True)
    code = getattr(error, "code", "AccountDeletionActionFailed")
    if retryable:
        raise AccountDeletionRetryableError(code) from None
    raise AccountDeletionInvariantError(code) from None


def _reverse_lookup_absent(deps: Dependencies, recovery: Any) -> bool:
    if not recovery or not recovery.get("stripeCustomerId"):
        return True
    key = stripe_customer_lookup_key(
        recovery.get("stripeCustomerId"),
        livemode=recovery.get("livemode"),
    )
    response = deps.table_resource.get_item(Key=key, ConsistentRead=True)
    return response.get("Item") is None


def _delete_reverse_lookup(deps: Dependencies, recovery: Any) -> None:
    if not recovery or not recovery.get("stripeCustomerId"):
        return
    key = stripe_customer_lookup_key(
        recovery.get("stripeCustomerId"),
        livemode=recovery.get("livemode"),
    )
    deps.table_resource.delete_item(Key=key)


def run_action(event: dict[str, Any], deps: Dependencies | None = None) -> dict[str, Any]:
    dependencies = deps or Dependencies()
    action = str(event.get("action") or "").strip().upper()
    request_id = str(event.get("requestId") or "").strip()
    if action not in ACTIONS or not is_valid_deletion_request_id(request_id):
        raise AccountDeletionInvariantError("InvalidDeletionWorkflowInput")
    try:
        audit = get_deletion_audit(
            request_id, table_resource=dependencies.table_resource
        )
        if audit is None:
            raise AccountDeletionInvariantError("DeletionOwnershipInvariant")
        if audit.get("status") == "COMPLETED":
            return {"action": action, "status": "COMPLETED", "idempotent": True}
        subject = get_deletion_subject(
            request_id, table_resource=dependencies.table_resource
        )
        if (
            subject is None
            or audit.get("subjectDigest") != subject_digest(subject)
        ):
            raise AccountDeletionInvariantError("DeletionOwnershipInvariant")

        if action != "FAIL" and audit.get("status") == "FAILED":
            audit = start_deletion_request(
                subject=subject,
                request_id=request_id,
                table_resource=dependencies.table_resource,
                transact_writer=dependencies.dynamodb.transact_write_items,
            )

        if action == "START":
            audit = start_deletion_request(
                subject=subject,
                request_id=request_id,
                table_resource=dependencies.table_resource,
            )
            return {"action": action, "status": audit["status"]}

        if action == "QUIESCE":
            workflow_arns = dependencies.workflow_arns or {
                "ocr": os.environ.get("OCR_WORKFLOW_ARN", ""),
                "reanalysis": os.environ.get(
                    "HISTORICAL_REANALYSIS_WORKFLOW_ARN", ""
                ),
                "export": os.environ.get("ACCOUNT_EXPORT_WORKFLOW_ARN", ""),
            }
            result = quiesce_user_work(
                subject=subject,
                deletion_started_at=audit.get("startedAt"),
                table_resource=dependencies.table_resource,
                stepfunctions_client=dependencies.stepfunctions_client,
                workflow_arns=workflow_arns,
                now=event.get("now"),
            )
            return {"action": action, "status": "IN_PROGRESS", **result}

        if action == "CANCEL_SUBSCRIPTION":
            destructive_started = bool(audit.get("destructiveStartedAt"))
            prepare_and_cancel_subscription(
                subject,
                mapping_reader=dependencies.billing_mapping_reader,
                gateway=dependencies.stripe_gateway,
                recovery_writer=(
                    (lambda **values: None)
                    if destructive_started
                    else lambda **values: write_billing_recovery(
                        subject=subject,
                        request_id=request_id,
                        table_resource=dependencies.table_resource,
                        **values,
                    )
                ),
                destructive_marker=(
                    (lambda: None)
                    if destructive_started
                    else lambda: begin_destructive_deletion(
                        subject=subject,
                        request_id=request_id,
                        table_resource=dependencies.table_resource,
                        transact_writer=(
                            dependencies.dynamodb.transact_write_items
                        ),
                    )
                ),
            )
        elif action in DESTRUCTIVE_ACTIONS and not audit.get(
            "destructiveStartedAt"
        ):
            raise AccountDeletionInvariantError(
                "DestructiveBoundaryMissing"
            )
        elif action == "DELETE_RAW_OBJECTS":
            delete_prefix(
                dependencies.s3_client,
                bucket=dependencies.raw_bucket,
                prefix=user_prefix(subject, export=False),
            )
        elif action == "DELETE_EXPORT_OBJECTS":
            delete_prefix(
                dependencies.s3_client,
                bucket=dependencies.export_bucket,
                prefix=user_prefix(subject, export=True),
            )
        elif action == "DELETE_APPLICATION_DATA":
            recovery = get_billing_recovery(
                subject=subject,
                request_id=request_id,
                table_resource=dependencies.table_resource,
            )
            delete_user_partition(
                dependencies.table_resource,
                dependencies.dynamodb,
                table_name=dependencies.table_name,
                subject=subject,
            )
            _delete_reverse_lookup(dependencies, recovery)
        elif action == "DELETE_COGNITO_IDENTITY":
            recovery = get_billing_recovery(
                subject=subject,
                request_id=request_id,
                table_resource=dependencies.table_resource,
            )
            prerequisites = (
                prefix_is_empty(
                    dependencies.s3_client,
                    bucket=dependencies.raw_bucket,
                    prefix=user_prefix(subject, export=False),
                )
                and prefix_is_empty(
                    dependencies.s3_client,
                    bucket=dependencies.export_bucket,
                    prefix=user_prefix(subject, export=True),
                )
                and partition_is_empty(dependencies.table_resource, subject)
                and _reverse_lookup_absent(dependencies, recovery)
            )
            if not prerequisites:
                raise AccountDeletionRetryableError("DeletionPrerequisiteUnverified")
            delete_identity(
                dependencies.cognito_client,
                user_pool_id=dependencies.user_pool_id,
                subject=subject,
            )
        elif action == "VERIFY":
            recovery = get_billing_recovery(
                subject=subject,
                request_id=request_id,
                table_resource=dependencies.table_resource,
            )
            verified = (
                prefix_is_empty(
                    dependencies.s3_client,
                    bucket=dependencies.raw_bucket,
                    prefix=user_prefix(subject, export=False),
                )
                and prefix_is_empty(
                    dependencies.s3_client,
                    bucket=dependencies.export_bucket,
                    prefix=user_prefix(subject, export=True),
                )
                and partition_is_empty(dependencies.table_resource, subject)
                and _reverse_lookup_absent(dependencies, recovery)
                and identity_is_absent(
                    dependencies.cognito_client,
                    user_pool_id=dependencies.user_pool_id,
                    subject=subject,
                )
            )
            if not verified:
                raise AccountDeletionRetryableError("DeletionVerificationIncomplete")
            mark_deletion_checkpoint(
                subject=subject,
                request_id=request_id,
                timestamp_field="verifiedAt",
                table_resource=dependencies.table_resource,
            )
        elif action == "COMPLETE":
            audit = complete_deletion_request(
                subject=subject,
                request_id=request_id,
                table_resource=dependencies.table_resource,
                transact_writer=dependencies.dynamodb.transact_write_items,
            )
            return {"action": action, "status": audit["status"]}
        elif action == "FAIL":
            phase = str(event.get("failedPhase") or "START").strip().upper()
            retryable = event.get("retryable") is True
            audit = fail_deletion_request(
                subject=subject,
                request_id=request_id,
                failure_code=str(event.get("failureCode") or "DeletionFailed"),
                retryable=retryable,
                phase=phase,
                destructive_started=bool(audit.get("destructiveStartedAt")),
                table_resource=dependencies.table_resource,
                transact_writer=dependencies.dynamodb.transact_write_items,
            )
            return {
                "action": action,
                "status": audit["status"],
                "durablePartialFailure": bool(audit.get("destructiveStartedAt")),
            }

        return {"action": action, "status": "IN_PROGRESS", "idempotent": True}
    except (AccountDeletionRetryableError, AccountDeletionInvariantError):
        raise
    except (
        DeletionStoreUnavailable,
        DeletionStoreInvariant,
        DeletionS3Error,
        DeletionDynamoError,
        DeletionStripeError,
        DeletionCognitoError,
        DeletionQuiescenceError,
    ) as error:
        _raise_classified(error)
    except (ClientError, BotoCoreError):
        raise AccountDeletionRetryableError("AccountDeletionAwsUnavailable") from None


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    result = run_action(event)
    print(json.dumps({
        "event": "AccountDeletionActionCompleted",
        "action": result["action"],
        "status": result["status"],
        "durablePartialFailure": result.get("durablePartialFailure", False),
    }))
    return result
