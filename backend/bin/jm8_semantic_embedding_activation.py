#!/usr/bin/env python3
"""Fail-closed readiness contract for semantic-embedding ingestion state."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "function"))

from semantic_retrieval_evaluation import (  # noqa: E402
    SemanticRetrievalEvaluationError,
    validate_passing_evaluation_report,
)
from semantic_vector_index_contract import (  # noqa: E402
    SemanticVectorIndexContractError,
    validate_vector_index_state,
)


VALID_STAGES = frozenset({"dev", "staging", "prod"})
VALID_TARGET_STATES = frozenset({"Enabled", "Disabled"})
VALID_ACTIVATION_MODES = frozenset({"canary", "steady", "disable"})
DLQ_RETENTION_SECONDS = "1209600"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")


class SemanticEmbeddingActivationError(ValueError):
    """Raised when activation readiness cannot be proven safely."""


def validate_activation_readiness(
    *,
    caller_identity: object,
    table_document: object,
    worker_configuration: object,
    worker_concurrency: object,
    worker_policy_response: object,
    mappings_document: object,
    queue_attributes: object,
    alarms_document: object,
    evaluation_report: object | None,
    target_state: str,
    activation_mode: str,
    app_name: str,
    stage: str,
    region: str,
    account_id: str,
) -> str:
    """Return ENABLE, DISABLE, or NOOP after exact readiness validation."""

    _validate_context(
        target_state=target_state,
        activation_mode=activation_mode,
        app_name=app_name,
        stage=stage,
        region=region,
        account_id=account_id,
    )
    partition = _validate_caller(caller_identity, account_id)

    table_name = f"{app_name}-{stage}-entry-chunks"
    table_arn = (
        f"arn:{partition}:dynamodb:{region}:{account_id}:table/{table_name}"
    )
    worker_name = f"{app_name}-{stage}-semantic-embedding-worker"
    worker_arn = (
        f"arn:{partition}:lambda:{region}:{account_id}:function:{worker_name}"
    )
    role_name = f"{worker_name}-role"
    role_arn = f"arn:{partition}:iam::{account_id}:role/{role_name}"
    policy_name = f"{worker_name}-access"
    dlq_name = f"{app_name}-{stage}-semantic-embedding-dlq"
    dlq_arn = f"arn:{partition}:sqs:{region}:{account_id}:{dlq_name}"
    model_arn = (
        f"arn:{partition}:bedrock:{region}::foundation-model/"
        f"{EMBEDDING_MODEL_ID}"
    )

    table = _mapping(table_document, "semantic table description").get("Table")
    table = _mapping(table, "semantic table")
    if (
        table.get("TableName") != table_name
        or table.get("TableArn") != table_arn
    ):
        raise SemanticEmbeddingActivationError(
            "semantic table identity is invalid"
        )
    stream_arn = table.get("LatestStreamArn")
    stream = table.get("StreamSpecification")
    if (
        not isinstance(stream_arn, str)
        or not stream_arn.startswith(f"{table_arn}/stream/")
        or stream != {
            "StreamEnabled": True,
            "StreamViewType": "NEW_AND_OLD_IMAGES",
        }
    ):
        raise SemanticEmbeddingActivationError(
            "semantic table stream is invalid"
        )

    mapping = _validate_mapping(
        mappings_document,
        stream_arn=stream_arn,
        worker_arn=worker_arn,
        dlq_arn=dlq_arn,
    )
    current_state = mapping["State"]

    if target_state == "Disabled":
        if activation_mode != "disable" or evaluation_report is not None:
            raise SemanticEmbeddingActivationError(
                "semantic embedding disable request is invalid"
            )
        return "NOOP" if current_state == "Disabled" else "DISABLE"

    try:
        index_state = validate_vector_index_state(
            table_document,  # type: ignore[arg-type]
            table_arn=table_arn,
        )
    except SemanticVectorIndexContractError:
        raise SemanticEmbeddingActivationError(
            "semantic vector index is not ready"
        ) from None
    if index_state.get("action") != "READY":
        raise SemanticEmbeddingActivationError(
            "semantic vector index is not ready"
        )

    configuration = _mapping(
        worker_configuration, "semantic embedding worker configuration"
    )
    expected_concurrency = 1 if stage == "prod" else 2
    if any((
        configuration.get("FunctionName") != worker_name,
        configuration.get("FunctionArn") != worker_arn,
        configuration.get("Runtime") != "python3.12",
        configuration.get("Architectures") != ["arm64"],
        configuration.get("Handler") != "semantic_embedding_worker.lambda_handler",
        configuration.get("Role") != role_arn,
        configuration.get("Timeout") != 120,
        configuration.get("MemorySize") != 512,
        configuration.get("State") != "Active",
        configuration.get("LastUpdateStatus") != "Successful",
        configuration.get("Environment", {}).get("Variables")
        != {"ENTRY_CHUNKS_TABLE_NAME": table_name},
    )):
        raise SemanticEmbeddingActivationError(
            "semantic embedding worker configuration is invalid"
        )
    concurrency = _mapping(
        worker_concurrency, "semantic embedding worker concurrency"
    )
    if concurrency.get("ReservedConcurrentExecutions") != expected_concurrency:
        raise SemanticEmbeddingActivationError(
            "semantic embedding worker concurrency is invalid"
        )

    expected_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadExactEntryChunksStream",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:DescribeStream",
                    "dynamodb:GetRecords",
                    "dynamodb:GetShardIterator",
                ],
                "Resource": stream_arn,
            },
            {
                "Sid": "ListEntryChunksStreams",
                "Effect": "Allow",
                "Action": ["dynamodb:ListStreams"],
                "Resource": "*",
            },
            {
                "Sid": "ReadExactEntryChunks",
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem", "dynamodb:Query"],
                "Resource": table_arn,
            },
            {
                "Sid": "PersistExactEntryChunksTransaction",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:ConditionCheckItem",
                    "dynamodb:UpdateItem",
                ],
                "Resource": table_arn,
                "Condition": {
                    "ForAnyValue:StringEquals": {
                        "dynamodb:EnclosingOperation": [
                            "TransactWriteItems"
                        ],
                    },
                },
            },
            {
                "Sid": "InvokeExactEmbeddingModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": model_arn,
            },
            {
                "Sid": "SendExactSemanticEmbeddingDlq",
                "Effect": "Allow",
                "Action": ["sqs:SendMessage"],
                "Resource": dlq_arn,
            },
        ],
    }
    policy_response = _mapping(
        worker_policy_response, "semantic embedding worker policy"
    )
    if (
        policy_response.get("RoleName") != role_name
        or policy_response.get("PolicyName") != policy_name
        or _policy_document(policy_response.get("PolicyDocument"))
        != expected_policy
    ):
        raise SemanticEmbeddingActivationError(
            "semantic embedding worker policy is invalid"
        )

    queue = _mapping(queue_attributes, "semantic embedding DLQ response")
    attributes = _mapping(
        queue.get("Attributes"), "semantic embedding DLQ attributes"
    )
    if any((
        attributes.get("QueueArn") != dlq_arn,
        attributes.get("KmsMasterKeyId") != "alias/aws/sqs",
        attributes.get("MessageRetentionPeriod") != DLQ_RETENTION_SECONDS,
        attributes.get("ApproximateNumberOfMessages") != "0",
        attributes.get("ApproximateNumberOfMessagesNotVisible") != "0",
        attributes.get("ApproximateNumberOfMessagesDelayed") != "0",
    )):
        raise SemanticEmbeddingActivationError(
            "semantic embedding DLQ is not empty and ready"
        )

    _validate_alarms(alarms_document, worker_name, dlq_name)

    if activation_mode == "canary":
        if evaluation_report is not None:
            raise SemanticEmbeddingActivationError(
                "semantic embedding canary request is invalid"
            )
        return "NOOP" if current_state == "Enabled" else "ENABLE"

    if activation_mode != "steady" or current_state != "Enabled":
        raise SemanticEmbeddingActivationError(
            "semantic embedding steady-state request is invalid"
        )
    if evaluation_report is None:
        raise SemanticEmbeddingActivationError(
            "semantic retrieval evaluation report is required"
        )
    try:
        validate_passing_evaluation_report(evaluation_report)
    except SemanticRetrievalEvaluationError:
        raise SemanticEmbeddingActivationError(
            "semantic retrieval evaluation report did not pass"
        ) from None
    return "NOOP"


def _validate_context(
    *,
    target_state: str,
    activation_mode: str,
    app_name: str,
    stage: str,
    region: str,
    account_id: str,
) -> None:
    if target_state not in VALID_TARGET_STATES:
        raise SemanticEmbeddingActivationError(
            "semantic embedding target state is invalid"
        )
    if activation_mode not in VALID_ACTIVATION_MODES:
        raise SemanticEmbeddingActivationError(
            "semantic embedding activation mode is invalid"
        )
    if not _NAME_PATTERN.fullmatch(app_name):
        raise SemanticEmbeddingActivationError("application name is invalid")
    if stage not in VALID_STAGES:
        raise SemanticEmbeddingActivationError("stage is invalid")
    if not _NAME_PATTERN.fullmatch(region):
        raise SemanticEmbeddingActivationError("AWS region is invalid")
    if not _ACCOUNT_PATTERN.fullmatch(account_id):
        raise SemanticEmbeddingActivationError("AWS account is invalid")


def _validate_caller(caller_identity: object, account_id: str) -> str:
    caller = _mapping(caller_identity, "AWS caller identity")
    arn = caller.get("Arn")
    if caller.get("Account") != account_id or not isinstance(arn, str):
        raise SemanticEmbeddingActivationError("AWS caller identity is invalid")
    match = re.fullmatch(
        rf"arn:([a-z0-9-]+):(iam|sts)::{account_id}:.+",
        arn,
    )
    if match is None:
        raise SemanticEmbeddingActivationError("AWS caller identity is invalid")
    return match.group(1)


def _validate_mapping(
    document: object,
    *,
    stream_arn: str,
    worker_arn: str,
    dlq_arn: str,
) -> Mapping[str, object]:
    mappings = _mapping(
        document, "semantic embedding mappings"
    ).get("EventSourceMappings")
    if not isinstance(mappings, list) or len(mappings) != 1:
        raise SemanticEmbeddingActivationError(
            "semantic embedding mapping count is invalid"
        )
    mapping = _mapping(mappings[0], "semantic embedding mapping")
    uuid = mapping.get("UUID")
    if not isinstance(uuid, str) or not uuid.strip():
        raise SemanticEmbeddingActivationError(
            "semantic embedding mapping identity is invalid"
        )
    expected = {
        "EventSourceArn": stream_arn,
        "FunctionArn": worker_arn,
        "StartingPosition": "LATEST",
        "BatchSize": 10,
        "MaximumBatchingWindowInSeconds": 1,
        "ParallelizationFactor": 1,
        "BisectBatchOnFunctionError": True,
        "MaximumRetryAttempts": 5,
        "MaximumRecordAgeInSeconds": 3600,
        "FunctionResponseTypes": ["ReportBatchItemFailures"],
        "DestinationConfig": {"OnFailure": {"Destination": dlq_arn}},
    }
    if any(mapping.get(key) != value for key, value in expected.items()):
        raise SemanticEmbeddingActivationError(
            "semantic embedding mapping configuration is invalid"
        )
    if mapping.get("State") not in {"Enabled", "Disabled"}:
        raise SemanticEmbeddingActivationError(
            "semantic embedding mapping state is not stable"
        )
    return mapping


def _validate_alarms(document: object, worker_name: str, dlq_name: str) -> None:
    alarms = _mapping(document, "semantic embedding alarms").get("MetricAlarms")
    if not isinstance(alarms, list):
        raise SemanticEmbeddingActivationError(
            "semantic embedding alarms are invalid"
        )
    expected = {
        f"{worker_name}-errors": (
            "AWS/Lambda", "Errors", "Sum", 1.0, "FunctionName", worker_name
        ),
        f"{worker_name}-throttles": (
            "AWS/Lambda", "Throttles", "Sum", 1.0, "FunctionName", worker_name
        ),
        f"{worker_name}-iterator-age": (
            "AWS/Lambda",
            "IteratorAge",
            "Maximum",
            300000.0,
            "FunctionName",
            worker_name,
        ),
        f"{worker_name}-record-failures": (
            "JournalM8/SemanticEmbedding",
            "RecordFailures",
            "Sum",
            1.0,
            "FunctionName",
            worker_name,
        ),
        f"{dlq_name}-visible-messages": (
            "AWS/SQS",
            "ApproximateNumberOfMessagesVisible",
            "Maximum",
            1.0,
            "QueueName",
            dlq_name,
        ),
    }
    by_name = {}
    for value in alarms:
        alarm = _mapping(value, "semantic embedding alarm")
        name = alarm.get("AlarmName")
        if not isinstance(name, str) or name in by_name:
            raise SemanticEmbeddingActivationError(
                "semantic embedding alarms are invalid"
            )
        by_name[name] = alarm
    if set(by_name) != set(expected):
        raise SemanticEmbeddingActivationError(
            "semantic embedding alarms are incomplete"
        )
    for name, specification in expected.items():
        (
            namespace,
            metric,
            statistic,
            threshold,
            dimension_name,
            dimension_value,
        ) = specification
        alarm = by_name[name]
        if any((
            alarm.get("Namespace") != namespace,
            alarm.get("MetricName") != metric,
            alarm.get("Statistic") != statistic,
            alarm.get("Period") != 300,
            alarm.get("EvaluationPeriods") != 1,
            alarm.get("Threshold") != threshold,
            alarm.get("ComparisonOperator") != "GreaterThanOrEqualToThreshold",
            alarm.get("TreatMissingData") != "notBreaching",
            alarm.get("StateValue") not in {"OK", "INSUFFICIENT_DATA"},
            alarm.get("Dimensions") != [{
                "Name": dimension_name,
                "Value": dimension_value,
            }],
        )):
            raise SemanticEmbeddingActivationError(
                "semantic embedding alarm configuration is invalid"
            )


def _policy_document(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(unquote(value))
        except json.JSONDecodeError:
            raise SemanticEmbeddingActivationError(
                "semantic embedding worker policy is malformed"
            ) from None
    return value


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticEmbeddingActivationError(f"{label} is malformed")
    return value


def _load(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SemanticEmbeddingActivationError(f"{label} is malformed") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "verify"))
    parser.add_argument("--caller", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--concurrency", type=Path, required=True)
    parser.add_argument("--worker-policy", type=Path, required=True)
    parser.add_argument("--mappings", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--alarms", type=Path, required=True)
    parser.add_argument("--evaluation-report", type=Path)
    parser.add_argument("--target-state", choices=sorted(VALID_TARGET_STATES), required=True)
    parser.add_argument("--activation-mode", choices=sorted(VALID_ACTIVATION_MODES), required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--stage", choices=sorted(VALID_STAGES), required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--account", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = (
            _load(args.evaluation_report, "semantic retrieval evaluation report")
            if args.evaluation_report is not None
            else None
        )
        action = validate_activation_readiness(
            caller_identity=_load(args.caller, "AWS caller identity"),
            table_document=_load(args.table, "semantic table description"),
            worker_configuration=_load(args.worker, "semantic embedding worker configuration"),
            worker_concurrency=_load(args.concurrency, "semantic embedding worker concurrency"),
            worker_policy_response=_load(args.worker_policy, "semantic embedding worker policy"),
            mappings_document=_load(args.mappings, "semantic embedding mappings"),
            queue_attributes=_load(args.queue, "semantic embedding DLQ"),
            alarms_document=_load(args.alarms, "semantic embedding alarms"),
            evaluation_report=report,
            target_state=args.target_state,
            activation_mode=args.activation_mode,
            app_name=args.app_name,
            stage=args.stage,
            region=args.region,
            account_id=args.account,
        )
        if args.command == "verify" and action != "NOOP":
            raise SemanticEmbeddingActivationError(
                "semantic embedding target state was not reached"
            )
    except SemanticEmbeddingActivationError:
        print("Semantic embedding activation readiness failed.", file=sys.stderr)
        return 1
    print(action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
