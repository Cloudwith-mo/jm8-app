#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote


VALID_STAGES = {"dev", "staging", "prod"}
ARN_COMPONENT_PATTERN = re.compile(r"^[a-z0-9-]+$")
ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
SEMANTIC_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"{label} is missing or malformed.") from error


def _require_exact_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SystemExit(f"{label} is missing or malformed.")
    return value


def _validate_context(
    model_id: str,
    expected_partition: str,
    expected_region: str,
    expected_account: str,
    app_name: str,
    stage: str,
) -> None:
    _require_exact_string(model_id, "Bedrock inference profile identifier")
    if not ARN_COMPONENT_PATTERN.fullmatch(expected_partition):
        raise SystemExit("Expected AWS partition is malformed.")
    if not ARN_COMPONENT_PATTERN.fullmatch(expected_region):
        raise SystemExit("Expected AWS region is malformed.")
    if not ACCOUNT_PATTERN.fullmatch(expected_account):
        raise SystemExit("Expected AWS account is malformed.")
    if not ARN_COMPONENT_PATTERN.fullmatch(app_name):
        raise SystemExit("Application name is malformed.")
    if stage not in VALID_STAGES:
        raise SystemExit("Stage must be dev, staging, or prod.")


def _parse_arn(value: Any, label: str) -> dict[str, str]:
    arn = _require_exact_string(value, label)
    parts = arn.split(":", 5)
    if len(parts) != 6 or parts[0] != "arn":
        raise SystemExit(f"{label} is malformed.")

    partition, service, region, account, resource = parts[1:]
    if (
        not ARN_COMPONENT_PATTERN.fullmatch(partition)
        or not ARN_COMPONENT_PATTERN.fullmatch(service)
        or not ARN_COMPONENT_PATTERN.fullmatch(region)
        or not resource
        or any(character.isspace() for character in resource)
    ):
        raise SystemExit(f"{label} is malformed.")
    if account and not ACCOUNT_PATTERN.fullmatch(account):
        raise SystemExit(f"{label} has a malformed account.")

    return {
        "arn": arn,
        "partition": partition,
        "service": service,
        "region": region,
        "account": account,
        "resource": resource,
    }


def _validate_profile_arn(
    profile_arn: Any,
    model_id: str,
    expected_partition: str,
    expected_region: str,
    expected_account: str,
) -> str:
    parsed = _parse_arn(profile_arn, "Bedrock inference profile ARN")
    if parsed["partition"] != expected_partition:
        raise SystemExit("Bedrock inference profile partition is incorrect.")
    if parsed["service"] != "bedrock":
        raise SystemExit("Bedrock inference profile service is incorrect.")
    if parsed["region"] != expected_region:
        raise SystemExit("Bedrock inference profile region is incorrect.")
    if parsed["account"] != expected_account:
        raise SystemExit("Bedrock inference profile account is incorrect.")

    resource_parts = parsed["resource"].split("/", 1)
    if (
        len(resource_parts) != 2
        or resource_parts[0]
        not in {"inference-profile", "application-inference-profile"}
        or not resource_parts[1]
    ):
        raise SystemExit("Bedrock inference profile resource is malformed.")

    if model_id.startswith("arn:"):
        if parsed["arn"] != model_id:
            raise SystemExit("Bedrock inference profile ARN does not match the configured model.")
    elif resource_parts[1] != model_id:
        raise SystemExit("Bedrock inference profile ID does not match the configured model.")

    return parsed["arn"]


def _validate_destination_model_arn(
    model_arn: Any,
    expected_partition: str,
) -> str:
    parsed = _parse_arn(model_arn, "Bedrock destination-model ARN")
    if parsed["partition"] != expected_partition:
        raise SystemExit("Bedrock destination-model partition is incorrect.")
    if parsed["service"] != "bedrock":
        raise SystemExit("Bedrock destination-model service is incorrect.")
    if parsed["account"]:
        raise SystemExit("Customer-scoped Bedrock destination models are not allowed.")
    if not parsed["resource"].startswith("foundation-model/"):
        raise SystemExit("Bedrock destination-model resource is malformed.")
    if not parsed["resource"].removeprefix("foundation-model/"):
        raise SystemExit("Bedrock destination-model resource is malformed.")
    return parsed["arn"]


