from __future__ import annotations

import copy
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
PROVISIONER = BIN_DIR / "provision-production-deployer"
HELPER = BIN_DIR / "jm8_production_deployer_policies.py"

sys.path.insert(0, str(BIN_DIR))

from jm8_production_deployer_policies import (  # noqa: E402
    ACCOUNT_ID,
    DEPLOYER_ROLE_ARN,
    DEPLOYER_ROLE_NAME,
    POLICY_NAMES,
    POLICY_PATH,
    POLICY_SIZE_LIMIT,
    RESOURCE_STAR_ACTIONS,
    AwsCli,
    ProductionDeployerError,
    apply_policies,
    compact_json,
    execute,
    generate_policies,
    policy_arn,
    preflight,
    validate_generated_policies,
    verify_applied_policies,
    verify_identity_and_role,
    write_policies,
)


def _argument(arguments: tuple[str, ...], name: str) -> str:
    index = arguments.index(name)
    return arguments[index + 1]


class FakeAws:
    """Stateful IAM fake used to prove reconciliation semantics."""

    def __init__(self, *, quota: int = 10) -> None:
        self.account = ACCOUNT_ID
        self.role_arn = DEPLOYER_ROLE_ARN
        self.quota = quota
        self.policies: dict[str, dict] = {}
        self.attached: set[str] = set()
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.fail_operation: tuple[str, str] | None = None
        self.mismatch_document_for: str | None = None

    @property
    def mutations(self) -> list[tuple[str, str, tuple[str, ...]]]:
        mutating = {
            ("iam", "create-policy"),
            ("iam", "create-policy-version"),
            ("iam", "delete-policy-version"),
            ("iam", "attach-role-policy"),
        }
        return [call for call in self.calls if call[:2] in mutating]

    def seed_policy(
        self,
        name: str,
        document: dict,
        *,
        attached: bool = True,
        version_count: int = 1,
    ) -> None:
        arn = policy_arn(name)
        versions = []
        for number in range(1, version_count + 1):
            versions.append({
                "VersionId": f"v{number}",
                "Document": copy.deepcopy(document),
                "IsDefaultVersion": number == version_count,
                "CreateDate": f"2026-01-{number:02d}T00:00:00Z",
            })
        self.policies[arn] = {
            "Name": name,
            "Path": POLICY_PATH,
            "Versions": versions,
            "Roles": {DEPLOYER_ROLE_NAME} if attached else set(),
            "Users": set(),
            "Groups": set(),
        }
        if attached:
            self.attached.add(arn)

    def call(self, service: str, operation: str, *arguments: str) -> dict:
        call = (service, operation, arguments)
        self.calls.append(call)
        if self.fail_operation == (service, operation):
            raise ProductionDeployerError(
                f"AWS command failed: {service} {operation} (AccessDenied)."
            )

        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": self.account,
                "Arn": f"arn:aws:sts::{self.account}:assumed-role/jm8-dev/session",
            }
        if (service, operation) == ("iam", "get-role"):
            return {"Role": {"Arn": self.role_arn, "RoleName": DEPLOYER_ROLE_NAME}}
        if (service, operation) == ("iam", "get-account-summary"):
            return {"SummaryMap": {"AttachedPoliciesPerRoleQuota": self.quota}}
        if (service, operation) == ("iam", "list-attached-role-policies"):
            return {
                "AttachedPolicies": [
                    {
                        "PolicyArn": arn,
                        "PolicyName": self.policies[arn]["Name"],
                    }
                    for arn in sorted(self.attached)
                ]
            }
        if (service, operation) == ("iam", "get-policy"):
            arn = _argument(arguments, "--policy-arn")
            if arn not in self.policies:
                raise ProductionDeployerError(
                    "AWS command failed: iam get-policy (NoSuchEntity)."
                )
            policy = self.policies[arn]
            default = next(
                version
                for version in policy["Versions"]
                if version["IsDefaultVersion"]
            )
            return {
                "Policy": {
                    "Arn": arn,
                    "PolicyName": policy["Name"],
                    "Path": policy["Path"],
                    "DefaultVersionId": default["VersionId"],
                }
            }
        if (service, operation) == ("iam", "get-policy-version"):
            arn = _argument(arguments, "--policy-arn")
            version_id = _argument(arguments, "--version-id")
            version = next(
                item
                for item in self.policies[arn]["Versions"]
                if item["VersionId"] == version_id
            )
            document = copy.deepcopy(version["Document"])
            if self.mismatch_document_for == arn:
                document["Statement"][0]["Action"].append("iam:CreateAccessKey")
            return {
                "PolicyVersion": {
                    "VersionId": version_id,
                    "IsDefaultVersion": version["IsDefaultVersion"],
                    "Document": document,
                }
            }
        if (service, operation) == ("iam", "list-entities-for-policy"):
            arn = _argument(arguments, "--policy-arn")
            policy = self.policies[arn]
            return {
                "PolicyGroups": [
                    {"GroupName": value} for value in sorted(policy["Groups"])
                ],
                "PolicyUsers": [
                    {"UserName": value} for value in sorted(policy["Users"])
                ],
                "PolicyRoles": [
                    {"RoleName": value} for value in sorted(policy["Roles"])
                ],
            }
        if (service, operation) == ("iam", "list-policy-versions"):
            arn = _argument(arguments, "--policy-arn")
            return {
                "Versions": [
                    {
                        "VersionId": item["VersionId"],
                        "IsDefaultVersion": item["IsDefaultVersion"],
                        "CreateDate": item["CreateDate"],
                    }
                    for item in self.policies[arn]["Versions"]
                ]
            }
        if (service, operation) == ("iam", "create-policy"):
            name = _argument(arguments, "--policy-name")
            arn = policy_arn(name)
            self.policies[arn] = {
                "Name": name,
                "Path": _argument(arguments, "--path"),
                "Versions": [{
                    "VersionId": "v1",
                    "Document": json.loads(_argument(arguments, "--policy-document")),
                    "IsDefaultVersion": True,
                    "CreateDate": "2026-02-01T00:00:00Z",
                }],
                "Roles": set(),
                "Users": set(),
                "Groups": set(),
            }
            return {"Policy": {"Arn": arn}}
        if (service, operation) == ("iam", "attach-role-policy"):
            role_name = _argument(arguments, "--role-name")
            arn = _argument(arguments, "--policy-arn")
            if role_name != DEPLOYER_ROLE_NAME:
                raise AssertionError("attempted attachment to an unexpected role")
            self.attached.add(arn)
            self.policies[arn]["Roles"].add(role_name)
            return {}
        if (service, operation) == ("iam", "delete-policy-version"):
            arn = _argument(arguments, "--policy-arn")
            version_id = _argument(arguments, "--version-id")
            policy = self.policies[arn]
            target = next(
                item for item in policy["Versions"] if item["VersionId"] == version_id
            )
            if target["IsDefaultVersion"]:
                raise AssertionError("attempted to delete the default version")
            policy["Versions"].remove(target)
            return {}
        if (service, operation) == ("iam", "create-policy-version"):
            arn = _argument(arguments, "--policy-arn")
            policy = self.policies[arn]
            previous_default = next(
                item for item in policy["Versions"] if item["IsDefaultVersion"]
            )
            number = max(
                int(item["VersionId"][1:]) for item in policy["Versions"]
            ) + 1
            previous_default["IsDefaultVersion"] = False
            version = {
                "VersionId": f"v{number}",
                "Document": json.loads(_argument(arguments, "--policy-document")),
                "IsDefaultVersion": True,
                "CreateDate": "2026-02-28T00:00:00Z",
            }
            policy["Versions"].append(version)
            return {
                "PolicyVersion": {
                    "VersionId": version["VersionId"],
                    "IsDefaultVersion": True,
                }
            }
        raise AssertionError(f"unexpected fake AWS call: {service} {operation}")


