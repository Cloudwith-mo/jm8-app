#!/usr/bin/env python3
"""Generate, reconcile, and verify JM8 production deployer managed policies."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


ACCOUNT_ID = "114743615542"
APP_NAME = "journalm8"
STAGE = "prod"
PROVISIONING_PROFILE = "jm8-dev"
DEPLOYER_ROLE_NAME = "journalm8-prod-deployer"
DEPLOYER_ROLE_ARN = (
    "arn:aws:iam::114743615542:role/journalm8-prod-deployer"
)
POLICY_PATH = "/journalm8/prod/deployer/"
POLICY_SIZE_LIMIT = 6_144
POLICY_NAMES = (
    "journalm8-prod-deployer-foundation",
    "journalm8-prod-deployer-compute",
    "journalm8-prod-deployer-iam",
    "journalm8-prod-deployer-frontend",
    "journalm8-prod-deployer-observability",
    "journalm8-prod-deployer-secrets",
)
FORBIDDEN_MANAGED_POLICY_NAMES = {
    "AdministratorAccess",
    "PowerUserAccess",
    "IAMFullAccess",
}
SAFE_AWS_ERROR_CODES = {
    "AccessDenied",
    "AccessDeniedException",
    "InvalidClientTokenId",
    "NoSuchEntity",
    "NoSuchEntityException",
    "RequestExpired",
    "Throttling",
    "ThrottlingException",
    "UnrecognizedClientException",
    "ValidationError",
    "ValidationException",
}
AWS_LAMBDA_BASIC_POLICY_ARN = (
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
)
BEDROCK_PROFILE_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
LOG_DELIVERY_CONTROL_PLANE_ACTIONS = {
    "logs:CreateLogDelivery",
    "logs:DeleteLogDelivery",
    "logs:DescribeLogGroups",
    "logs:DescribeResourcePolicies",
    "logs:GetLogDelivery",
    "logs:ListLogDeliveries",
    "logs:PutResourcePolicy",
    "logs:UpdateLogDelivery",
}
RESOURCE_STAR_ACTIONS = {
    "cloudfront:CreateDistribution",
    "cloudfront:CreateOriginAccessControl",
    "cognito-idp:CreateUserPool",
    "cognito-idp:DescribeUserPoolDomain",
    "cognito-idp:ListUserPools",
    "states:ListStateMachines",
    *LOG_DELIVERY_CONTROL_PLANE_ACTIONS,
}


class ProductionDeployerError(RuntimeError):
    """Raised when generation or reconciliation cannot prove safety."""


def compact_json(value: Any) -> str:
    """Return the deterministic non-whitespace JSON representation."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def policy_arn(name: str) -> str:
    return f"arn:aws:iam::{ACCOUNT_ID}:policy{POLICY_PATH}{name}"