def _policy_document(
    profile_arn: str,
    model_arns: list[str],
    semantic_embedding_model_arn: str,
) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadJM8AnalysisInferenceProfile",
                "Effect": "Allow",
                "Action": ["bedrock:GetInferenceProfile"],
                "Resource": profile_arn,
            },
            {
                "Sid": "InvokeJM8AnalysisInferenceProfile",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": profile_arn,
            },
            {
                "Sid": "InvokeJM8AnalysisDestinationModels",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": model_arns,
                "Condition": {
                    "StringEquals": {
                        "bedrock:InferenceProfileArn": profile_arn,
                    },
                },
            },
            {
                "Sid": "InvokeJM8SemanticQueryEmbeddingModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": semantic_embedding_model_arn,
            },
        ],
    }


def build_policy(
    profile: Any,
    model_id: str,
    expected_partition: str,
    expected_region: str,
    expected_account: str,
    app_name: str,
    stage: str,
) -> dict[str, Any]:
    _validate_context(
        model_id,
        expected_partition,
        expected_region,
        expected_account,
        app_name,
        stage,
    )
    if not isinstance(profile, dict):
        raise SystemExit("Bedrock inference profile response is malformed.")

    profile_arn = _validate_profile_arn(
        profile.get("inferenceProfileArn"),
        model_id,
        expected_partition,
        expected_region,
        expected_account,
    )

    returned_profile_id = profile.get("inferenceProfileId")
    expected_profile_id = profile_arn.rsplit("/", 1)[1]
    if returned_profile_id is not None:
        returned_profile_id = _require_exact_string(
            returned_profile_id,
            "Bedrock inference profile ID",
        )
        if returned_profile_id != expected_profile_id:
            raise SystemExit("Bedrock inference profile response is inconsistent.")

    models = profile.get("models")
    if not isinstance(models, list) or not models:
        raise SystemExit("The inference profile returned no destination models.")

    model_arns = []
    for model in models:
        if not isinstance(model, dict):
            raise SystemExit("Bedrock destination-model response is malformed.")
        model_arns.append(
            _validate_destination_model_arn(
                model.get("modelArn"),
                expected_partition,
            )
        )

    if len(set(model_arns)) != len(model_arns):
        raise SystemExit("Bedrock inference profile returned duplicate destination models.")

    semantic_embedding_model_arn = (
        f"arn:{expected_partition}:bedrock:{expected_region}::"
        f"foundation-model/{SEMANTIC_EMBEDDING_MODEL_ID}"
    )
    _validate_destination_model_arn(
        semantic_embedding_model_arn,
        expected_partition,
    )

    return _policy_document(
        profile_arn,
        sorted(model_arns),
        semantic_embedding_model_arn,
    )


