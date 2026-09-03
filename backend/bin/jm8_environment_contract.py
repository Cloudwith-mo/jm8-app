#!/usr/bin/env python3
"""
JM8 Environment Validation and Deployment Guard

Enforces environment contracts and deployment safety across all mutating scripts.
Used at the start of every script that modifies AWS resources.

Environment Contract:
- APP_NAME must equal journalm8
- STAGE must be exactly dev, staging, or prod
- AWS_REGION must be explicitly set
- AWS_PROFILE must be jm8-dev for dev/staging and jm8-prod for prod
- EXPECTED_AWS_ACCOUNT_ID must be explicitly configured and match actual STS account
- dev/staging/prod currently require account 114743615542
- prod requires AWS_PROFILE=jm8-prod and explicit same-account isolation acknowledgment
- TABLE_NAME must match ${APP_NAME}-${STAGE}-main
- RAW_BUCKET must match ${APP_NAME}-${STAGE}-raw-${ACCOUNT_ID}
- prod FRONTEND_BUCKET must match ${APP_NAME}-prod-frontend-${ACCOUNT_ID}
- API_NAME and LAMBDA_FUNCTION_NAME must identify the exact stage API when configured
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
CANONICAL_AWS_PROFILE_BY_STAGE = {
    "dev": "jm8-dev",
    "staging": "jm8-dev",
    "prod": "jm8-prod",
}
PRODUCTION_AWS_PROFILE = CANONICAL_AWS_PROFILE_BY_STAGE["prod"]
PRODUCTION_ISOLATION_MODE = "stage-scoped-same-account"
STRIPE_BOOTSTRAP_MODE = "pre-webhook"
PRODUCTION_API_NAME = "journalm8-prod-api"
FRONTEND_STACK_COMPLETE_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
}
PRODUCTION_FORBIDDEN_DEMO_VARIABLES = {
    "AUTH_BYPASS",
    "DEMO_MODE",
    "DEMO_USER_ID",
    "JM8_DEMO_MODE",
    "MOCK_API",
    "VITE_AUTH_BYPASS",
    "VITE_DEMO_MODE",
    "VITE_DEMO_USER_ID",
    "VITE_MOCK_API",
}

PRODUCTION_SCOPED_REFERENCE_KEYS = {
    "TABLE_NAME",
    "RAW_BUCKET",
    "EXPORT_BUCKET",
    "FRONTEND_BUCKET",
    "API_NAME",
    "LAMBDA_FUNCTION_NAME",
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


def validate_stage_aws_profile(stage: str, aws_profile: str) -> None:
    """Require the repository's canonical deployment profile for each stage."""
    expected_profile = CANONICAL_AWS_PROFILE_BY_STAGE.get(stage)
    if expected_profile is None:
        raise EnvironmentContractError("Cannot validate AWS_PROFILE for invalid STAGE")
    if aws_profile != expected_profile:
        raise EnvironmentContractError(
            f"STAGE={stage} requires AWS_PROFILE={expected_profile}"
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


def validate_production_demo_controls(
    stage: str,
    environment: Mapping[str, str],
) -> None:
    """Reject production demo identities and authentication bypass controls."""
    if stage != "prod":
        return

    if any(name in environment for name in PRODUCTION_FORBIDDEN_DEMO_VARIABLES):
        raise EnvironmentContractError(
            "Production must not define demo-mode or demo-identity variables"
        )

    if (
        "VITE_COGNITO_ENABLED" in environment
        and str(environment.get("VITE_COGNITO_ENABLED") or "").strip()
        != "true"
    ):
        raise EnvironmentContractError(
            "Production VITE_COGNITO_ENABLED must equal true when set"
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


def validate_shared_api_names(
    app_name: str,
    stage: str,
    environment: Mapping[str, str],
) -> None:
    """Keep shared API identity separate from specialized worker targets."""
    expected_name = f"{app_name}-{stage}-api"
    for key in ("API_NAME", "LAMBDA_FUNCTION_NAME"):
        value = str(environment.get(key) or "").strip()
        if value and value != expected_name:
            raise EnvironmentContractError(
                f"{key} must be '{expected_name}' for STAGE={stage}"
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


def validate_export_bucket_name(
    app_name: str,
    stage: str,
    export_bucket: str,
    account_id: str,
    raw_bucket: str,
    frontend_bucket: str = "",
) -> None:
    """Validate the dedicated, stage-scoped account-export bucket."""
    if not export_bucket:
        raise EnvironmentContractError("EXPORT_BUCKET is required")
    expected_bucket = f"{app_name}-{stage}-exports-{account_id}"
    if export_bucket != expected_bucket:
        raise EnvironmentContractError(
            f"EXPORT_BUCKET must be '{expected_bucket}', got '{export_bucket}'"
        )
    if export_bucket in {raw_bucket, frontend_bucket}:
        raise EnvironmentContractError(
            "EXPORT_BUCKET must be distinct from RAW_BUCKET and FRONTEND_BUCKET"
        )


def validate_account_deletion_targets(
    app_name: str,
    stage: str,
    region: str,
    account_id: str,
    environment: Mapping[str, str],
    *,
    require_workflows: bool,
) -> None:
    """Reject cross-environment resources before deletion deployment can run."""
    pool_name = str(environment.get("COGNITO_USER_POOL_NAME") or "").strip()
    pool_id = str(environment.get("COGNITO_USER_POOL_ID") or "").strip()
    if pool_name != f"{app_name}-{stage}-users":
        raise EnvironmentContractError(
            "COGNITO_USER_POOL_NAME must identify the exact deployment environment"
        )
    if not re.fullmatch(re.escape(region) + r"_[A-Za-z0-9]+", pool_id):
        raise EnvironmentContractError(
            "COGNITO_USER_POOL_ID must identify a pool in the deployment region"
        )

    issuer = str(environment.get("COGNITO_ISSUER") or "").strip()
    expected_issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    if issuer and issuer != expected_issuer:
        raise EnvironmentContractError(
            "COGNITO_ISSUER must identify the exact configured user pool"
        )

    secret_arn = str(environment.get("STRIPE_SECRET_ARN") or "").strip()
    secret_pattern = re.compile(
        rf"^arn:aws:secretsmanager:{re.escape(region)}:{re.escape(account_id)}:"
        rf"secret:{re.escape(app_name)}/{re.escape(stage)}/stripe-[A-Za-z0-9]{{6,}}$"
    )
    if secret_pattern.fullmatch(secret_arn) is None:
        raise EnvironmentContractError(
            "STRIPE_SECRET_ARN must identify the exact deployment environment"
        )

    if not require_workflows:
        return
    workflow_names = {
        "OCR_WORKFLOW_ARN": f"{app_name}-{stage}-ocr-workflow",
        "HISTORICAL_REANALYSIS_WORKFLOW_ARN": (
            f"{app_name}-{stage}-historical-reanalysis-workflow"
        ),
        "ACCOUNT_EXPORT_WORKFLOW_ARN": f"{app_name}-{stage}-account-export-workflow",
    }
    for key, workflow_name in workflow_names.items():
        expected = (
            f"arn:aws:states:{region}:{account_id}:stateMachine:{workflow_name}"
        )
        if str(environment.get(key) or "").strip() != expected:
            raise EnvironmentContractError(
                f"{key} must identify the exact deployment environment"
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


def production_api_exists(profile: str, region: str) -> bool:
    """Return whether the exact production API exists, failing closed."""
    try:
        result = subprocess.run(
            [
                "aws", "apigatewayv2", "get-apis",
                "--profile", profile,
                "--region", region,
                "--output", "json",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise EnvironmentContractError(
            "Unable to verify production API absence for Stripe bootstrap"
        ) from error

    if result.returncode != 0:
        raise EnvironmentContractError(
            "Unable to verify production API absence for Stripe bootstrap"
        )

    try:
        document = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise EnvironmentContractError(
            "Production API inventory response is malformed"
        ) from error

    items = document.get("Items") if isinstance(document, dict) else None
    if not isinstance(items, list) or not all(
        isinstance(item, dict) and isinstance(item.get("Name"), str)
        for item in items
    ):
        raise EnvironmentContractError(
            "Production API inventory response is malformed"
        )

    return any(item["Name"] == PRODUCTION_API_NAME for item in items)


def validate_https_frontend_origin(origin: str) -> str:
    """Validate the strict HTTPS-origin syntax used by frontend stack outputs."""
    if not origin or origin != origin.strip() or any(
        character.isspace() for character in origin
    ):
        raise EnvironmentContractError(
            "FrontendOrigin must be a non-empty HTTPS origin"
        )

    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise EnvironmentContractError(
            "FrontendOrigin must be a valid HTTPS origin"
        )
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise EnvironmentContractError(
            "FrontendOrigin must not contain credentials"
        )
    if parsed.path or parsed.query or parsed.fragment:
        raise EnvironmentContractError(
            "FrontendOrigin must not contain a path, query, or fragment"
        )
    if "*" in parsed.hostname:
        raise EnvironmentContractError(
            "FrontendOrigin must not contain a wildcard host"
        )
    hostname = parsed.hostname
    if len(hostname) > 253 or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise EnvironmentContractError(
            "FrontendOrigin must contain a valid DNS hostname"
        )

    try:
        port = parsed.port
    except ValueError as error:
        raise EnvironmentContractError(
            "FrontendOrigin contains an invalid port"
        ) from error
    if port not in {None, 443}:
        raise EnvironmentContractError(
            "FrontendOrigin must not contain a non-default port"
        )

    return origin


def resolve_stage_frontend_origin(
    app_name: str,
    stage: str,
    profile: str,
    region: str,
    account_id: str,
) -> str:
    """Resolve the exact stage frontend stack and return its validated origin."""
    stack_name = f"{app_name}-{stage}-frontend-hosting"
    try:
        result = subprocess.run(
            [
                "aws", "cloudformation", "describe-stacks",
                "--stack-name", stack_name,
                "--profile", profile,
                "--region", region,
                "--output", "json",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise EnvironmentContractError(
            "Unable to resolve the stage frontend-hosting stack"
        ) from error

    if result.returncode != 0:
        raise EnvironmentContractError(
            "Unable to resolve the stage frontend-hosting stack"
        )

    try:
        document = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise EnvironmentContractError(
            "Frontend-hosting stack response is malformed"
        ) from error

    stacks = document.get("Stacks") if isinstance(document, dict) else None
    if not isinstance(stacks, list) or len(stacks) != 1:
        raise EnvironmentContractError(
            "Exactly one stage frontend-hosting stack must be resolved"
        )

    stack = stacks[0]
    if not isinstance(stack, dict) or stack.get("StackName") != stack_name:
        raise EnvironmentContractError(
            "Frontend-hosting stack identity does not match the stage"
        )
    expected_stack_id_prefix = (
        f"arn:aws:cloudformation:{region}:{account_id}:stack/{stack_name}/"
    )
    stack_id = stack.get("StackId")
    if not isinstance(stack_id, str) or not stack_id.startswith(
        expected_stack_id_prefix
    ):
        raise EnvironmentContractError(
            "Frontend-hosting stack account or region does not match the stage"
        )
    if stack.get("StackStatus") not in FRONTEND_STACK_COMPLETE_STATUSES:
        raise EnvironmentContractError(
            "Frontend-hosting stack is not in a stable completed state"
        )

    outputs = stack.get("Outputs")
    if not isinstance(outputs, list):
        raise EnvironmentContractError(
            "Frontend-hosting stack outputs are missing"
        )
    origin_outputs = [
        output.get("OutputValue")
        for output in outputs
        if isinstance(output, dict)
        and output.get("OutputKey") == "FrontendOrigin"
    ]
    if len(origin_outputs) != 1 or not isinstance(origin_outputs[0], str):
        raise EnvironmentContractError(
            "Frontend-hosting stack must contain exactly one FrontendOrigin output"
        )
    return validate_https_frontend_origin(origin_outputs[0])


def validate_stage_frontend_origin_contract(
    stage: str,
    frontend_origin: str,
    allowed_origins_csv: str,
    expected_origin: str,
) -> None:
    """Bind non-development CORS to the exact frontend stack output."""
    if stage not in {"staging", "prod"}:
        return

    validate_https_frontend_origin(expected_origin)
    validate_https_frontend_origin(frontend_origin)
    if frontend_origin != expected_origin:
        raise EnvironmentContractError(
            "FRONTEND_ORIGIN does not match the stage frontend-hosting stack"
        )

    raw_origins = [item.strip() for item in allowed_origins_csv.split(",")]
    normalized_origins = validate_allowed_origins(stage, allowed_origins_csv)
    if raw_origins != [expected_origin] or normalized_origins != [expected_origin]:
        raise EnvironmentContractError(
            "ALLOWED_ORIGINS must contain only the stage frontend origin"
        )


def validate_stripe_bootstrap_context(
    operation: str,
    stage: str,
    actual_account_id: Optional[str],
) -> bool:
    """Validate the one-time production pre-webhook bootstrap state."""
    mode = os.environ.get("JM8_STRIPE_BOOTSTRAP_MODE", "").strip()
    if not mode:
        return False
    if mode != STRIPE_BOOTSTRAP_MODE:
        raise EnvironmentContractError(
            "JM8_STRIPE_BOOTSTRAP_MODE must equal pre-webhook when set"
        )

    op = operation.lower()
    if op not in {"deploy", "provision-stripe-secret"}:
        raise EnvironmentContractError(
            "Stripe pre-webhook bootstrap is permitted only for deploy or "
            "provision-stripe-secret"
        )

    required_values = {
        "STAGE": stage,
        "AWS_PROFILE": os.environ.get("AWS_PROFILE", "").strip(),
        "EXPECTED_AWS_ACCOUNT_ID": os.environ.get(
            "EXPECTED_AWS_ACCOUNT_ID", ""
        ).strip(),
        "DEPLOY_CONFIRMATION": os.environ.get("DEPLOY_CONFIRMATION", "").strip(),
        "PRODUCTION_ISOLATION_MODE": os.environ.get(
            "PRODUCTION_ISOLATION_MODE", ""
        ).strip(),
        "API_NAME": os.environ.get("API_NAME", "").strip(),
    }
    expected_values = {
        "STAGE": "prod",
        "AWS_PROFILE": PRODUCTION_AWS_PROFILE,
        "EXPECTED_AWS_ACCOUNT_ID": JM8_AWS_ACCOUNT_ID,
        "DEPLOY_CONFIRMATION": "prod",
        "PRODUCTION_ISOLATION_MODE": PRODUCTION_ISOLATION_MODE,
        "API_NAME": PRODUCTION_API_NAME,
    }
    for name, expected in expected_values.items():
        if required_values[name] != expected:
            raise EnvironmentContractError(
                f"Stripe pre-webhook bootstrap requires exact {name}"
            )

    if actual_account_id != JM8_AWS_ACCOUNT_ID:
        raise EnvironmentContractError(
            "Stripe pre-webhook bootstrap requires the approved AWS account"
        )
    if os.environ.get("API_ENDPOINT", "").strip():
        raise EnvironmentContractError(
            "Stripe pre-webhook bootstrap requires API_ENDPOINT to be unset"
        )

    price_id = os.environ.get("STRIPE_PRO_MONTHLY_PRICE_ID", "").strip()
    if not re.fullmatch(r"price_[A-Za-z0-9_]{6,}", price_id):
        raise EnvironmentContractError(
            "Stripe pre-webhook bootstrap requires a production price ID"
        )
    checkout_urls = tuple(
        os.environ.get(name, "").strip()
        for name in (
            "STRIPE_CHECKOUT_SUCCESS_URL",
            "STRIPE_CHECKOUT_CANCEL_URL",
            "STRIPE_PORTAL_RETURN_URL",
        )
    )
    if not all(checkout_urls):
        raise EnvironmentContractError(
            "Stripe pre-webhook bootstrap requires checkout and portal URLs"
        )
    validate_non_dev_urls(stage, *checkout_urls)

    app_name = os.environ.get("APP_NAME", "").strip()
    if f"{app_name}/{stage}/stripe" != "journalm8/prod/stripe":
        raise EnvironmentContractError(
            "Stripe pre-webhook bootstrap requires the production secret path"
        )

    if op == "provision-stripe-secret":
        secret_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        if not re.fullmatch(r"sk_live_[A-Za-z0-9_]{8,}", secret_key):
            raise EnvironmentContractError(
                "Stripe pre-webhook bootstrap requires a live-mode key"
            )
        if os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip():
            raise EnvironmentContractError(
                "Stripe pre-webhook bootstrap requires no webhook secret"
            )
    else:
        if os.environ.get("STRIPE_SECRET_KEY", "").strip() or os.environ.get(
            "STRIPE_WEBHOOK_SECRET", ""
        ).strip():
            raise EnvironmentContractError(
                "deploy must not receive raw Stripe credentials"
            )

    if production_api_exists(
        required_values["AWS_PROFILE"], os.environ.get("AWS_REGION", "").strip()
    ):
        raise EnvironmentContractError(
            "Stripe pre-webhook bootstrap is forbidden after production API creation"
        )
    return True


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
    validate_stage_aws_profile(stage, aws_profile)

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
        validate_production_demo_controls(stage, os.environ)

    # AWS validation
    actual_account_id = validate_aws_configuration(aws_region, aws_profile, expected_account_id)

    # Stage-account mapping
    validate_stage_account_mapping(stage, actual_account_id)

    # Resource naming
    validate_production_resource_references(stage, os.environ)
    validate_resource_names(app_name, stage, table_name, raw_bucket, actual_account_id)
    validate_shared_api_names(app_name, stage, os.environ)
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
        validate_operation_specific(operation, stage, actual_account_id)

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


def validate_operation_specific(
    operation: str,
    stage: str,
    actual_account_id: Optional[str] = None,
) -> None:
    """
    Perform operation-specific validation rules.

    Supported operations (set via JM8_OPERATION env var):
    - deploy: requires STRIPE_SECRET_ARN and forbids raw STRIPE_SECRET_KEY
    - provision-stripe-secret, setup-stripe-catalog: require STRIPE_SECRET_KEY
      and validate key mode according to stage
    - other operations: no special Stripe checks
    """
    op = operation.lower()
    stripe_bootstrap = validate_stripe_bootstrap_context(
        operation, stage, actual_account_id
    )

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

        validate_export_bucket_name(
            os.environ.get("APP_NAME", "").strip(),
            stage,
            os.environ.get("EXPORT_BUCKET", "").strip(),
            actual_account_id or os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip(),
            os.environ.get("RAW_BUCKET", "").strip(),
            os.environ.get("FRONTEND_BUCKET", "").strip(),
        )
        validate_account_deletion_targets(
            os.environ.get("APP_NAME", "").strip(),
            stage,
            os.environ.get("AWS_REGION", "").strip(),
            actual_account_id or os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip(),
            os.environ,
            require_workflows=False,
        )

    if op == "deploy-account-export":
        validate_export_bucket_name(
            os.environ.get("APP_NAME", "").strip(),
            stage,
            os.environ.get("EXPORT_BUCKET", "").strip(),
            actual_account_id or os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip(),
            os.environ.get("RAW_BUCKET", "").strip(),
            os.environ.get("FRONTEND_BUCKET", "").strip(),
        )

    if op == "deploy-account-deletion":
        validate_export_bucket_name(
            os.environ.get("APP_NAME", "").strip(),
            stage,
            os.environ.get("EXPORT_BUCKET", "").strip(),
            actual_account_id or os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip(),
            os.environ.get("RAW_BUCKET", "").strip(),
            os.environ.get("FRONTEND_BUCKET", "").strip(),
        )
        validate_account_deletion_targets(
            os.environ.get("APP_NAME", "").strip(),
            stage,
            os.environ.get("AWS_REGION", "").strip(),
            actual_account_id or os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip(),
            os.environ,
            require_workflows=True,
        )

    if op in {"provision-stripe-secret", "setup-stripe-catalog"}:
        stripe_secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        if not stripe_secret:
            raise EnvironmentContractError(
                f"{operation} requires STRIPE_SECRET_KEY to be set"
            )
        # Reuse existing validation logic for mode checks
        validate_stripe_credentials(stage, stripe_secret)
        if op == "provision-stripe-secret" and stage == "prod" and not stripe_bootstrap:
            webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
            if not re.fullmatch(r"whsec_[A-Za-z0-9_]{8,}", webhook_secret):
                raise EnvironmentContractError(
                    "Production requires STRIPE_WEBHOOK_SECRET"
                )

    if op == "create-auth":
        validate_stage_aws_profile(
            stage,
            os.environ.get("AWS_PROFILE", "").strip(),
        )
        validate_non_dev_urls(
            stage,
            os.environ.get("CALLBACK_URL", "").strip(),
            os.environ.get("LOGOUT_URL", "").strip(),
        )

    if op in {"create-api", "create-resources"}:
        allowed_origins_csv = os.environ.get("ALLOWED_ORIGINS", "").strip()
        validate_allowed_origins(stage, allowed_origins_csv)

        if op == "create-api" and stage in {"staging", "prod"}:
            expected_origin = resolve_stage_frontend_origin(
                os.environ.get("APP_NAME", "").strip(),
                stage,
                os.environ.get("AWS_PROFILE", "").strip(),
                os.environ.get("AWS_REGION", "").strip(),
                actual_account_id
                or os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip(),
            )
            validate_stage_frontend_origin_contract(
                stage,
                os.environ.get("FRONTEND_ORIGIN", "").strip(),
                allowed_origins_csv,
                expected_origin,
            )

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

        allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").strip()
        if not allowed_origins:
            raise EnvironmentContractError("deploy-frontend requires ALLOWED_ORIGINS")
        expected_origin = resolve_stage_frontend_origin(
            app_name,
            stage,
            os.environ.get("AWS_PROFILE", "").strip(),
            os.environ.get("AWS_REGION", "").strip(),
            actual_account_id or expected_account_id,
        )
        validate_stage_frontend_origin_contract(
            stage,
            frontend_origin,
            allowed_origins,
            expected_origin,
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