class PolicyGenerationTests(unittest.TestCase):
    def test_policy_names_count_and_path_are_deterministic(self):
        first = generate_policies()
        second = generate_policies()

        self.assertEqual(tuple(first), POLICY_NAMES)
        self.assertEqual(len(first), 6)
        self.assertEqual(compact_json(first), compact_json(second))
        for name in POLICY_NAMES:
            self.assertEqual(
                policy_arn(name),
                f"arn:aws:iam::{ACCOUNT_ID}:policy{POLICY_PATH}{name}",
            )

    def test_generated_files_are_deterministic(self):
        policies = generate_policies()
        with tempfile.TemporaryDirectory() as first_directory:
            with tempfile.TemporaryDirectory() as second_directory:
                first = Path(first_directory)
                second = Path(second_directory)
                write_policies(policies, first)
                write_policies(policies, second)
                self.assertEqual(
                    {
                        path.name: path.read_bytes()
                        for path in first.iterdir()
                    },
                    {
                        path.name: path.read_bytes()
                        for path in second.iterdir()
                    },
                )

    def test_every_policy_is_below_the_compact_aws_size_limit(self):
        for name, document in generate_policies().items():
            with self.subTest(policy=name):
                self.assertLessEqual(len(compact_json(document)), POLICY_SIZE_LIMIT)

    def test_oversized_policy_fails_validation(self):
        policies = generate_policies()
        policies[POLICY_NAMES[0]]["Statement"][0]["Resource"] = "x" * 7_000
        with self.assertRaisesRegex(ProductionDeployerError, "6,144"):
            validate_generated_policies(policies)

    def test_actions_are_explicit_and_forbidden_managed_policies_are_absent(self):
        serialized = compact_json(generate_policies())
        for forbidden in (
            '"Action":"*"',
            '"Action":["*"]',
            ':*"',
            "AdministratorAccess",
            "PowerUserAccess",
            "IAMFullAccess",
            "organizations:",
            "account:",
            "iam:CreateAccessKey",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

        for document in generate_policies().values():
            for statement in document["Statement"]:
                for action in statement["Action"]:
                    self.assertNotEqual(action, "*")
                    self.assertFalse(action.endswith(":*"))

    def test_production_customer_resources_exclude_dev_and_staging(self):
        serialized = compact_json(generate_policies())
        self.assertNotIn("journalm8-dev", serialized)
        self.assertNotIn("journalm8-staging", serialized)
        for expected in (
            "journalm8-prod-main",
            "journalm8-prod-entry-chunks",
            "journalm8-prod-raw-114743615542",
            "journalm8-prod-frontend-114743615542",
            "journalm8/prod/stripe-",
        ):
            self.assertIn(expected, serialized)

    def test_entry_chunks_control_plane_is_exact_and_least_privilege(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-foundation"
        ]["Statement"]
        statement = next(
            item
            for item in statements
            if item["Sid"] == "ManageProductionEntryChunksTable"
        )
        self.assertEqual(
            statement["Resource"],
            (
                f"arn:aws:dynamodb:us-east-1:{ACCOUNT_ID}:"
                "table/journalm8-prod-entry-chunks"
            ),
        )
        self.assertEqual(
            set(statement["Action"]),
            {
                "dynamodb:CreateTable",
                "dynamodb:DescribeContinuousBackups",
                "dynamodb:DescribeTable",
                "dynamodb:ListTagsOfResource",
                "dynamodb:TagResource",
                "dynamodb:UpdateContinuousBackups",
                "dynamodb:UpdateTable",
            },
        )
        self.assertNotIn("dynamodb:DeleteTable", statement["Action"])

    def test_entry_chunks_runtime_policy_has_only_required_data_actions(self):
        source = (BIN_DIR / "create-resources").read_text(encoding="utf-8")
        policy_source = source.split(
            "cat > .build/lambda-app-policy.json <<POLICY\n",
            1,
        )[1].split("\nPOLICY", 1)[0]
        replacements = {
            "${AWS_REGION}": "us-east-1",
            "${ACCOUNT_ID}": ACCOUNT_ID,
            "${TABLE_NAME}": "journalm8-prod-main",
            "${ENTRY_CHUNKS_TABLE_NAME}": "journalm8-prod-entry-chunks",
            "${RAW_BUCKET}": f"journalm8-prod-raw-{ACCOUNT_ID}",
        }
        for placeholder, value in replacements.items():
            policy_source = policy_source.replace(placeholder, value)
        policy = json.loads(policy_source)
        statement = next(
            item
            for item in policy["Statement"]
            if item["Sid"] == "DynamoDBSemanticMemoryAccess"
        )

        self.assertEqual(
            set(statement["Action"]),
            {
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:Query",
                "dynamodb:BatchWriteItem",
            },
        )
        self.assertEqual(
            statement["Resource"],
            (
                f"arn:aws:dynamodb:us-east-1:{ACCOUNT_ID}:"
                "table/journalm8-prod-entry-chunks"
            ),
        )
        self.assertNotIn("*", statement["Resource"])
        self.assertNotIn("dynamodb:Scan", statement["Action"])

    def test_describe_user_pool_client_is_production_pool_scoped(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-foundation"
        ]["Statement"]
        matches = [
            statement
            for statement in statements
            if "cognito-idp:DescribeUserPoolClient" in statement["Action"]
        ]
        self.assertEqual(len(matches), 1)
        statement = matches[0]
        expected_resource = (
            f"arn:aws:cognito-idp:us-east-1:{ACCOUNT_ID}:userpool/*"
        )
        self.assertEqual(statement["Resource"], expected_resource)
        self.assertEqual(
            statement["Condition"],
            {
                "StringEquals": {
                    "aws:ResourceTag/App": "journalm8",
                    "aws:ResourceTag/Stage": "prod",
                }
            },
        )

        def authorized(resource_arn, resource_tags):
            return (
                fnmatchcase(resource_arn, expected_resource)
                and resource_tags.get("App") == "journalm8"
                and resource_tags.get("Stage") == "prod"
            )

        production_pool = (
            f"arn:aws:cognito-idp:us-east-1:{ACCOUNT_ID}:"
            "userpool/us-east-1_Production"
        )
        self.assertTrue(
            authorized(production_pool, {"App": "journalm8", "Stage": "prod"})
        )
        self.assertFalse(
            authorized(
                production_pool,
                {"App": "journalm8", "Stage": "staging"},
            )
        )
        self.assertFalse(
            authorized(
                "arn:aws:cognito-idp:us-east-1:999999999999:"
                "userpool/us-east-1_Unrelated",
                {"App": "journalm8", "Stage": "prod"},
            )
        )
        self.assertNotEqual(statement["Resource"], "*")
        self.assertNotIn("cognito-idp:*", statement["Action"])

    def test_classic_hosted_ui_branding_actions_are_exact_and_pool_scoped(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-foundation"
        ]["Statement"]
        matches = [
            statement
            for statement in statements
            if statement["Sid"] == "ManageProductionClassicHostedUiBranding"
        ]
        self.assertEqual(len(matches), 1)
        statement = matches[0]
        expected_resource = (
            f"arn:aws:cognito-idp:us-east-1:{ACCOUNT_ID}:userpool/*"
        )
        self.assertEqual(
            statement["Action"],
            [
                "cognito-idp:GetUICustomization",
                "cognito-idp:SetUICustomization",
            ],
        )
        self.assertEqual(statement["Resource"], expected_resource)
        self.assertEqual(
            statement["Condition"],
            {
                "StringEquals": {
                    "aws:ResourceTag/App": "journalm8",
                    "aws:ResourceTag/Stage": "prod",
                }
            },
        )

        def authorized(resource_arn, resource_tags):
            return (
                fnmatchcase(resource_arn, expected_resource)
                and resource_tags.get("App") == "journalm8"
                and resource_tags.get("Stage") == "prod"
            )

        self.assertTrue(
            authorized(
                f"arn:aws:cognito-idp:us-east-1:{ACCOUNT_ID}:"
                "userpool/us-east-1_Production",
                {"App": "journalm8", "Stage": "prod"},
            )
        )
        self.assertFalse(
            authorized(
                f"arn:aws:cognito-idp:us-east-1:{ACCOUNT_ID}:"
                "userpool/us-east-1_Staging",
                {"App": "journalm8", "Stage": "staging"},
            )
        )
        self.assertFalse(
            authorized(
                "arn:aws:cognito-idp:us-east-1:999999999999:"
                "userpool/us-east-1_Production",
                {"App": "journalm8", "Stage": "prod"},
            )
        )
        self.assertNotEqual(statement["Resource"], "*")
        self.assertNotIn("cognito-idp:*", statement["Action"])

    def test_resource_star_is_isolated_and_conditioned(self):
        for document in generate_policies().values():
            for statement in document["Statement"]:
                if statement["Resource"] == "*":
                    self.assertTrue(
                        set(statement["Action"]).issubset(RESOURCE_STAR_ACTIONS)
                    )
                    self.assertIn("Condition", statement)

    def test_api_gateway_access_log_delivery_control_plane_is_exact(self):
        policies = generate_policies()
        observability = policies[
            "journalm8-prod-deployer-observability"
        ]["Statement"]
        statement = next(
            item
            for item in observability
            if item["Sid"] == "ManageApiGatewayAccessLogDelivery"
        )
        approved_actions = {
            "logs:CreateLogDelivery",
            "logs:DeleteLogDelivery",
            "logs:DescribeLogGroups",
            "logs:DescribeResourcePolicies",
            "logs:GetLogDelivery",
            "logs:ListLogDeliveries",
            "logs:PutResourcePolicy",
            "logs:UpdateLogDelivery",
        }
        self.assertEqual(set(statement["Action"]), approved_actions)
        self.assertEqual(len(statement["Action"]), 8)
        self.assertEqual(statement["Resource"], "*")
        self.assertEqual(
            statement["Condition"],
            {"StringEquals": {"aws:RequestedRegion": "us-east-1"}},
        )

        unscoped_logs_statements = [
            item
            for document in policies.values()
            for item in document["Statement"]
            if item["Resource"] == "*"
            and any(action.startswith("logs:") for action in item["Action"])
        ]
        self.assertEqual(unscoped_logs_statements, [statement])

        all_logs_actions = {
            action
            for document in policies.values()
            for item in document["Statement"]
            for action in item["Action"]
            if action.startswith("logs:")
        }
        self.assertEqual(
            all_logs_actions,
            approved_actions
            | {
                "logs:CreateLogGroup",
                "logs:PutMetricFilter",
                "logs:PutRetentionPolicy",
                "logs:TagResource",
            },
        )
        for forbidden in (
            "logs:*",
            "logs:CancelExportTask",
            "logs:CreateExportTask",
            "logs:DeleteLogGroup",
            "logs:DeleteSubscriptionFilter",
            "logs:DescribeSubscriptionFilters",
            "logs:FilterLogEvents",
            "logs:GetLogEvents",
            "logs:GetLogRecord",
            "logs:GetQueryResults",
            "logs:PutSubscriptionFilter",
            "logs:StartQuery",
            "logs:StopQuery",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, all_logs_actions)

    def test_production_operations_resources_remain_exactly_scoped(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-observability"
        ]["Statement"]

        budget = next(
            item for item in statements
            if item["Sid"] == "ManageProductionBudgets"
        )
        self.assertEqual(
            set(budget["Resource"]),
            {
                (
                    f"arn:aws:budgets::{ACCOUNT_ID}:budget/"
                    "journalm8-prod-bedrock-monthly"
                ),
                (
                    f"arn:aws:budgets::{ACCOUNT_ID}:budget/"
                    "journalm8-prod-production-monthly"
                ),
            },
        )
        self.assertNotIn("*", "".join(budget["Resource"]))

        alarms = next(
            item for item in statements
            if item["Sid"] == "ManageProductionAlarms"
        )
        self.assertEqual(
            set(alarms["Action"]),
            {
                "cloudwatch:PutMetricAlarm",
                "cloudwatch:TagResource",
            },
        )
        for alarm_name in (
            "journalm8-prod-ocr-worker-errors",
            "journalm8-prod-api-gateway-5xx",
            "journalm8-prod-api-gateway-high-latency",
        ):
            alarm_arn = (
                f"arn:aws:cloudwatch:us-east-1:{ACCOUNT_ID}:alarm:{alarm_name}"
            )
            self.assertTrue(fnmatchcase(alarm_arn, alarms["Resource"]))

        describe_alarms = next(
            item for item in statements
            if item["Sid"] == "DescribeProductionAlarms"
        )
        self.assertEqual(
            describe_alarms,
            {
                "Sid": "DescribeProductionAlarms",
                "Effect": "Allow",
                "Action": ["cloudwatch:DescribeAlarms"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"aws:RequestedRegion": "us-east-1"},
                },
            },
        )

        dashboard = next(
            item for item in statements
            if item["Sid"] == "ManageProductionDashboards"
        )
        self.assertEqual(
            dashboard["Resource"],
            f"arn:aws:cloudwatch::{ACCOUNT_ID}:dashboard/journalm8-prod-*",
        )

    def test_opaque_api_ids_are_gated_by_exact_production_tags(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-compute"
        ]["Statement"]
        create = next(
            statement
            for statement in statements
            if statement["Sid"] == "CreateTaggedProductionHttpApi"
        )
        manage = next(
            statement
            for statement in statements
            if statement["Sid"] == "ManageTaggedProductionHttpApi"
        )
        self.assertEqual(create["Action"], ["apigateway:POST"])
        self.assertEqual(
            create["Resource"],
            "arn:aws:apigateway:us-east-1::/apis",
        )
        self.assertEqual(
            create["Condition"],
            {
                "StringEquals": {
                    "apigateway:Request/ApiName": "journalm8-prod-api",
                    "aws:RequestTag/App": "journalm8",
                    "aws:RequestTag/ManagedBy": "aws-cli",
                    "aws:RequestTag/Stage": "prod",
                },
                "ForAllValues:StringEquals": {
                    "aws:TagKeys": ["App", "ManagedBy", "Stage"],
                },
                "Null": {
                    "aws:RequestTag/App": "false",
                    "aws:RequestTag/ManagedBy": "false",
                    "aws:RequestTag/Stage": "false",
                    "aws:TagKeys": "false",
                },
            },
        )
        self.assertEqual(
            manage["Action"],
            ["apigateway:GET", "apigateway:PATCH", "apigateway:POST"],
        )
        self.assertEqual(
            manage["Resource"],
            "arn:aws:apigateway:us-east-1::/apis/*",
        )
        self.assertEqual(
            manage["Condition"],
            {
                "StringEquals": {
                    "aws:ResourceTag/App": "journalm8",
                    "aws:ResourceTag/Stage": "prod",
                }
            },
        )

    def test_http_api_tag_on_create_statement_is_exact_and_separate(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-compute"
        ]["Statement"]
        create = next(
            statement
            for statement in statements
            if statement["Sid"] == "CreateTaggedProductionHttpApi"
        )
        tag_on_create = next(
            statement
            for statement in statements
            if statement["Sid"] == "TagProductionHttpApiDuringCreation"
        )
        expected_resource = (
            "arn:aws:apigateway:us-east-1::/tags/"
            "arn%3Aaws%3Aapigateway%3Aus-east-1%3A%3A%2Fv2%2Fapis%2F*"
        )

        self.assertEqual(tag_on_create["Effect"], "Allow")
        self.assertEqual(tag_on_create["Action"], ["apigateway:POST"])
        self.assertEqual(tag_on_create["Resource"], expected_resource)
        self.assertEqual(
            tag_on_create["Condition"],
            {
                "StringEquals": {
                    "aws:RequestTag/App": "journalm8",
                    "aws:RequestTag/ManagedBy": "aws-cli",
                    "aws:RequestTag/Stage": "prod",
                },
                "ForAllValues:StringEquals": {
                    "aws:TagKeys": ["App", "ManagedBy", "Stage"],
                },
                "Null": {
                    "aws:RequestTag/App": "false",
                    "aws:RequestTag/ManagedBy": "false",
                    "aws:RequestTag/Stage": "false",
                    "aws:TagKeys": "false",
                },
            },
        )
        self.assertNotIn(
            "apigateway:Request/ApiName",
            tag_on_create["Condition"]["StringEquals"],
        )
        self.assertEqual(create["Resource"], "arn:aws:apigateway:us-east-1::/apis")
        self.assertEqual(
            create["Condition"]["StringEquals"][
                "apigateway:Request/ApiName"
            ],
            "journalm8-prod-api",
        )

        tag_resources = [
            statement["Resource"]
            for statement in statements
            if "/tags/" in statement["Resource"]
        ]
        self.assertEqual(
            [resource for resource in tag_resources if "%3A" in resource],
            [expected_resource],
        )
        self.assertNotIn("arn:aws:apigateway:us-east-1::/tags/*", tag_resources)

    def test_http_api_tag_on_create_rejects_noncanonical_requests(self):
        statement = next(
            statement
            for statement in generate_policies()[
                "journalm8-prod-deployer-compute"
            ]["Statement"]
            if statement["Sid"] == "TagProductionHttpApiDuringCreation"
        )

        def authorized(action, resource, request_tags):
            if action not in statement["Action"]:
                return False
            if not fnmatchcase(resource, statement["Resource"]):
                return False

            condition = statement["Condition"]
            for key, expected in condition["StringEquals"].items():
                tag_key = key.removeprefix("aws:RequestTag/")
                if request_tags.get(tag_key) != expected:
                    return False

            allowed_keys = set(
                condition["ForAllValues:StringEquals"]["aws:TagKeys"]
            )
            if not request_tags or not set(request_tags).issubset(allowed_keys):
                return False

            for key, expected_null in condition["Null"].items():
                if key == "aws:TagKeys":
                    is_null = not request_tags
                else:
                    tag_key = key.removeprefix("aws:RequestTag/")
                    is_null = tag_key not in request_tags
                if is_null != (expected_null == "true"):
                    return False
            return True

        exact_resource = (
            "arn:aws:apigateway:us-east-1::/tags/"
            "arn%3Aaws%3Aapigateway%3Aus-east-1%3A%3A%2Fv2%2Fapis%2Fprod123"
        )
        canonical_tags = {
            "App": "journalm8",
            "Stage": "prod",
            "ManagedBy": "aws-cli",
        }
        self.assertTrue(
            authorized("apigateway:POST", exact_resource, canonical_tags)
        )

        for missing_key in canonical_tags:
            with self.subTest(missing=missing_key):
                tags = dict(canonical_tags)
                del tags[missing_key]
                self.assertFalse(
                    authorized("apigateway:POST", exact_resource, tags)
                )

        tags_with_extra = {**canonical_tags, "Owner": "unapproved"}
        self.assertFalse(
            authorized("apigateway:POST", exact_resource, tags_with_extra)
        )

        incorrect_values = (
            ("App", "journalm8-dev"),
            ("App", "journalm8-staging"),
            ("Stage", "dev"),
            ("Stage", "staging"),
            ("ManagedBy", "console"),
        )
        for key, value in incorrect_values:
            with self.subTest(key=key, value=value):
                tags = dict(canonical_tags)
                tags[key] = value
                self.assertFalse(
                    authorized("apigateway:POST", exact_resource, tags)
                )

        self.assertFalse(
            authorized("apigateway:GET", exact_resource, canonical_tags)
        )
        for unrelated_resource in (
            "arn:aws:apigateway:us-east-1::/tags/*",
            "arn:aws:apigateway:us-east-1::/tags/unrelated",
            "arn:aws:apigateway:us-east-1::/apis/prod123",
        ):
            with self.subTest(resource=unrelated_resource):
                self.assertFalse(
                    authorized(
                        "apigateway:POST",
                        unrelated_resource,
                        canonical_tags,
                    )
                )

    def test_runtime_http_api_tag_endpoint_permissions_are_absent(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-compute"
        ]["Statement"]
        sids = {statement["Sid"] for statement in statements}
        self.assertNotIn("ReadProductionHttpApiTags", sids)
        self.assertNotIn("ReconcileProductionHttpApiTags", sids)

        runtime_tag_resource = (
            "arn:aws:apigateway:us-east-1::/tags/"
            "arn:aws:apigateway:us-east-1::/apis/*"
        )
        resources = [statement["Resource"] for statement in statements]
        self.assertNotIn(runtime_tag_resource, resources)
        self.assertNotIn("arn:aws:apigateway:us-east-1::/tags/*", resources)

    def test_opaque_cloudfront_writes_require_cloudformation_forwarding(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-frontend"
        ]["Statement"]
        protected_actions = {
            "cloudfront:CreateDistribution",
            "cloudfront:CreateOriginAccessControl",
            "cloudfront:DeleteOriginAccessControl",
            "cloudfront:UpdateOriginAccessControl",
        }
        matched_actions = set()
        for statement in statements:
            actions = set(statement["Action"])
            if actions & protected_actions:
                matched_actions.update(actions & protected_actions)
                self.assertEqual(
                    statement["Condition"]["ForAnyValue:StringEquals"],
                    {"aws:CalledVia": "cloudformation.amazonaws.com"},
                )
        self.assertEqual(matched_actions, protected_actions)

    def test_frontend_policy_requires_stack_tags_and_omits_prod_basic_auth(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-frontend"
        ]["Statement"]
        change_set = next(
            statement
            for statement in statements
            if statement["Sid"] == "CreateTaggedProductionFrontendChangeSet"
        )
        self.assertEqual(change_set["Action"], ["cloudformation:CreateChangeSet"])
        self.assertEqual(
            change_set["Condition"]["StringEquals"],
            {
                "aws:RequestTag/App": "journalm8",
                "aws:RequestTag/ManagedBy": "aws-cli",
                "aws:RequestTag/Stage": "prod",
            },
        )
        actions = {
            action
            for statement in statements
            for action in statement["Action"]
        }
        self.assertNotIn("cloudfront:CreateFunction", actions)
        self.assertFalse(
            any(action.endswith("Function") for action in actions)
        )
        self.assertIn("s3:GetBucketPolicy", actions)
        self.assertIn("s3:GetBucketTagging", actions)
        self.assertIn("cloudfront:ListTagsForResource", actions)

    def test_only_inspected_deployment_services_are_authorized(self):
        expected_services = {
            "apigateway",
            "bedrock",
            "budgets",
            "cloudformation",
            "cloudfront",
            "cloudwatch",
            "cognito-idp",
            "dynamodb",
            "iam",
            "lambda",
            "logs",
            "s3",
            "secretsmanager",
            "sns",
            "sqs",
            "states",
        }
        actions = {
            action
            for document in generate_policies().values()
            for statement in document["Statement"]
            for action in statement["Action"]
        }
        self.assertEqual(
            {action.split(":", 1)[0] for action in actions},
            expected_services,
        )

    def test_policy_services_match_repository_deployment_script_inventory(self):
        script_names = (
            "create-resources",
            "create-auth",
            "create-api",
            "secure-api",
            "deploy",
            "configure-bedrock-analyzer",
            "deploy-ocr-workflow",
            "deploy-historical-reanalysis-workflow",
            "create-frontend-hosting",
            "deploy-frontend",
            "deploy-observability",
            "deploy-account-export",
            "deploy-semantic-memory",
            "deploy-semantic-embedding",
            "deploy-analysis-observability",
            "deploy-bedrock-budget",
            "deploy-production-budget",
            "provision-stripe-secret",
            "setup-stripe-catalog",
            "jm8_bedrock_analysis_policy.sh",
        )
        cli_services = set()
        for script_name in script_names:
            source = (BIN_DIR / script_name).read_text(encoding="utf-8")
            flattened = re.sub(r"\\\n\s*", " ", source)
            cli_services.update(
                re.findall(r"\baws\s+([a-z0-9-]+)\s+[a-z0-9-]+", flattened)
            )
        cli_services.discard("sts")
        normalized = {
            {
                "apigatewayv2": "apigateway",
                "s3api": "s3",
                "stepfunctions": "states",
            }.get(service, service)
            for service in cli_services
        }
        generated_services = {
            action.split(":", 1)[0]
            for document in generate_policies().values()
            for statement in document["Statement"]
            for action in statement["Action"]
        }
        self.assertEqual(generated_services, normalized)

    def test_nontrivial_cli_to_iam_action_mappings_are_canonical(self):
        actions = {
            action
            for document in generate_policies().values()
            for statement in document["Statement"]
            for action in statement["Action"]
        }
        for required in (
            "budgets:ModifyBudget",
            "budgets:ViewBudget",
            "s3:GetEncryptionConfiguration",
            "s3:GetLifecycleConfiguration",
            "s3:PutEncryptionConfiguration",
            "s3:PutLifecycleConfiguration",
            "dynamodb:TagResource",
            "lambda:TagResource",
            "secretsmanager:TagResource",
            "states:TagResource",
        ):
            self.assertIn(required, actions)
        for api_operation_name in (
            "budgets:CreateBudget",
            "budgets:CreateNotification",
            "budgets:DescribeBudget",
            "s3:GetBucketEncryption",
            "s3:PutBucketEncryption",
        ):
            self.assertNotIn(api_operation_name, actions)

    def test_iam_policy_cannot_modify_or_attach_to_deployer(self):
        document = generate_policies()["journalm8-prod-deployer-iam"]
        serialized = compact_json(document)
        self.assertNotIn(DEPLOYER_ROLE_ARN, serialized)
        self.assertNotIn("iam:CreatePolicy", serialized)
        self.assertNotIn("iam:CreatePolicyVersion", serialized)
        self.assertNotIn("iam:SetDefaultPolicyVersion", serialized)
        self.assertNotIn("iam:UpdateRole", serialized)
        self.assertNotIn("iam:CreateAccessKey", serialized)

    def test_pass_role_has_exact_runtime_roles_and_service_conditions(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-iam"
        ]["Statement"]
        pass_statements = [
            statement
            for statement in statements
            if statement["Action"] == ["iam:PassRole"]
        ]
        self.assertEqual(len(pass_statements), 2)
        services = {
            statement["Condition"]["StringEquals"]["iam:PassedToService"]
            for statement in pass_statements
        }
        self.assertEqual(services, {"lambda.amazonaws.com", "states.amazonaws.com"})
        for statement in pass_statements:
            resources = statement["Resource"]
            self.assertIsInstance(resources, list)
            for resource in resources:
                self.assertTrue(
                    resource.startswith(
                        f"arn:aws:iam::{ACCOUNT_ID}:role/journalm8-prod-"
                    )
                )
                self.assertNotIn("*", resource)
                self.assertNotEqual(resource, DEPLOYER_ROLE_ARN)

    def test_lambda_basic_policy_attachment_excludes_workflow_roles(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-iam"
        ]["Statement"]
        attach = next(
            statement
            for statement in statements
            if statement["Sid"] == "AttachOnlyLambdaBasicExecutionPolicy"
        )
        self.assertEqual(
            attach["Condition"],
            {"ArnEquals": {"iam:PolicyARN": (
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            )}},
        )
        self.assertEqual(
            set(attach["Resource"]),
            {
                f"arn:aws:iam::{ACCOUNT_ID}:role/journalm8-prod-lambda-basic-role",
                f"arn:aws:iam::{ACCOUNT_ID}:role/journalm8-prod-ocr-worker-role",
                (
                    f"arn:aws:iam::{ACCOUNT_ID}:role/"
                    "journalm8-prod-historical-reanalysis-worker-role"
                ),
                (
                    f"arn:aws:iam::{ACCOUNT_ID}:role/"
                    "journalm8-prod-historical-reanalysis-coordinator-role"
                ),
                (
                    f"arn:aws:iam::{ACCOUNT_ID}:role/"
                    "journalm8-prod-account-export-worker-role"
                ),
                (
                    f"arn:aws:iam::{ACCOUNT_ID}:role/"
                    "journalm8-prod-account-deletion-worker-role"
                ),
                (
                    f"arn:aws:iam::{ACCOUNT_ID}:role/"
                    "journalm8-prod-semantic-memory-worker-role"
                ),
                (
                    f"arn:aws:iam::{ACCOUNT_ID}:role/"
                    "journalm8-prod-semantic-embedding-worker-role"
                ),
            },
        )

    def test_account_deletion_roles_are_exactly_authorized(self):
        statements = generate_policies()[
            "journalm8-prod-deployer-iam"
        ]["Statement"]
        managed = next(
            statement
            for statement in statements
            if statement["Sid"] == "ManageOnlyProductionRuntimeRoles"
        )
        self.assertIn(
            f"arn:aws:iam::{ACCOUNT_ID}:role/"
            "journalm8-prod-account-deletion-worker-role",
            managed["Resource"],
        )
        self.assertIn(
            f"arn:aws:iam::{ACCOUNT_ID}:role/"
            "journalm8-prod-account-deletion-step-role",
            managed["Resource"],
        )
        step_pass = next(
            statement
            for statement in statements
            if statement["Sid"] == "PassOnlyProductionWorkflowRolesToStepFunctions"
        )
        self.assertIn(
            f"arn:aws:iam::{ACCOUNT_ID}:role/"
            "journalm8-prod-account-deletion-step-role",
            step_pass["Resource"],
        )

    def test_generated_documents_contain_no_secret_material(self):
        serialized = compact_json(generate_policies())
        for forbidden in (
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "sk_live_",
            "sk_test_",
            "whsec_",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_deployer_cannot_directly_execute_account_deletion(self):
        policies = generate_policies()
        forbidden_actions = {
            "cognito-idp:AdminDeleteUser",
            "cognito-idp:AdminUserGlobalSignOut",
            "dynamodb:DeleteItem",
            "dynamodb:BatchWriteItem",
            "lambda:InvokeFunction",
            "states:StartExecution",
            "states:StopExecution",
        }
        all_actions = {
            action
            for document in policies.values()
            for statement in document["Statement"]
            for action in statement["Action"]
        }
        self.assertTrue(forbidden_actions.isdisjoint(all_actions))

        for document in policies.values():
            for statement in document["Statement"]:
                resources = statement["Resource"]
                if isinstance(resources, str):
                    resources = [resources]
                if any(
                    "journalm8-prod-raw-" in resource
                    or "journalm8-prod-exports-" in resource
                    for resource in resources
                ):
                    self.assertNotIn("s3:DeleteObjectVersion", statement["Action"])


class ReconciliationTests(unittest.TestCase):
    def _seed_canonical(self, aws: FakeAws) -> dict[str, dict]:
        policies = generate_policies()
        for name, document in policies.items():
            aws.seed_policy(name, document)
        return policies

    def test_generate_mode_performs_no_aws_mutation(self):
        aws = FakeAws()
        with tempfile.TemporaryDirectory() as directory:
            execute("generate", aws, Path(directory), "us-east-1")
        self.assertEqual(aws.mutations, [])

    @patch(
        "jm8_production_deployer_policies.generate_policies",
        side_effect=ProductionDeployerError("policy exceeds 6,144 characters"),
    )
    def test_generation_failure_occurs_before_any_aws_call(self, mock_generate):
        aws = FakeAws()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProductionDeployerError, "6,144"):
                execute("apply", aws, Path(directory), "us-east-1")
        self.assertEqual(aws.calls, [])
        mock_generate.assert_called_once_with("us-east-1")

    def test_first_apply_attaches_only_to_exact_deployer_role(self):
        aws = FakeAws()
        with tempfile.TemporaryDirectory() as directory:
            execute("apply", aws, Path(directory), "us-east-1")

        attachments = [
            call for call in aws.mutations if call[:2] == ("iam", "attach-role-policy")
        ]
        self.assertEqual(len(attachments), 6)
        for _, _, arguments in attachments:
            self.assertEqual(_argument(arguments, "--role-name"), DEPLOYER_ROLE_NAME)
        self.assertEqual(aws.attached, {policy_arn(name) for name in POLICY_NAMES})

    def test_idempotent_second_apply_has_no_mutations(self):
        aws = FakeAws()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            execute("apply", aws, output, "us-east-1")
            aws.calls.clear()
            execute("apply", aws, output, "us-east-1")
        self.assertEqual(aws.mutations, [])

    def test_changed_policy_creates_one_new_default_version(self):
        aws = FakeAws()
        policies = self._seed_canonical(aws)
        target_name = POLICY_NAMES[0]
        target_arn = policy_arn(target_name)
        aws.policies[target_arn]["Versions"][0]["Document"] = {
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "Old",
                "Effect": "Allow",
                "Action": ["dynamodb:DescribeTable"],
                "Resource": "*",
            }],
        }

        inventory = preflight(aws, policies)
        apply_policies(aws, policies, inventory)
        verify_applied_policies(aws, policies)

        creates = [
            call for call in aws.mutations if call[:2] == ("iam", "create-policy-version")
        ]
        self.assertEqual(len(creates), 1)
        versions = aws.policies[target_arn]["Versions"]
        self.assertEqual(sum(item["IsDefaultVersion"] for item in versions), 1)
        default = next(item for item in versions if item["IsDefaultVersion"])
        self.assertEqual(compact_json(default["Document"]), compact_json(policies[target_name]))

    def test_five_version_rollover_deletes_only_oldest_non_default(self):
        aws = FakeAws()
        policies = generate_policies()
        for name, document in policies.items():
            aws.seed_policy(
                name,
                document,
                version_count=5 if name == POLICY_NAMES[0] else 1,
            )
        target_arn = policy_arn(POLICY_NAMES[0])
        aws.policies[target_arn]["Versions"][-1]["Document"] = {
            "Version": "2012-10-17",
            "Statement": [],
        }

        inventory = preflight(aws, policies)
        apply_policies(aws, policies, inventory)

        deletes = [
            call for call in aws.mutations if call[:2] == ("iam", "delete-policy-version")
        ]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(_argument(deletes[0][2], "--version-id"), "v1")
        self.assertNotEqual(_argument(deletes[0][2], "--version-id"), "v5")
        self.assertLess(
            aws.calls.index(deletes[0]),
            next(
                index
                for index, call in enumerate(aws.calls)
                if call[:2] == ("iam", "create-policy-version")
            ),
        )

    def test_version_creation_failure_preserves_previous_default_at_limit(self):
        aws = FakeAws()
        policies = generate_policies()
        for name, document in policies.items():
            aws.seed_policy(
                name,
                document,
                version_count=5 if name == POLICY_NAMES[0] else 1,
            )
        target_arn = policy_arn(POLICY_NAMES[0])
        aws.policies[target_arn]["Versions"][-1]["Document"] = {
            "Version": "2012-10-17",
            "Statement": [],
        }
        aws.fail_operation = ("iam", "create-policy-version")
        inventory = preflight(aws, policies)

        with self.assertRaises(ProductionDeployerError):
            apply_policies(aws, policies, inventory)

        default = next(
            item
            for item in aws.policies[target_arn]["Versions"]
            if item["IsDefaultVersion"]
        )
        self.assertEqual(default["VersionId"], "v5")
        self.assertNotIn(
            "v1",
            {
                item["VersionId"]
                for item in aws.policies[target_arn]["Versions"]
            },
        )

    def test_applied_document_mismatch_fails_verification(self):
        aws = FakeAws()
        policies = self._seed_canonical(aws)
        aws.mismatch_document_for = policy_arn(POLICY_NAMES[2])
        with self.assertRaisesRegex(ProductionDeployerError, "does not match"):
            verify_applied_policies(aws, policies)

    def test_quota_failure_occurs_before_mutation(self):
        aws = FakeAws(quota=5)
        policies = generate_policies()
        with self.assertRaisesRegex(ProductionDeployerError, "quota"):
            preflight(aws, policies)
        self.assertEqual(aws.mutations, [])

    def test_unexpected_existing_attachment_fails_before_mutation(self):
        aws = FakeAws()
        policies = self._seed_canonical(aws)
        unexpected_arn = f"arn:aws:iam::{ACCOUNT_ID}:policy/unrelated"
        aws.policies[unexpected_arn] = {
            "Name": "unrelated",
            "Path": "/",
            "Versions": [],
            "Roles": {DEPLOYER_ROLE_NAME},
            "Users": set(),
            "Groups": set(),
        }
        aws.attached.add(unexpected_arn)
        with self.assertRaisesRegex(ProductionDeployerError, "unexpected"):
            preflight(aws, policies)
        self.assertEqual(aws.mutations, [])

    def test_policy_attached_to_other_identity_fails_closed(self):
        aws = FakeAws()
        policies = self._seed_canonical(aws)
        aws.policies[policy_arn(POLICY_NAMES[0])]["Users"].add("other-user")
        with self.assertRaisesRegex(ProductionDeployerError, "outside"):
            preflight(aws, policies)
        self.assertEqual(aws.mutations, [])

    def test_wrong_identity_or_role_fails_closed(self):
        for attribute, value in (
            ("account", "999999999999"),
            ("role_arn", f"arn:aws:iam::{ACCOUNT_ID}:role/unrelated"),
        ):
            with self.subTest(attribute=attribute):
                aws = FakeAws()
                setattr(aws, attribute, value)
                with self.assertRaises(ProductionDeployerError):
                    verify_identity_and_role(aws)
                self.assertEqual(aws.mutations, [])

    def test_access_denied_is_not_interpreted_as_missing_policy(self):
        aws = FakeAws()
        aws.fail_operation = ("iam", "get-policy")
        with self.assertRaisesRegex(ProductionDeployerError, "AccessDenied"):
            preflight(aws, generate_policies())
        self.assertEqual(aws.mutations, [])


class CliAndShellSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provisioner = PROVISIONER.read_text(encoding="utf-8")
        cls.helper = HELPER.read_text(encoding="utf-8")

    @patch("jm8_production_deployer_policies.subprocess.run")
    def test_aws_cli_rejects_malformed_response(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not-json", stderr="")
        with self.assertRaisesRegex(ProductionDeployerError, "malformed"):
            AwsCli("jm8-dev", "us-east-1").call("sts", "get-caller-identity")

    @patch("jm8_production_deployer_policies.subprocess.run")
    def test_aws_cli_failure_is_secret_safe_and_fail_closed(self, mock_run):
        secret = "EXAMPLE_SECRET_CREDENTIAL"
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr=f"AccessDenied ({secret})",
        )
        with self.assertRaises(ProductionDeployerError) as context:
            AwsCli("jm8-dev", "us-east-1").call("iam", "get-role")
        self.assertNotIn(secret, str(context.exception))

    @patch(
        "jm8_production_deployer_policies.subprocess.run",
        side_effect=OSError("network unavailable"),
    )
    def test_aws_cli_network_failure_is_fail_closed(self, mock_run):
        with self.assertRaisesRegex(ProductionDeployerError, "could not run"):
            AwsCli("jm8-dev", "us-east-1").call("iam", "get-role")
        mock_run.assert_called_once()

    def test_provisioner_requires_jm8_dev_and_exact_account(self):
        self.assertIn('if [ "$AWS_PROFILE" != "jm8-dev" ]', self.provisioner)
        self.assertIn(
            'if [ "$EXPECTED_AWS_ACCOUNT_ID" != "114743615542" ]',
            self.provisioner,
        )
        self.assertIn("generate|apply|verify", self.provisioner)

    def test_exact_attachment_target_is_constant_and_not_caller_controlled(self):
        self.assertIn('DEPLOYER_ROLE_NAME = "journalm8-prod-deployer"', self.helper)
        self.assertIn(DEPLOYER_ROLE_ARN, self.helper)
        self.assertNotIn("--role-name", self.provisioner)

    def test_production_deployer_receives_no_inline_policy(self):
        for forbidden in (
            "put-role-policy",
            "put-user-policy",
            "put-group-policy",
        ):
            self.assertNotIn(forbidden, self.provisioner.lower())
            self.assertNotIn(forbidden, self.helper.lower())

    def test_helper_rejects_wrong_profile_and_account_before_aws(self):
        cases = (
            ("jm8-prod", ACCOUNT_ID, "AWS_PROFILE=jm8-dev"),
            ("jm8-dev", "999999999999", "approved AWS account"),
        )
        for profile, account, expected_error in cases:
            with self.subTest(profile=profile, account=account):
                with tempfile.TemporaryDirectory() as directory:
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(HELPER),
                            "generate",
                            "--aws-profile",
                            profile,
                            "--aws-region",
                            "us-east-1",
                            "--expected-account-id",
                            account,
                            "--output-dir",
                            directory,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)

    def test_no_weakened_shell_or_suppressed_failure_behavior(self):
        for source in (self.provisioner, self.helper):
            self.assertNotIn("|| true", source)
            self.assertNotIn("eval ", source)
            self.assertNotIn("set -x", source)

    def test_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(PROVISIONER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
