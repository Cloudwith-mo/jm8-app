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
- dev/staging/prod currently require account 114743615542
- prod requires AWS_PROFILE=jm8-prod and explicit same-account isolation acknowledgment
- TABLE_NAME must match ${APP_NAME}-${STAGE}-main
- RAW_BUCKET must match ${APP_NAME}-${STAGE}-raw-${ACCOUNT_ID}
- prod FRONTEND_BUCKET must match ${APP_NAME}-prod-frontend-${ACCOUNT_ID}
- prod API_NAME must equal journalm8-prod-api when configured
- prod STRIPE_SECRET_ARN must identify only journalm8/prod/stripe in the
  expected region and account
- Non-dev URLs cannot contain localhost or 127.0.0.1
- Stripe credentials must be test mode (sk_test_*) for dev/staging
- Production requires live mode (sk_live_*) but secrets are never logged
- No secret values may appear in output

Confirmation Gates:
- dev: no confirmation required
- staging: requires DEPLOY_CONFIRMATION=staging environment variable
- prod: requires DEPLOY_CONFIRMATION=prod, AWS_PROFILE=jm8-prod, and
  PRODUCTION_ISOLATION_MODE=stage-scoped-same-account
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit


JM8_AWS_ACCOUNT_ID = "114743615542"
PRODUCTION_AWS_PROFILE = "jm8-prod"
PRODUCTION_ISOLATION_MODE = "stage-scoped-same-account"

