#!/usr/bin/env python3
"""Validate JM8 resource identities and canonical deployment tags."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


PRODUCTION_ACCOUNT_ID = "114743615542"
CANONICAL_APP_NAME = "journalm8"
VALID_STAGES = {"dev", "staging", "prod"}


class TagContractError(RuntimeError):
    """Raised when a resource or tag response violates the contract."""


def _context(app_name: str, stage: str, account_id: str, region: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", app_name):
        raise TagContractError("Application name is malformed.")
    if app_name != CANONICAL_APP_NAME:
        raise TagContractError("Application name is not approved.")
    if stage not in VALID_STAGES:
        raise TagContractError("Deployment stage is invalid.")
    if not re.fullmatch(r"[0-9]{12}", account_id):
        raise TagContractError("AWS account ID is malformed.")
    if not re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-[0-9]", region):
        raise TagContractError("AWS region is malformed.")
    if stage == "prod" and (
        account_id != PRODUCTION_ACCOUNT_ID
    ):
        raise TagContractError("Production tag context is not approved.")


def _load_object() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        raise TagContractError("AWS response is malformed JSON.") from error
    if not isinstance(value, dict):
        raise TagContractError("AWS response is malformed.")
    return value


def _tag_map(value: dict[str, Any]) -> dict[str, str]:
    tags = value.get("Tags")
    if not isinstance(tags, dict):
        raise TagContractError("AWS tag response is malformed.")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in tags.items()
    ):
        raise TagContractError("AWS tag response is malformed.")
    return tags


def verify_tags(
    value: dict[str, Any],
    app_name: str,
    stage: str,
    *,
    before_reconcile: bool,
) -> None:
    tags = _tag_map(value)
    expected = {
        "App": app_name,
        "Stage": stage,
        "ManagedBy": "aws-cli",
    }
    if before_reconcile:
        for key in ("App", "Stage"):
            if key in tags and tags[key] != expected[key]:
                raise TagContractError("Resource belongs to a different tag contract.")
        return
    if any(tags.get(key) != item for key, item in expected.items()):
        raise TagContractError("Required resource tags were not applied exactly.")


def verify_exact_tags(
    value: dict[str, Any],
    app_name: str,
    stage: str,
) -> None:
    tags = _tag_map(value)
    expected = {
        "App": app_name,
        "Stage": stage,
        "ManagedBy": "aws-cli",
    }
    if tags != expected:
        raise TagContractError("Resource tags do not exactly match the contract.")


def resolve_api(value: dict[str, Any], expected_name: str) -> str:
    items = value.get("Items")
    if not isinstance(items, list) or value.get("NextToken"):
        raise TagContractError("HTTP API inventory response is malformed or incomplete.")
    matches = [
        item
        for item in items
        if isinstance(item, dict) and item.get("Name") == expected_name
    ]
    if len(matches) > 1:
        raise TagContractError("Expected HTTP API name is duplicated.")
    if not matches:
        return ""
    api_id = matches[0].get("ApiId")
    if not isinstance(api_id, str) or not re.fullmatch(r"[a-z0-9]{10}", api_id):
        raise TagContractError("HTTP API identifier is malformed.")
    return api_id


def validate_api(
    value: dict[str, Any],
    api_id: str,
    expected_name: str,
) -> None:
    if not re.fullmatch(r"[a-z0-9]{10}", api_id):
        raise TagContractError("HTTP API identifier is malformed.")
    if (
        value.get("ApiId") != api_id
        or value.get("Name") != expected_name
        or value.get("ProtocolType") != "HTTP"
    ):
        raise TagContractError("HTTP API identity does not match the deployment contract.")


def resolve_user_pool(value: dict[str, Any], expected_name: str, region: str) -> str:
    items = value.get("UserPools")
    if not isinstance(items, list) or value.get("PaginationToken"):
        raise TagContractError("Cognito user-pool inventory is malformed or incomplete.")
    matches = [
        item
        for item in items
        if isinstance(item, dict) and item.get("Name") == expected_name
    ]
    if len(matches) > 1:
        raise TagContractError("Expected Cognito user-pool name is duplicated.")
    if not matches:
        return ""
    pool_id = matches[0].get("Id")
    if not isinstance(pool_id, str) or not re.fullmatch(
        rf"{re.escape(region)}_[A-Za-z0-9]+",
        pool_id,
    ):
        raise TagContractError("Cognito user-pool identifier is malformed.")
    return pool_id


def validate_user_pool(
    value: dict[str, Any],
    pool_id: str,
    expected_name: str,
    account_id: str,
    region: str,
) -> str:
    pool = value.get("UserPool")
    if not isinstance(pool, dict):
        raise TagContractError("Cognito user-pool response is malformed.")
    expected_arn = f"arn:aws:cognito-idp:{region}:{account_id}:userpool/{pool_id}"
    if (
        pool.get("Id") != pool_id
        or pool.get("Name") != expected_name
        or pool.get("Arn") != expected_arn
    ):
        raise TagContractError(
            "Cognito user-pool identity does not match the deployment contract."
        )
    return expected_arn


def validate_lambda(
    value: dict[str, Any],
    function_name: str,
    account_id: str,
    region: str,
) -> str:
    configuration = value.get("Configuration")
    if not isinstance(configuration, dict):
        raise TagContractError("Lambda response is malformed.")
    expected_arn = f"arn:aws:lambda:{region}:{account_id}:function:{function_name}"
    if (
        configuration.get("FunctionName") != function_name
        or configuration.get("FunctionArn") != expected_arn
    ):
        raise TagContractError("Lambda identity does not match the deployment contract.")
    return expected_arn


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "resolve-api",
            "validate-api",
            "resolve-user-pool",
            "validate-user-pool",
            "validate-lambda",
            "verify-tags",
            "verify-tags-before-reconcile",
            "verify-exact-tags",
        ),
    )
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-name")
    parser.add_argument("--resource-id")
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        _context(args.app_name, args.stage, args.account_id, args.region)
        value = _load_object()
        if args.command == "resolve-api":
            if not args.expected_name:
                raise TagContractError("Expected HTTP API name is required.")
            print(resolve_api(value, args.expected_name))
        elif args.command == "validate-api":
            if not args.expected_name or not args.resource_id:
                raise TagContractError("Expected HTTP API identity is required.")
            validate_api(value, args.resource_id, args.expected_name)
        elif args.command == "resolve-user-pool":
            if not args.expected_name:
                raise TagContractError("Expected Cognito user-pool name is required.")
            print(resolve_user_pool(value, args.expected_name, args.region))
        elif args.command == "validate-user-pool":
            if not args.expected_name or not args.resource_id:
                raise TagContractError("Expected Cognito user-pool identity is required.")
            print(validate_user_pool(
                value,
                args.resource_id,
                args.expected_name,
                args.account_id,
                args.region,
            ))
        elif args.command == "validate-lambda":
            if not args.expected_name:
                raise TagContractError("Expected Lambda name is required.")
            print(validate_lambda(
                value,
                args.expected_name,
                args.account_id,
                args.region,
            ))
        elif args.command == "verify-exact-tags":
            verify_exact_tags(value, args.app_name, args.stage)
        else:
            verify_tags(
                value,
                args.app_name,
                args.stage,
                before_reconcile=(
                    args.command == "verify-tags-before-reconcile"
                ),
            )
    except TagContractError as error:
        print(f"TAG_CONTRACT_ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
