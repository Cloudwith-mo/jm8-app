#!/usr/bin/env python3
"""
JM8 Environment Validation and Deployment Guard

Enforces environment contracts and deployment safety across all mutating scripts.
Used at the start of every script that modifies AWS resources.

Environment Contract:
- APP_NAME must equal journalm8
- STAGE must be exactly dev, staging, or prod
- AWS_REGION must be explicitly set
- AWS_PROFILE must be explicitly set
- EXPECTED_AWS_ACCOUNT_ID must be explicitly configured and match actual STS account
- dev/staging currently require account 114743615542
- prod must fail (no production account configured yet)
- TABLE_NAME must match ${APP_NAME}-${STAGE}-main
- RAW_BUCKET must match ${APP_NAME}-${STAGE}-raw-${ACCOUNT_ID}
- Non-dev URLs cannot contain localhost or 127.0.0.1
- Stripe credentials must be test mode (sk_test_*) for dev/staging
- Production requires live mode (sk_live_*) but secrets are never logged
- No secret values may appear in output

Confirmation Gates:
- dev: no confirmation required
- staging: requires DEPLOY_CONFIRMATION=staging environment variable
- prod: requires DEPLOY_CONFIRMATION=prod plus separate AWS account
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


class EnvironmentContractError(Exception):
    """Raised when environment contract is violated."""
    pass


def get_actual_aws_account_id(profile: str, region: str) -> Optional[str]:
    """
    Fetch actual AWS account ID from STS using AWS CLI.
    Returns None if STS call fails (account mismatch).
    Never logs or prints the account ID in error messages.
    """
    try:
        result = subprocess.run(
            [
                "aws", "sts", "get-caller-identity",
                "--profile", profile,
                "--region", region,
                "--query", "Account",
                "--output", "text",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            account_id = result.stdout.strip()
            if account_id and account_id.isdigit() and len(account_id) == 12:
                return account_id
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def validate_app_name(app_name: str) -> None:
    """APP_NAME must equal journalm8."""
    if app_name != "journalm8":
        raise EnvironmentContractError(
            f"APP_NAME must be 'journalm8', got '{app_name}'"
        )


def validate_stage(stage: str) -> None:
    """STAGE must be exactly dev, staging, or prod."""
    allowed_stages = {"dev", "staging", "prod"}
    if stage not in allowed_stages:
        raise EnvironmentContractError(
            f"STAGE must be one of {sorted(allowed_stages)}, got '{stage}'"
        )


def validate_aws_configuration(
    region: str, profile: str, expected_account_id: str
) -> str:
    """
    Validate AWS configuration and fetch actual account ID.
    Returns actual account ID if validation passes.
    Raises EnvironmentContractError if AWS credentials don't match.
    """
    if not region:
        raise EnvironmentContractError("AWS_REGION is required and must not be empty")
    if not profile:
        raise EnvironmentContractError("AWS_PROFILE is required and must not be empty")
    if not expected_account_id:
        raise EnvironmentContractError(
            "EXPECTED_AWS_ACCOUNT_ID is required and must not be empty"
        )
    if not (expected_account_id.isdigit() and len(expected_account_id) == 12):
        raise EnvironmentContractError(
            "EXPECTED_AWS_ACCOUNT_ID must be a 12-digit number"
        )

    actual_account_id = get_actual_aws_account_id(profile, region)
    if actual_account_id is None:
        raise EnvironmentContractError(
            f"Failed to fetch AWS account ID. Verify AWS_PROFILE={profile} "
            f"and AWS_REGION={region} are correct and credentials are valid."
        )

    if actual_account_id != expected_account_id:
        raise EnvironmentContractError(
            "AWS account ID mismatch: EXPECTED_AWS_ACCOUNT_ID does not match "
            "actual account. This is a critical safety check to prevent deploying "
            "to the wrong account."
        )

    return actual_account_id


def validate_stage_account_mapping(stage: str, account_id: str) -> None:
    """
    Validate that the stage and account match the deployment policy.
    - dev/staging: must be 114743615542
    - prod: must be a separate (not yet configured) account
    """
    if stage in {"dev", "staging"}:
        allowed_account = "114743615542"
        if account_id != allowed_account:
            raise EnvironmentContractError(
                f"STAGE={stage} requires AWS account {allowed_account}, "
                f"but EXPECTED_AWS_ACCOUNT_ID={account_id}"
            )
    elif stage == "prod":
        raise EnvironmentContractError(
            "STAGE=prod is not yet configured. Production requires a separate "
            "AWS account. Configure the production account ID in your deployment "
            "pipeline before proceeding."
        )


def validate_resource_names(
    app_name: str, stage: str, table_name: str, raw_bucket: str, account_id: str
) -> None:
    """
    Validate that TABLE_NAME and RAW_BUCKET match expected naming patterns.
    - TABLE_NAME: ${APP_NAME}-${STAGE}-main
    - RAW_BUCKET: ${APP_NAME}-${STAGE}-raw-${ACCOUNT_ID}
    """
    expected_table = f"{app_name}-{stage}-main"
    if table_name != expected_table:
        raise EnvironmentContractError(
            f"TABLE_NAME must be '{expected_table}', got '{table_name}'"
        )

    expected_bucket = f"{app_name}-{stage}-raw-{account_id}"
    if raw_bucket != expected_bucket:
        raise EnvironmentContractError(
            f"RAW_BUCKET must be '{expected_bucket}', got '{raw_bucket}'"
        )


def validate_non_dev_urls(stage: str, *urls: str) -> None:
    """
    For non-dev stages, URLs must not contain localhost or 127.0.0.1.
    For dev, URLs may contain localhost.
    """
    if stage == "dev":
        return  # dev allows localhost

    localhost_patterns = ["localhost", "127.0.0.1"]
    for url in urls:
        if url:
            url_lower = url.lower()
            for pattern in localhost_patterns:
                if pattern in url_lower:
                    raise EnvironmentContractError(
                        f"STAGE={stage} cannot use localhost URLs. "
                        f"Found localhost in: {url}"
                    )


def validate_stripe_credentials(
    stage: str,
    stripe_secret_key: Optional[str] = None,
    stripe_webhook_secret: Optional[str] = None,
) -> None:
    """
    Validate Stripe credentials without logging them.
    - dev/staging: must be test mode (sk_test_*)
    - prod: must be live mode (sk_live_*) but NEVER logged
    """
    if stripe_secret_key:
        if stage in {"dev", "staging"}:
            if not stripe_secret_key.startswith("sk_test_"):
                raise EnvironmentContractError(
                    f"STAGE={stage} requires test-mode Stripe secret (sk_test_*)"
                )
        elif stage == "prod":
            if not stripe_secret_key.startswith("sk_live_"):
                raise EnvironmentContractError(
                    f"STAGE={stage} requires live-mode Stripe secret (sk_live_*)"
                )


def validate_confirmation_gate(stage: str) -> None:
    """
    Enforce confirmation gates to prevent accidental deployments.
    - dev: no confirmation required
    - staging: requires DEPLOY_CONFIRMATION=staging
    - prod: requires DEPLOY_CONFIRMATION=prod
    """
    confirmation = os.environ.get("DEPLOY_CONFIRMATION", "").strip()

    if stage == "dev":
        return  # no confirmation required for dev

    if stage == "staging":
        if confirmation != "staging":
            raise EnvironmentContractError(
                "STAGE=staging requires DEPLOY_CONFIRMATION=staging environment variable"
            )
    elif stage == "prod":
        if confirmation != "prod":
            raise EnvironmentContractError(
                "STAGE=prod requires DEPLOY_CONFIRMATION=prod environment variable"
            )


def validate_environment_contract() -> dict:
    """
    Validate the complete environment contract.
    Returns a dict with validated configuration.
    Raises EnvironmentContractError if any validation fails.
    """
    app_name = os.environ.get("APP_NAME", "").strip()
    stage = os.environ.get("STAGE", "").strip()
    aws_region = os.environ.get("AWS_REGION", "").strip()
    aws_profile = os.environ.get("AWS_PROFILE", "").strip()
    expected_account_id = os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip()
    table_name = os.environ.get("TABLE_NAME", "").strip()
    raw_bucket = os.environ.get("RAW_BUCKET", "").strip()

    # Basic validation
    validate_app_name(app_name)
    validate_stage(stage)

    # AWS validation
    actual_account_id = validate_aws_configuration(aws_region, aws_profile, expected_account_id)

    # Stage-account mapping
    validate_stage_account_mapping(stage, actual_account_id)

    # Resource naming
    validate_resource_names(app_name, stage, table_name, raw_bucket, actual_account_id)

    # Optional: Stripe (if provided)
    stripe_secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if stripe_secret:
        validate_stripe_credentials(stage, stripe_secret)

    # Optional: ENV_NAME consistency check (some scripts still use ENV_NAME)
    env_name = os.environ.get("ENV_NAME", "").strip()
    if env_name:
        # Ensure any legacy ENV_NAME matches STAGE to avoid accidental cross-stage
        if env_name != stage:
            raise EnvironmentContractError(
                f"ENV_NAME ('{env_name}') must equal STAGE ('{stage}') when set"
            )

    # Optional: operation-specific validation via JM8_OPERATION env var
    operation = os.environ.get("JM8_OPERATION", "").strip()
    if operation:
        validate_operation_specific(operation, stage)

    # Confirmation gate
    validate_confirmation_gate(stage)

    return {
        "app_name": app_name,
        "stage": stage,
        "aws_region": aws_region,
        "aws_profile": aws_profile,
        "account_id": actual_account_id,
        "table_name": table_name,
        "raw_bucket": raw_bucket,
    }


def validate_operation_specific(operation: str, stage: str) -> None:
    """
    Perform operation-specific validation rules.

    Supported operations (set via JM8_OPERATION env var):
    - deploy: requires STRIPE_SECRET_ARN and forbids raw STRIPE_SECRET_KEY
    - provision-stripe-secret, setup-stripe-catalog: require STRIPE_SECRET_KEY
      and validate key mode according to stage
    - other operations: no special Stripe checks
    """
    op = operation.lower()

    if op == "deploy":
        stripe_secret_arn = os.environ.get("STRIPE_SECRET_ARN", "").strip()
        if not stripe_secret_arn:
            raise EnvironmentContractError(
                "deploy operation requires STRIPE_SECRET_ARN to be set"
            )

        if os.environ.get("STRIPE_SECRET_KEY", "").strip():
            raise EnvironmentContractError(
                "deploy operation must not use plaintext STRIPE_SECRET_KEY; use STRIPE_SECRET_ARN"
            )

    if op in {"provision-stripe-secret", "setup-stripe-catalog"}:
        stripe_secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        if not stripe_secret:
            raise EnvironmentContractError(
                f"{operation} requires STRIPE_SECRET_KEY to be set"
            )
        # Reuse existing validation logic for mode checks
        validate_stripe_credentials(stage, stripe_secret)


def main(argv: list[str]) -> None:
    """
    CLI entry point. Usage: python3 jm8_environment_contract.py validate

    Exits with status 0 if validation passes.
    Exits with status 1 if validation fails (prints error to stderr).
    """
    if len(argv) < 2:
        print("Usage: python3 jm8_environment_contract.py validate", file=sys.stderr)
        sys.exit(1)

    command = argv[1]

    if command == "validate":
        try:
            config = validate_environment_contract()
            # Exit silently on success (for use in shell scripts)
            sys.exit(0)
        except EnvironmentContractError as exc:
            print(f"ENVIRONMENT_CONTRACT_ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)