PRODUCTION_SCOPED_REFERENCE_KEYS = {
    "TABLE_NAME",
    "RAW_BUCKET",
    "FRONTEND_BUCKET",
    "API_NAME",
    "API_ENDPOINT",
    "FRONTEND_ORIGIN",
    "ALLOWED_ORIGINS",
    "STRIPE_SECRET_ARN",
    "STRIPE_CHECKOUT_SUCCESS_URL",
    "STRIPE_CHECKOUT_CANCEL_URL",
    "STRIPE_PORTAL_RETURN_URL",
    "COGNITO_DOMAIN",
    "COGNITO_ISSUER",
    "CALLBACK_URL",
    "LOGOUT_URL",
}


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
    - dev/staging/prod: must be 114743615542
    - prod's additional isolation controls are validated separately
    """
    if stage in {"dev", "staging", "prod"}:
        if account_id != JM8_AWS_ACCOUNT_ID:
            raise EnvironmentContractError(
                f"STAGE={stage} requires AWS account {JM8_AWS_ACCOUNT_ID}, "
                f"but EXPECTED_AWS_ACCOUNT_ID={account_id}"
            )


def validate_production_isolation_controls(
    stage: str,
    aws_profile: str,
    expected_account_id: str,
    isolation_mode: str,
) -> None:
    """Require the owner-approved same-account production isolation controls."""
    if stage != "prod":
        return

    if aws_profile != PRODUCTION_AWS_PROFILE:
        raise EnvironmentContractError(
            "STAGE=prod requires AWS_PROFILE=jm8-prod"
        )

    if expected_account_id != JM8_AWS_ACCOUNT_ID:
        raise EnvironmentContractError(
            f"STAGE=prod requires EXPECTED_AWS_ACCOUNT_ID={JM8_AWS_ACCOUNT_ID}"
        )

    if isolation_mode != PRODUCTION_ISOLATION_MODE:
        raise EnvironmentContractError(
            "STAGE=prod requires PRODUCTION_ISOLATION_MODE="
            "stage-scoped-same-account"
        )


def validate_production_resource_references(
    stage: str,
    environment: Mapping[str, str],
) -> None:
    """Require production-scoped names and reject unrelated AWS resources."""
    if stage != "prod":
        return

    cross_stage_pattern = re.compile(
        r"(^|[./:_-])(dev|staging)([./:_-]|$)",
        re.IGNORECASE,
    )

    for key in sorted(PRODUCTION_SCOPED_REFERENCE_KEYS):
        value = str(environment.get(key) or "").strip()
        if value and cross_stage_pattern.search(value):
            raise EnvironmentContractError(
                f"{key} must not reference dev or staging resources for STAGE=prod"
            )

    api_name = str(environment.get("API_NAME") or "").strip()
    if api_name and api_name != "journalm8-prod-api":
        raise EnvironmentContractError(
            "API_NAME must be 'journalm8-prod-api' for STAGE=prod"
        )

    user_pool_name = str(
        environment.get("COGNITO_USER_POOL_NAME") or ""
    ).strip()
    if user_pool_name and user_pool_name != "journalm8-prod-users":
        raise EnvironmentContractError(
            "COGNITO_USER_POOL_NAME must be 'journalm8-prod-users' for STAGE=prod"
        )

    stripe_secret_arn = str(
        environment.get("STRIPE_SECRET_ARN") or ""
    ).strip()
    if stripe_secret_arn:
        expected_region = str(environment.get("AWS_REGION") or "").strip()
        expected_account = str(
            environment.get("EXPECTED_AWS_ACCOUNT_ID") or ""
        ).strip()
        secret_arn_pattern = re.compile(
            r"^arn:aws:secretsmanager:([^:]+):([0-9]{12}):"
            r"secret:journalm8/prod/stripe-[A-Za-z0-9]{6}$"
        )
        match = secret_arn_pattern.fullmatch(stripe_secret_arn)
        if (
            match is None
            or match.group(1) != expected_region
            or match.group(2) != expected_account
        ):
            raise EnvironmentContractError(
                "STRIPE_SECRET_ARN must identify journalm8/prod/stripe in the "
                "expected production region and account"
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


def validate_frontend_bucket_name(
    app_name: str,
    stage: str,
    frontend_bucket: str,
    account_id: str,
    raw_bucket: str,
) -> None:
    """Validate the stage-scoped frontend bucket naming contract."""
    if not frontend_bucket:
        raise EnvironmentContractError("FRONTEND_BUCKET is required")
    expected_bucket = f"{app_name}-{stage}-frontend-{account_id}"
    if frontend_bucket != expected_bucket:
        raise EnvironmentContractError(
            f"FRONTEND_BUCKET must be '{expected_bucket}', got '{frontend_bucket}'"
        )
    if frontend_bucket == raw_bucket:
        raise EnvironmentContractError(
            "FRONTEND_BUCKET must not equal RAW_BUCKET; use a dedicated frontend bucket"
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


def _normalize_origin(origin: str) -> str:
    parsed = urlsplit(origin)

    if parsed.scheme not in {"http", "https"}:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS must contain absolute origins with http/https schemes"
        )

    if not parsed.netloc:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS must contain origins with a host"
        )

    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS must not contain credentials"
        )

    if parsed.path not in {"", "/"}:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS must not contain paths"
        )

    if parsed.query or parsed.fragment:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS must not contain query strings or fragments"
        )

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS must contain valid hostnames"
        )

    if "*" in host:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS must not contain wildcard hosts"
        )

    try:
        port = parsed.port
    except ValueError:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS contains an origin with an invalid port"
        )

    if port is None:
        return f"{parsed.scheme}://{host}"

    return f"{parsed.scheme}://{host}:{port}"


def validate_allowed_origins(stage: str, allowed_origins_csv: str) -> list[str]:
    """
    Validate and normalize ALLOWED_ORIGINS.

    - CSV of absolute origins only
    - No paths/query/fragments/credentials/wildcards
    - staging/prod require https and must not use localhost/127.0.0.1
    - dev may include localhost/127.0.0.1 origins
    """
    if not allowed_origins_csv or not allowed_origins_csv.strip():
        raise EnvironmentContractError("ALLOWED_ORIGINS is required")

    normalized: list[str] = []
    seen: set[str] = set()

    for raw_item in allowed_origins_csv.split(","):
        item = raw_item.strip()
        if not item:
            continue

        origin = _normalize_origin(item)
        parsed = urlsplit(origin)
        host = (parsed.hostname or "").lower()

        if stage in {"staging", "prod"}:
            if parsed.scheme != "https":
                raise EnvironmentContractError(
                    "ALLOWED_ORIGINS must use https for STAGE=staging/prod"
                )

            if host in {"localhost", "127.0.0.1"}:
                raise EnvironmentContractError(
                    "ALLOWED_ORIGINS must not include localhost for STAGE=staging/prod"
                )

        if origin in seen:
            raise EnvironmentContractError(
                "ALLOWED_ORIGINS must not contain duplicate origins"
            )

        normalized.append(origin)
        seen.add(origin)

    if not normalized:
        raise EnvironmentContractError("ALLOWED_ORIGINS must contain at least one origin")

    return normalized


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
    frontend_bucket = os.environ.get("FRONTEND_BUCKET", "").strip()
    production_isolation_mode = os.environ.get(
        "PRODUCTION_ISOLATION_MODE",
        "",
    ).strip()

    # Basic validation
    validate_app_name(app_name)
    validate_stage(stage)

    # Production controls are checked before STS so an unapproved profile or
    # incomplete same-account acknowledgment cannot initiate even a read call.
    if stage == "prod":
        validate_confirmation_gate(stage)
        validate_production_isolation_controls(
            stage,
            aws_profile,
            expected_account_id,
            production_isolation_mode,
        )

    # AWS validation
    actual_account_id = validate_aws_configuration(aws_region, aws_profile, expected_account_id)

    # Stage-account mapping
    validate_stage_account_mapping(stage, actual_account_id)

    # Resource naming
    validate_production_resource_references(stage, os.environ)
    validate_resource_names(app_name, stage, table_name, raw_bucket, actual_account_id)
    if stage == "prod":
        validate_frontend_bucket_name(
            app_name,
            stage,
            frontend_bucket,
            actual_account_id,
            raw_bucket,
        )

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

    # Optional: validate ALLOWED_ORIGINS if provided
    allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if allowed_origins:
        validate_allowed_origins(stage, allowed_origins)

    # Confirmation gate
    if stage != "prod":
        validate_confirmation_gate(stage)

    return {
        "app_name": app_name,
        "stage": stage,
        "aws_region": aws_region,
        "aws_profile": aws_profile,
        "account_id": actual_account_id,
        "table_name": table_name,
        "raw_bucket": raw_bucket,
        "frontend_bucket": frontend_bucket,
        "production_isolation_mode": production_isolation_mode,
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

        validate_non_dev_urls(
            stage,
            os.environ.get("STRIPE_CHECKOUT_SUCCESS_URL", "").strip(),
            os.environ.get("STRIPE_CHECKOUT_CANCEL_URL", "").strip(),
            os.environ.get("STRIPE_PORTAL_RETURN_URL", "").strip(),
        )

    if op in {"provision-stripe-secret", "setup-stripe-catalog"}:
        stripe_secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        if not stripe_secret:
            raise EnvironmentContractError(
                f"{operation} requires STRIPE_SECRET_KEY to be set"
            )
        # Reuse existing validation logic for mode checks
        validate_stripe_credentials(stage, stripe_secret)

    if op == "create-auth":
        validate_non_dev_urls(
            stage,
            os.environ.get("CALLBACK_URL", "").strip(),
            os.environ.get("LOGOUT_URL", "").strip(),
        )

    if op in {"create-api", "create-resources"}:
        allowed_origins_csv = os.environ.get("ALLOWED_ORIGINS", "").strip()
        validate_allowed_origins(stage, allowed_origins_csv)

    if op == "create-frontend-hosting":
        if stage not in {"staging", "prod"}:
            raise EnvironmentContractError(
                "create-frontend-hosting supports only staging or prod"
            )

        app_name = os.environ.get("APP_NAME", "").strip()
        expected_account_id = os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip()
        raw_bucket = os.environ.get("RAW_BUCKET", "").strip()
        frontend_bucket = os.environ.get("FRONTEND_BUCKET", "").strip()

        if not frontend_bucket:
            raise EnvironmentContractError("create-frontend-hosting requires FRONTEND_BUCKET")
        if stage == "staging":
            basic_auth_user = os.environ.get(
                "STAGING_BASIC_AUTH_USERNAME", ""
            ).strip()
            basic_auth_password = os.environ.get(
                "STAGING_BASIC_AUTH_PASSWORD", ""
            ).strip()
            if not basic_auth_user or not basic_auth_password:
                raise EnvironmentContractError(
                    "create-frontend-hosting requires staging Basic Auth credentials"
                )

        validate_frontend_bucket_name(
            app_name,
            stage,
            frontend_bucket,
            expected_account_id,
            raw_bucket,
        )

    if op == "deploy-frontend":
        if stage not in {"staging", "prod"}:
            raise EnvironmentContractError(
                "deploy-frontend supports only staging or prod"
            )

        app_name = os.environ.get("APP_NAME", "").strip()
        expected_account_id = os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip()
        raw_bucket = os.environ.get("RAW_BUCKET", "").strip()
        frontend_bucket = os.environ.get("FRONTEND_BUCKET", "").strip()
        cloudfront_distribution_id = os.environ.get("CLOUDFRONT_DISTRIBUTION_ID", "").strip()
        frontend_origin = os.environ.get("FRONTEND_ORIGIN", "").strip()

        if not frontend_bucket:
            raise EnvironmentContractError("deploy-frontend requires FRONTEND_BUCKET")
        if not cloudfront_distribution_id:
            raise EnvironmentContractError("deploy-frontend requires CLOUDFRONT_DISTRIBUTION_ID")
        if not frontend_origin:
            raise EnvironmentContractError("deploy-frontend requires FRONTEND_ORIGIN")

        validate_frontend_bucket_name(
            app_name,
            stage,
            frontend_bucket,
            expected_account_id,
            raw_bucket,
        )

        parsed = urlsplit(frontend_origin)
        if parsed.scheme != "https":
            raise EnvironmentContractError(
                "FRONTEND_ORIGIN must use https for staging/prod"
            )
        if not parsed.netloc:
            raise EnvironmentContractError("FRONTEND_ORIGIN must contain a valid hostname")
        if parsed.path not in {"", "/"}:
            raise EnvironmentContractError("FRONTEND_ORIGIN must contain no path, query, or fragment")
        if parsed.query or parsed.fragment:
            raise EnvironmentContractError("FRONTEND_ORIGIN must contain no path, query, or fragment")

        allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").strip()
        if not allowed_origins:
            raise EnvironmentContractError("deploy-frontend requires ALLOWED_ORIGINS")
        normalized = validate_allowed_origins(stage, allowed_origins)
        count = sum(1 for value in normalized if value == frontend_origin)
        if count != 1:
            raise EnvironmentContractError(
                "FRONTEND_ORIGIN must appear exactly once in ALLOWED_ORIGINS"
            )

        if os.environ.get("STRIPE_SECRET_KEY", "").strip():
            # Intentionally ignored for this frontend-only deployment path.
            pass


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
            validate_environment_contract()
            # Exit silently on success (for use in shell scripts)
            sys.exit(0)
        except EnvironmentContractError as exc:
            print(f"ENVIRONMENT_CONTRACT_ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    elif command == "allowed-origins-json":
        try:
            stage = os.environ.get("STAGE", "").strip()
            validate_stage(stage)
            origins = validate_allowed_origins(
                stage,
                os.environ.get("ALLOWED_ORIGINS", "").strip(),
            )
            print(json.dumps(origins))
            sys.exit(0)
        except EnvironmentContractError as exc:
            print(f"ENVIRONMENT_CONTRACT_ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)