def _statement(
    sid: str,
    actions: Iterable[str],
    resources: str | Iterable[str],
    condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    statement: dict[str, Any] = {
        "Sid": sid,
        "Effect": "Allow",
        "Action": sorted(actions),
        "Resource": (
            resources
            if isinstance(resources, str)
            else sorted(resources)
        ),
    }
    if condition:
        statement["Condition"] = condition
    return statement


def _policy(*statements: dict[str, Any]) -> dict[str, Any]:
    return {"Version": "2012-10-17", "Statement": list(statements)}


def generate_policies(region: str = "us-east-1") -> dict[str, dict[str, Any]]:
    """Build the six deterministic least-privilege policy documents."""
    if not re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-\d", region):
        raise ProductionDeployerError("AWS region is malformed.")

    table_arn = f"arn:aws:dynamodb:{region}:{ACCOUNT_ID}:table/journalm8-prod-main"
    raw_bucket_arn = f"arn:aws:s3:::journalm8-prod-raw-{ACCOUNT_ID}"
    frontend_bucket_arn = f"arn:aws:s3:::journalm8-prod-frontend-{ACCOUNT_ID}"
    lambda_arn = f"arn:aws:lambda:{region}:{ACCOUNT_ID}:function:journalm8-prod-*"
    state_machine_arn = (
        f"arn:aws:states:{region}:{ACCOUNT_ID}:stateMachine:journalm8-prod-*"
    )
    log_group_arns = (
        f"arn:aws:logs:{region}:{ACCOUNT_ID}:log-group:/aws/lambda/journalm8-prod-*",
        f"arn:aws:logs:{region}:{ACCOUNT_ID}:log-group:/aws/apigateway/journalm8-prod-api",
        f"arn:aws:logs:{region}:{ACCOUNT_ID}:log-group:/aws/vendedlogs/states/journalm8-prod-*",
    )
    runtime_role_names = (
        "journalm8-prod-lambda-basic-role",
        "journalm8-prod-ocr-worker-role",
        "journalm8-prod-step-functions-role",
        "journalm8-prod-historical-reanalysis-worker-role",
        "journalm8-prod-historical-reanalysis-coordinator-role",
        "journalm8-prod-historical-reanalysis-step-role",
    )
    runtime_role_arns = tuple(
        f"arn:aws:iam::{ACCOUNT_ID}:role/{name}"
        for name in runtime_role_names
    )
    lambda_role_arns = tuple(
        arn
        for arn in runtime_role_arns
        if not arn.endswith("step-functions-role")
        and not arn.endswith("historical-reanalysis-step-role")
    )
    step_role_arns = tuple(
        arn for arn in runtime_role_arns if arn not in lambda_role_arns
    )
    production_tag_condition = {
        "StringEquals": {
            "aws:ResourceTag/App": APP_NAME,
            "aws:ResourceTag/Stage": STAGE,
        }
    }
    production_request_tag_condition = {
        "StringEquals": {
            "aws:RequestTag/App": APP_NAME,
            "aws:RequestTag/Stage": STAGE,
        }
    }
    requested_region_condition = {
        "StringEquals": {"aws:RequestedRegion": region}
    }

    foundation = _policy(
        _statement(
            "ManageProductionTable",
            (
                "dynamodb:CreateTable",
                "dynamodb:DescribeContinuousBackups",
                "dynamodb:DescribeTable",
                "dynamodb:TagResource",
                "dynamodb:UpdateContinuousBackups",
                "dynamodb:UpdateTable",
            ),
            (table_arn, f"{table_arn}/index/*"),
        ),
        _statement(
            "ManageProductionRawBucket",
            (
                "s3:CreateBucket",
                "s3:GetBucketCORS",
                "s3:GetEncryptionConfiguration",
                "s3:GetLifecycleConfiguration",
                "s3:GetBucketLocation",
                "s3:GetBucketPolicy",
                "s3:GetBucketPublicAccessBlock",
                "s3:GetBucketTagging",
                "s3:GetBucketVersioning",
                "s3:ListBucket",
                "s3:PutBucketCORS",
                "s3:PutEncryptionConfiguration",
                "s3:PutLifecycleConfiguration",
                "s3:PutBucketPolicy",
                "s3:PutBucketPublicAccessBlock",
                "s3:PutBucketTagging",
                "s3:PutBucketVersioning",
            ),
            raw_bucket_arn,
        ),
        _statement(
            "DiscoverProductionUserPool",
            ("cognito-idp:ListUserPools",),
            "*",
            requested_region_condition,
        ),
        _statement(
            "CreateTaggedProductionUserPool",
            ("cognito-idp:CreateUserPool",),
            "*",
            production_request_tag_condition,
        ),
        _statement(
            "ManageTaggedProductionUserPool",
            (
                "cognito-idp:CreateUserPoolDomain",
                "cognito-idp:CreateUserPoolClient",
                "cognito-idp:DescribeUserPool",
                "cognito-idp:DescribeUserPoolClient",
                "cognito-idp:ListTagsForResource",
                "cognito-idp:ListUserPoolClients",
            ),
            f"arn:aws:cognito-idp:{region}:{ACCOUNT_ID}:userpool/*",
            production_tag_condition,
        ),
        _statement(
            "ManageProductionClassicHostedUiBranding",
            (
                "cognito-idp:GetUICustomization",
                "cognito-idp:SetUICustomization",
            ),
            f"arn:aws:cognito-idp:{region}:{ACCOUNT_ID}:userpool/*",
            production_tag_condition,
        ),
        _statement(
            "TagVerifiedProductionUserPool",
            ("cognito-idp:TagResource",),
            f"arn:aws:cognito-idp:{region}:{ACCOUNT_ID}:userpool/*",
            {
                "StringEquals": {
                    "aws:RequestTag/App": APP_NAME,
                    "aws:RequestTag/ManagedBy": "aws-cli",
                    "aws:RequestTag/Stage": STAGE,
                    "aws:ResourceTag/App": APP_NAME,
                    "aws:ResourceTag/Stage": STAGE,
                },
                "ForAllValues:StringEquals": {
                    "aws:TagKeys": ["App", "ManagedBy", "Stage"],
                },
            },
        ),
        _statement(
            "DescribeProductionUserPoolDomain",
            ("cognito-idp:DescribeUserPoolDomain",),
            "*",
            requested_region_condition,
        ),
        _statement(
            "ResolveProductionBedrockProfile",
            ("bedrock:GetInferenceProfile",),
            (
                f"arn:aws:bedrock:{region}:{ACCOUNT_ID}:"
                f"inference-profile/{BEDROCK_PROFILE_ID}"
            ),
        ),
    )

    api_collection_arn = f"arn:aws:apigateway:{region}::/apis"
    api_resource_arn = f"arn:aws:apigateway:{region}::/apis/*"
    api_tag_on_create_arn = (
        f"arn:aws:apigateway:{region}::/tags/"
        f"arn%3Aaws%3Aapigateway%3A{region}%3A%3A%2Fv2%2Fapis%2F*"
    )
    required_api_request_tag_presence = {
        "aws:RequestTag/App": "false",
        "aws:RequestTag/ManagedBy": "false",
        "aws:RequestTag/Stage": "false",
        "aws:TagKeys": "false",
    }
    exact_api_request_tag_keys = {
        "aws:TagKeys": ["App", "ManagedBy", "Stage"],
    }
    compute = _policy(
        _statement(
            "ManageProductionLambdaFunctions",
            (
                "lambda:AddPermission",
                "lambda:CreateFunction",
                "lambda:GetFunction",
                "lambda:GetFunctionConfiguration",
                "lambda:ListTags",
                "lambda:PutFunctionConcurrency",
                "lambda:TagResource",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
            ),
            lambda_arn,
        ),
        _statement(
            "ManageProductionStateMachines",
            (
                "states:CreateStateMachine",
                "states:DescribeStateMachine",
                "states:TagResource",
                "states:UpdateStateMachine",
            ),
            state_machine_arn,
        ),
        _statement(
            "ListStateMachinesForExactNameResolution",
            ("states:ListStateMachines",),
            "*",
            requested_region_condition,
        ),
        _statement(
            "DiscoverHttpApis",
            ("apigateway:GET",),
            api_collection_arn,
        ),
        _statement(
            "CreateTaggedProductionHttpApi",
            ("apigateway:POST",),
            api_collection_arn,
            {
                "StringEquals": {
                    "apigateway:Request/ApiName": "journalm8-prod-api",
                    "aws:RequestTag/App": APP_NAME,
                    "aws:RequestTag/ManagedBy": "aws-cli",
                    "aws:RequestTag/Stage": STAGE,
                },
                "ForAllValues:StringEquals": exact_api_request_tag_keys,
                "Null": required_api_request_tag_presence,
            },
        ),
        _statement(
            "TagProductionHttpApiDuringCreation",
            ("apigateway:POST",),
            api_tag_on_create_arn,
            {
                "StringEquals": {
                    "aws:RequestTag/App": APP_NAME,
                    "aws:RequestTag/ManagedBy": "aws-cli",
                    "aws:RequestTag/Stage": STAGE,
                },
                "ForAllValues:StringEquals": exact_api_request_tag_keys,
                "Null": required_api_request_tag_presence,
            },
        ),
        _statement(
            "ManageTaggedProductionHttpApi",
            ("apigateway:GET", "apigateway:PATCH", "apigateway:POST"),
            api_resource_arn,
            {
                "StringEquals": {
                    "aws:ResourceTag/App": APP_NAME,
                    "aws:ResourceTag/Stage": STAGE,
                }
            },
        ),
    )

    iam_policy = _policy(
        _statement(
            "ManageOnlyProductionRuntimeRoles",
            (
                "iam:CreateRole",
                "iam:GetRole",
                "iam:GetRolePolicy",
                "iam:ListAttachedRolePolicies",
                "iam:PutRolePolicy",
                "iam:UpdateAssumeRolePolicy",
            ),
            runtime_role_arns,
        ),
        _statement(
            "AttachOnlyLambdaBasicExecutionPolicy",
            ("iam:AttachRolePolicy",),
            lambda_role_arns,
            {"ArnEquals": {"iam:PolicyARN": AWS_LAMBDA_BASIC_POLICY_ARN}},
        ),
        _statement(
            "PassOnlyProductionLambdaRolesToLambda",
            ("iam:PassRole",),
            lambda_role_arns,
            {"StringEquals": {"iam:PassedToService": "lambda.amazonaws.com"}},
        ),
        _statement(
            "PassOnlyProductionWorkflowRolesToStepFunctions",
            ("iam:PassRole",),
            step_role_arns,
            {"StringEquals": {"iam:PassedToService": "states.amazonaws.com"}},
        ),
    )

    stack_arn = (
        f"arn:aws:cloudformation:{region}:{ACCOUNT_ID}:"
        "stack/journalm8-prod-frontend-hosting/*"
    )
    frontend = _policy(
        _statement(
            "ManageProductionFrontendStack",
            (
                "cloudformation:DeleteChangeSet",
                "cloudformation:DescribeChangeSet",
                "cloudformation:DescribeStackEvents",
                "cloudformation:DescribeStacks",
                "cloudformation:ExecuteChangeSet",
                "cloudformation:GetTemplate",
                "cloudformation:ListStackResources",
            ),
            stack_arn,
        ),
        _statement(
            "CreateTaggedProductionFrontendChangeSet",
            ("cloudformation:CreateChangeSet",),
            stack_arn,
            {
                "StringEquals": {
                    "aws:RequestTag/App": APP_NAME,
                    "aws:RequestTag/ManagedBy": "aws-cli",
                    "aws:RequestTag/Stage": STAGE,
                },
                "ForAllValues:StringEquals": {
                    "aws:TagKeys": ["App", "ManagedBy", "Stage"],
                },
            },
        ),
        _statement(
            "ManageProductionFrontendBucket",
            (
                "s3:CreateBucket",
                "s3:DeleteBucket",
                "s3:DeleteBucketPolicy",
                "s3:GetEncryptionConfiguration",
                "s3:GetBucketLocation",
                "s3:GetBucketOwnershipControls",
                "s3:GetBucketPolicy",
                "s3:GetBucketPublicAccessBlock",
                "s3:GetBucketTagging",
                "s3:GetBucketVersioning",
                "s3:GetBucketWebsite",
                "s3:ListBucket",
                "s3:PutEncryptionConfiguration",
                "s3:PutLifecycleConfiguration",
                "s3:PutBucketOwnershipControls",
                "s3:PutBucketPolicy",
                "s3:PutBucketPublicAccessBlock",
                "s3:PutBucketTagging",
                "s3:PutBucketVersioning",
            ),
            frontend_bucket_arn,
        ),
        _statement(
            "PublishProductionFrontendObjects",
            ("s3:DeleteObject", "s3:PutObject"),
            f"{frontend_bucket_arn}/*",
        ),
        _statement(
            "CreateTaggedProductionDistribution",
            ("cloudfront:CreateDistribution",),
            "*",
            {
                "StringEquals": {
                    "aws:RequestTag/App": APP_NAME,
                    "aws:RequestTag/ManagedBy": "aws-cli",
                    "aws:RequestTag/Stage": STAGE,
                    "aws:RequestedRegion": region,
                },
                "ForAnyValue:StringEquals": {
                    "aws:CalledVia": "cloudformation.amazonaws.com",
                },
            },
        ),
        _statement(
            "CreateProductionOriginAccessControl",
            ("cloudfront:CreateOriginAccessControl",),
            "*",
            {
                "StringEquals": {"aws:RequestedRegion": region},
                "ForAnyValue:StringEquals": {
                    "aws:CalledVia": "cloudformation.amazonaws.com",
                },
            },
        ),
        _statement(
            "ManageTaggedProductionDistributions",
            (
                "cloudfront:CreateInvalidation",
                "cloudfront:DeleteDistribution",
                "cloudfront:GetDistribution",
                "cloudfront:GetDistributionConfig",
                "cloudfront:GetInvalidation",
                "cloudfront:ListTagsForResource",
                "cloudfront:UpdateDistribution",
            ),
            f"arn:aws:cloudfront::{ACCOUNT_ID}:distribution/*",
            production_tag_condition,
        ),
        _statement(
            "ReadProductionOriginAccessControl",
            (
                "cloudfront:GetOriginAccessControl",
                "cloudfront:GetOriginAccessControlConfig",
            ),
            f"arn:aws:cloudfront::{ACCOUNT_ID}:origin-access-control/*",
            requested_region_condition,
        ),
        _statement(
            "ManageProductionOriginAccessControlViaStack",
            (
                "cloudfront:DeleteOriginAccessControl",
                "cloudfront:UpdateOriginAccessControl",
            ),
            f"arn:aws:cloudfront::{ACCOUNT_ID}:origin-access-control/*",
            {
                "StringEquals": {"aws:RequestedRegion": region},
                "ForAnyValue:StringEquals": {
                    "aws:CalledVia": "cloudformation.amazonaws.com",
                },
            },
        ),
        _statement(
            "TagOnlyProductionCloudFrontResources",
            ("cloudfront:TagResource",),
            f"arn:aws:cloudfront::{ACCOUNT_ID}:distribution/*",
            {
                "StringEquals": {
                    "aws:RequestTag/App": APP_NAME,
                    "aws:RequestTag/ManagedBy": "aws-cli",
                    "aws:RequestTag/Stage": STAGE,
                },
                "ForAnyValue:StringEquals": {
                    "aws:CalledVia": "cloudformation.amazonaws.com",
                },
            },
        ),
    )

    topic_arn = f"arn:aws:sns:{region}:{ACCOUNT_ID}:journalm8-prod-alerts"
    alarm_arn = f"arn:aws:cloudwatch:{region}:{ACCOUNT_ID}:alarm:journalm8-prod-*"
    dashboard_arn = (
        f"arn:aws:cloudwatch::{ACCOUNT_ID}:dashboard/journalm8-prod-*"
    )
    budget_arns = (
        (
            f"arn:aws:budgets::{ACCOUNT_ID}:budget/"
            "journalm8-prod-bedrock-monthly"
        ),
        (
            f"arn:aws:budgets::{ACCOUNT_ID}:budget/"
            "journalm8-prod-production-monthly"
        ),
    )
    observability = _policy(
        _statement(
            "ManageProductionLogGroups",
            (
                "logs:CreateLogGroup",
                "logs:PutMetricFilter",
                "logs:PutRetentionPolicy",
            ),
            log_group_arns,
        ),
        _statement(
            "ManageApiGatewayAccessLogDelivery",
            LOG_DELIVERY_CONTROL_PLANE_ACTIONS,
            "*",
            requested_region_condition,
        ),
        _statement(
            "ManageProductionAlarms",
            ("cloudwatch:DescribeAlarms", "cloudwatch:PutMetricAlarm"),
            alarm_arn,
        ),
        _statement(
            "ManageProductionDashboards",
            ("cloudwatch:PutDashboard",),
            dashboard_arn,
        ),
        _statement(
            "ManageProductionAlertTopic",
            (
                "sns:CreateTopic",
                "sns:GetTopicAttributes",
                "sns:ListSubscriptionsByTopic",
                "sns:SetTopicAttributes",
                "sns:Subscribe",
            ),
            topic_arn,
        ),
        _statement(
            "ManageProductionBudgets",
            (
                "budgets:ModifyBudget",
                "budgets:ViewBudget",
            ),
            budget_arns,
        ),
    )

    secret_arn = (
        f"arn:aws:secretsmanager:{region}:{ACCOUNT_ID}:"
        "secret:journalm8/prod/stripe-*"
    )
    secrets = _policy(
        _statement(
            "ManageOnlyProductionStripeSecret",
            (
                "secretsmanager:DescribeSecret",
                "secretsmanager:GetSecretValue",
                "secretsmanager:PutSecretValue",
                "secretsmanager:TagResource",
            ),
            secret_arn,
        ),
        _statement(
            "CreateOnlyTaggedProductionStripeSecret",
            ("secretsmanager:CreateSecret",),
            secret_arn,
            {
                "StringEquals": {
                    "aws:RequestTag/App": APP_NAME,
                    "aws:RequestTag/Stage": STAGE,
                    "secretsmanager:Name": "journalm8/prod/stripe",
                }
            },
        ),
    )

    policies = {
        POLICY_NAMES[0]: foundation,
        POLICY_NAMES[1]: compute,
        POLICY_NAMES[2]: iam_policy,
        POLICY_NAMES[3]: frontend,
        POLICY_NAMES[4]: observability,
        POLICY_NAMES[5]: secrets,
    }
    validate_generated_policies(policies)
    return policies


def _as_list(value: Any, label: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        raise ProductionDeployerError(f"{label} is missing or malformed.")
    return [value]


def validate_generated_policies(policies: Any) -> None:
    """Validate policy count, size, action syntax, and canonical safety."""
    if not isinstance(policies, dict) or tuple(policies) != POLICY_NAMES:
        raise ProductionDeployerError("Generated policy set is not canonical.")

    for name, document in policies.items():
        size = len(compact_json(document))
        if size > POLICY_SIZE_LIMIT:
            raise ProductionDeployerError(
                f"Managed policy {name} exceeds the 6,144-character limit."
            )
        if not isinstance(document, dict) or document.get("Version") != "2012-10-17":
            raise ProductionDeployerError(f"Managed policy {name} is malformed.")
        statements = document.get("Statement")
        if not isinstance(statements, list) or not statements:
            raise ProductionDeployerError(f"Managed policy {name} is malformed.")
        for statement in statements:
            if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
                raise ProductionDeployerError(f"Managed policy {name} is malformed.")
            actions = _as_list(statement.get("Action"), "Policy actions")
            if not actions or any(
                not isinstance(action, str)
                or action == "*"
                or action.endswith(":*")
                for action in actions
            ):
                raise ProductionDeployerError(
                    f"Managed policy {name} contains a wildcard action."
                )
            resources = _as_list(statement.get("Resource"), "Policy resources")
            if not resources or any(not isinstance(item, str) for item in resources):
                raise ProductionDeployerError(f"Managed policy {name} is malformed.")
            if "*" in resources and not statement.get("Condition"):
                raise ProductionDeployerError(
                    f"Unscoped statement in {name} must have applicable conditions."
                )
            if "*" in resources and not set(actions).issubset(RESOURCE_STAR_ACTIONS):
                raise ProductionDeployerError(
                    f"Managed policy {name} contains an unsupported unscoped action."
                )

    serialized = compact_json(policies)
    for forbidden in (
        "AdministratorAccess",
        "PowerUserAccess",
        "IAMFullAccess",
        "journalm8-dev-",
        "journalm8-staging-",
    ):
        if forbidden in serialized:
            raise ProductionDeployerError(
                "Generated policies contain forbidden authorization."
            )

    if DEPLOYER_ROLE_ARN in serialized:
        raise ProductionDeployerError("The deployer IAM policy permits self-modification.")


def write_policies(policies: dict[str, dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, document in policies.items():
        (output_dir / f"{name}.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    manifest = {
        "accountId": ACCOUNT_ID,
        "roleArn": DEPLOYER_ROLE_ARN,
        "policyPath": POLICY_PATH,
        "policies": [
            {
                "name": name,
                "arn": policy_arn(name),
                "compactSize": len(compact_json(document)),
            }
            for name, document in policies.items()
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class AwsCli:
    """Secret-safe AWS CLI JSON adapter."""

    def __init__(self, profile: str, region: str) -> None:
        self.profile = profile
        self.region = region

    def call(self, service: str, operation: str, *arguments: str) -> dict[str, Any]:
        command = [
            "aws",
            service,
            operation,
            *arguments,
            "--profile",
            self.profile,
            "--region",
            self.region,
            "--output",
            "json",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProductionDeployerError(
                f"AWS command could not run: {service} {operation}."
            ) from error
        if result.returncode != 0:
            code_match = re.search(r"\(([A-Za-z0-9]+)\)", result.stderr or "")
            candidate = code_match.group(1) if code_match else ""
            code = candidate if candidate in SAFE_AWS_ERROR_CODES else "AwsCliError"
            raise ProductionDeployerError(
                f"AWS command failed: {service} {operation} ({code})."
            )
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise ProductionDeployerError(
                f"AWS response was malformed: {service} {operation}."
            ) from error
        if not isinstance(response, dict):
            raise ProductionDeployerError(
                f"AWS response was malformed: {service} {operation}."
            )
        return response


def _decode_document(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(unquote(value))
        except json.JSONDecodeError as error:
            raise ProductionDeployerError(
                "Managed policy document is malformed."
            ) from error
    if not isinstance(value, dict):
        raise ProductionDeployerError("Managed policy document is malformed.")
    return value


def verify_identity_and_role(aws: AwsCli) -> None:
    identity = aws.call("sts", "get-caller-identity")
    account = identity.get("Account")
    caller_arn = identity.get("Arn")
    if account != ACCOUNT_ID or not isinstance(caller_arn, str):
        raise ProductionDeployerError("AWS caller identity is incorrect.")
    if not re.fullmatch(
        rf"arn:aws:(?:iam|sts)::{ACCOUNT_ID}:"
        r"(?:user|role|assumed-role)/[^\s]+",
        caller_arn,
    ):
        raise ProductionDeployerError("AWS caller identity is malformed.")

    role = aws.call("iam", "get-role", "--role-name", DEPLOYER_ROLE_NAME)
    role_data = role.get("Role")
    if not isinstance(role_data, dict) or role_data.get("Arn") != DEPLOYER_ROLE_ARN:
        raise ProductionDeployerError("Production deployer role is missing or incorrect.")


def _attached_policy_arns(aws: AwsCli) -> set[str]:
    response = aws.call(
        "iam",
        "list-attached-role-policies",
        "--role-name",
        DEPLOYER_ROLE_NAME,
    )
    items = response.get("AttachedPolicies")
    if not isinstance(items, list) or response.get("IsTruncated") is True:
        raise ProductionDeployerError("Attached-policy response is malformed.")
    arns: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ProductionDeployerError("Attached-policy response is malformed.")
        arn = item.get("PolicyArn")
        name = item.get("PolicyName")
        if not isinstance(arn, str) or not isinstance(name, str):
            raise ProductionDeployerError("Attached-policy response is malformed.")
        if name in FORBIDDEN_MANAGED_POLICY_NAMES:
            raise ProductionDeployerError("A forbidden managed policy is attached.")
        if arn in arns or name != arn.rsplit("/", 1)[-1]:
            raise ProductionDeployerError("Attached-policy response is malformed.")
        arns.add(arn)
    return arns


def _effective_role_policy_quota(aws: AwsCli) -> int:
    response = aws.call("iam", "get-account-summary")
    summary = response.get("SummaryMap")
    if not isinstance(summary, dict):
        raise ProductionDeployerError("IAM account summary is malformed.")
    quota = summary.get("AttachedPoliciesPerRoleQuota")
    if not isinstance(quota, int) or quota < 1:
        raise ProductionDeployerError("Managed-policy-per-role quota is malformed.")
    return quota


def _get_policy_or_none(aws: AwsCli, arn: str) -> dict[str, Any] | None:
    try:
        return aws.call("iam", "get-policy", "--policy-arn", arn)
    except ProductionDeployerError as error:
        if "(NoSuchEntity)" in str(error):
            return None
        raise


def _policy_entities(aws: AwsCli, arn: str) -> dict[str, Any]:
    response = aws.call("iam", "list-entities-for-policy", "--policy-arn", arn)
    if response.get("IsTruncated") is True:
        raise ProductionDeployerError("Managed-policy entity response is truncated.")
    for key in ("PolicyGroups", "PolicyUsers", "PolicyRoles"):
        if not isinstance(response.get(key), list):
            raise ProductionDeployerError("Managed-policy entity response is malformed.")
    return response


def _require_only_target_role(entities: dict[str, Any], attached: bool) -> None:
    if entities["PolicyGroups"] or entities["PolicyUsers"]:
        raise ProductionDeployerError("Managed policy is attached outside the target role.")
    role_names = {
        role.get("RoleName")
        for role in entities["PolicyRoles"]
        if isinstance(role, dict)
    }
    expected = {DEPLOYER_ROLE_NAME} if attached else set()
    if role_names != expected or len(role_names) != len(entities["PolicyRoles"]):
        raise ProductionDeployerError("Managed policy is attached outside the target role.")


def _get_default_document(
    aws: AwsCli,
    arn: str,
    policy_response: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    policy_data = policy_response.get("Policy")
    if not isinstance(policy_data, dict):
        raise ProductionDeployerError("Managed policy metadata is malformed.")
    if (
        policy_data.get("Arn") != arn
        or policy_data.get("Path") != POLICY_PATH
        or policy_data.get("PolicyName") != arn.rsplit("/", 1)[-1]
    ):
        raise ProductionDeployerError("Managed policy metadata is incorrect.")
    version_id = policy_data.get("DefaultVersionId")
    if not isinstance(version_id, str) or not re.fullmatch(r"v[1-9][0-9]*", version_id):
        raise ProductionDeployerError("Managed policy default version is malformed.")
    response = aws.call(
        "iam",
        "get-policy-version",
        "--policy-arn",
        arn,
        "--version-id",
        version_id,
    )
    version = response.get("PolicyVersion")
    if not isinstance(version, dict) or version.get("VersionId") != version_id:
        raise ProductionDeployerError("Managed policy version response is malformed.")
    return version_id, _decode_document(version.get("Document"))


@dataclass(frozen=True)
class ExistingPolicy:
    name: str
    arn: str
    response: dict[str, Any] | None
    default_version_id: str | None
    document: dict[str, Any] | None
    attached_to_target: bool


def preflight(
    aws: AwsCli,
    policies: dict[str, dict[str, Any]],
) -> dict[str, ExistingPolicy]:
    """Read and validate all remote state before any mutation."""
    quota = _effective_role_policy_quota(aws)
    if len(policies) > quota:
        raise ProductionDeployerError(
            "Generated policy count exceeds the effective per-role quota."
        )
    expected_arns = {policy_arn(name) for name in policies}
    attached_arns = _attached_policy_arns(aws)
    if attached_arns - expected_arns:
        raise ProductionDeployerError(
            "Production deployer has an unexpected managed policy attachment."
        )
    if len(attached_arns | expected_arns) > quota:
        raise ProductionDeployerError(
            "Desired attachments exceed the effective per-role quota."
        )

    inventory: dict[str, ExistingPolicy] = {}
    for name in policies:
        arn = policy_arn(name)
        response = _get_policy_or_none(aws, arn)
        if response is None:
            inventory[name] = ExistingPolicy(name, arn, None, None, None, False)
            continue
        entities = _policy_entities(aws, arn)
        attached = arn in attached_arns
        _require_only_target_role(entities, attached)
        version_id, document = _get_default_document(aws, arn, response)
        inventory[name] = ExistingPolicy(
            name,
            arn,
            response,
            version_id,
            document,
            attached,
        )
    return inventory


def _oldest_non_default_version(versions: list[Any]) -> str:
    candidates: list[tuple[datetime, str]] = []
    for version in versions:
        if not isinstance(version, dict):
            raise ProductionDeployerError("Managed policy version list is malformed.")
        version_id = version.get("VersionId")
        is_default = version.get("IsDefaultVersion")
        created = version.get("CreateDate")
        if (
            not isinstance(version_id, str)
            or not isinstance(is_default, bool)
            or not isinstance(created, str)
        ):
            raise ProductionDeployerError("Managed policy version list is malformed.")
        if not is_default:
            try:
                timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError as error:
                raise ProductionDeployerError(
                    "Managed policy version date is malformed."
                ) from error
            candidates.append((timestamp, version_id))
    if not candidates:
        raise ProductionDeployerError(
            "Five-version policy has no removable non-default version."
        )
    return min(candidates)[1]


def _replace_changed_policy(
    aws: AwsCli,
    existing: ExistingPolicy,
    expected_document: dict[str, Any],
) -> None:
    versions_response = aws.call(
        "iam", "list-policy-versions", "--policy-arn", existing.arn
    )
    versions = versions_response.get("Versions")
    if not isinstance(versions, list) or not 1 <= len(versions) <= 5:
        raise ProductionDeployerError("Managed policy version list is malformed.")
    if len(versions) == 5:
        removable = _oldest_non_default_version(versions)
        if removable == existing.default_version_id:
            raise ProductionDeployerError("Refusing to delete a default policy version.")
        aws.call(
            "iam",
            "delete-policy-version",
            "--policy-arn",
            existing.arn,
            "--version-id",
            removable,
        )
    response = aws.call(
        "iam",
        "create-policy-version",
        "--policy-arn",
        existing.arn,
        "--policy-document",
        compact_json(expected_document),
        "--set-as-default",
    )
    version = response.get("PolicyVersion")
    if not isinstance(version, dict) or version.get("IsDefaultVersion") is not True:
        raise ProductionDeployerError("New managed policy version was not made default.")


def apply_policies(
    aws: AwsCli,
    policies: dict[str, dict[str, Any]],
    inventory: dict[str, ExistingPolicy],
) -> None:
    for name, expected_document in policies.items():
        existing = inventory[name]
        if existing.response is None:
            response = aws.call(
                "iam",
                "create-policy",
                "--policy-name",
                name,
                "--path",
                POLICY_PATH,
                "--policy-document",
                compact_json(expected_document),
                "--description",
                f"JM8 production deployer permissions: {name.rsplit('-', 1)[-1]}",
            )
            policy_data = response.get("Policy")
            if not isinstance(policy_data, dict) or policy_data.get("Arn") != existing.arn:
                raise ProductionDeployerError("Created managed policy ARN is incorrect.")
        elif compact_json(existing.document) != compact_json(expected_document):
            _replace_changed_policy(aws, existing, expected_document)

        if not existing.attached_to_target:
            aws.call(
                "iam",
                "attach-role-policy",
                "--role-name",
                DEPLOYER_ROLE_NAME,
                "--policy-arn",
                existing.arn,
            )


def verify_applied_policies(
    aws: AwsCli,
    policies: dict[str, dict[str, Any]],
) -> None:
    expected_arns = {policy_arn(name) for name in policies}
    quota = _effective_role_policy_quota(aws)
    if len(expected_arns) > quota:
        raise ProductionDeployerError("Applied policies exceed the effective role quota.")
    if _attached_policy_arns(aws) != expected_arns:
        raise ProductionDeployerError(
            "Production deployer managed-policy attachments are incorrect."
        )
    for name, expected_document in policies.items():
        arn = policy_arn(name)
        response = _get_policy_or_none(aws, arn)
        if response is None:
            raise ProductionDeployerError("Expected managed policy is missing.")
        entities = _policy_entities(aws, arn)
        _require_only_target_role(entities, True)
        _, actual_document = _get_default_document(aws, arn, response)
        if compact_json(actual_document) != compact_json(expected_document):
            raise ProductionDeployerError(
                f"Applied default policy document does not match {name}."
            )


def execute(
    mode: str,
    aws: AwsCli,
    output_dir: Path,
    region: str,
) -> None:
    policies = generate_policies(region)
    write_policies(policies, output_dir)
    verify_identity_and_role(aws)
    inventory = preflight(aws, policies)
    if mode == "apply":
        apply_policies(aws, policies, inventory)
        verify_applied_policies(aws, policies)
    elif mode == "verify":
        verify_applied_policies(aws, policies)
    elif mode != "generate":
        raise ProductionDeployerError("Mode must be generate, apply, or verify.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("generate", "apply", "verify"))
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument("--aws-region", required=True)
    parser.add_argument("--expected-account-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.aws_profile != PROVISIONING_PROFILE:
        raise SystemExit("Production deployer provisioning requires AWS_PROFILE=jm8-dev.")
    if args.expected_account_id != ACCOUNT_ID:
        raise SystemExit("Production deployer provisioning requires the approved AWS account.")
    aws = AwsCli(args.aws_profile, args.aws_region)
    try:
        execute(args.mode, aws, args.output_dir, args.aws_region)
    except ProductionDeployerError as error:
        print(f"PRODUCTION_DEPLOYER_ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(
        f"Production deployer policy {args.mode} completed for "
        f"{DEPLOYER_ROLE_NAME}; policies={len(POLICY_NAMES)}."
    )


if __name__ == "__main__":
    main()