def _decode_policy_document(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(unquote(value))
        except json.JSONDecodeError as error:
            raise SystemExit("Applied Bedrock policy document is malformed.") from error
    if not isinstance(value, dict):
        raise SystemExit("Applied Bedrock policy document is malformed.")
    return value


def _validate_canonical_policy(
    policy: Any,
    model_id: str,
    expected_partition: str,
    expected_region: str,
    expected_account: str,
    app_name: str,
    stage: str,
) -> None:
    _validate_context(
        model_id,
        expected_partition,
        expected_region,
        expected_account,
        app_name,
        stage,
    )
    if not isinstance(policy, dict):
        raise SystemExit("Expected Bedrock policy is malformed.")
    statements = policy.get("Statement")
    if policy.get("Version") != "2012-10-17" or not isinstance(statements, list):
        raise SystemExit("Expected Bedrock policy is malformed.")

    by_sid = {}
    for statement in statements:
        if not isinstance(statement, dict):
            raise SystemExit("Expected Bedrock policy is malformed.")
        sid = statement.get("Sid")
        if not isinstance(sid, str) or sid in by_sid:
            raise SystemExit("Expected Bedrock policy is malformed.")
        by_sid[sid] = statement

    required_sids = {
        "ReadJM8AnalysisInferenceProfile",
        "InvokeJM8AnalysisInferenceProfile",
        "InvokeJM8AnalysisDestinationModels",
        "InvokeJM8SemanticQueryEmbeddingModel",
    }
    if set(by_sid) != required_sids:
        raise SystemExit("Expected Bedrock policy statements are incorrect.")

    read_statement = by_sid["ReadJM8AnalysisInferenceProfile"]
    invoke_statement = by_sid["InvokeJM8AnalysisInferenceProfile"]
    destination_statement = by_sid["InvokeJM8AnalysisDestinationModels"]
    semantic_statement = by_sid[
        "InvokeJM8SemanticQueryEmbeddingModel"
    ]
    profile_arn = _validate_profile_arn(
        read_statement.get("Resource"),
        model_id,
        expected_partition,
        expected_region,
        expected_account,
    )
    destination_resources = destination_statement.get("Resource")
    if not isinstance(destination_resources, list) or not destination_resources:
        raise SystemExit("Expected Bedrock destination resources are incorrect.")
    model_arns = [
        _validate_destination_model_arn(resource, expected_partition)
        for resource in destination_resources
    ]
    if len(set(model_arns)) != len(model_arns):
        raise SystemExit("Expected Bedrock destination resources contain duplicates.")

    semantic_model_arn = _validate_destination_model_arn(
        semantic_statement.get("Resource"),
        expected_partition,
    )
    expected_semantic_model_arn = (
        f"arn:{expected_partition}:bedrock:{expected_region}::"
        f"foundation-model/{SEMANTIC_EMBEDDING_MODEL_ID}"
    )
    if semantic_model_arn != expected_semantic_model_arn:
        raise SystemExit(
            "Expected semantic embedding model resource is incorrect."
        )

    canonical = _policy_document(
        profile_arn,
        sorted(model_arns),
        semantic_model_arn,
    )
    if policy != canonical:
        raise SystemExit("Expected Bedrock policy is not canonical least privilege.")
    if invoke_statement.get("Resource") != profile_arn:
        raise SystemExit("Expected Bedrock inference-profile resource is inconsistent.")


def verify_applied_policy(
    response: Any,
    expected_policy: Any,
    expected_role_name: str,
    expected_policy_name: str,
    model_id: str,
    expected_partition: str,
    expected_region: str,
    expected_account: str,
    app_name: str,
    stage: str,
) -> None:
    _validate_canonical_policy(
        expected_policy,
        model_id,
        expected_partition,
        expected_region,
        expected_account,
        app_name,
        stage,
    )
    if not isinstance(response, dict):
        raise SystemExit("Applied Bedrock policy response is malformed.")
    if response.get("RoleName") != expected_role_name:
        raise SystemExit("Applied Bedrock policy role is incorrect.")
    if response.get("PolicyName") != expected_policy_name:
        raise SystemExit("Applied Bedrock policy name is incorrect.")

    applied_policy = _decode_policy_document(response.get("PolicyDocument"))
    if applied_policy != expected_policy:
        raise SystemExit("Applied Bedrock policy does not match the expected policy.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    generate.add_argument("profile_response", type=Path)
    generate.add_argument("policy_output", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("applied_response", type=Path)
    verify.add_argument("expected_policy", type=Path)
    verify.add_argument("expected_role_name")
    verify.add_argument("expected_policy_name")

    for command_parser in (generate, verify):
        command_parser.add_argument("model_id")
        command_parser.add_argument("expected_partition")
        command_parser.add_argument("expected_region")
        command_parser.add_argument("expected_account")
        command_parser.add_argument("app_name")
        command_parser.add_argument("stage")

    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "generate":
        profile = _load_json(args.profile_response, "Bedrock inference profile response")
        policy = build_policy(
            profile,
            args.model_id,
            args.expected_partition,
            args.expected_region,
            args.expected_account,
            args.app_name,
            args.stage,
        )
        args.policy_output.write_text(json.dumps(policy, indent=2) + "\n")
        return

    applied_response = _load_json(
        args.applied_response,
        "Applied Bedrock policy response",
    )
    expected_policy = _load_json(
        args.expected_policy,
        "Expected Bedrock policy",
    )
    verify_applied_policy(
        applied_response,
        expected_policy,
        args.expected_role_name,
        args.expected_policy_name,
        args.model_id,
        args.expected_partition,
        args.expected_region,
        args.expected_account,
        args.app_name,
        args.stage,
    )


if __name__ == "__main__":
    main()
