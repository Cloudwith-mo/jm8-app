#!/usr/bin/env python3
"""Generate, apply, and verify the JM8 GitHub Actions OIDC boundary."""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from jm8_production_deployer_policies import (
    ACCOUNT_ID,
    POLICY_SIZE_LIMIT,
    compact_json,
    generate_policies,
)


APP_NAME = "journalm8"
REPOSITORY = "Cloudwith-mo/jm8-app"
PROVISIONING_PROFILE = "jm8-dev"
STAGES = ("dev", "staging", "prod")
PROVIDER_STACK_NAME = "journalm8-github-oidc-provider"
OIDC_HOST = "token.actions.githubusercontent.com"
OIDC_URL = f"https://{OIDC_HOST}"
OIDC_AUDIENCE = "sts.amazonaws.com"
COMPLETE_STACK_STATES = {"CREATE_COMPLETE", "UPDATE_COMPLETE"}
SAFE_ERROR_CODES = {
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


class OidcBootstrapError(RuntimeError):
    """Raised when the OIDC boundary cannot be proven safe."""


def role_name(stage: str) -> str:
    return f"{APP_NAME}-{stage}-github-deployer"


def role_stack_name(stage: str) -> str:
    return role_name(stage)


def role_arn(stage: str) -> str:
    return f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name(stage)}"


def policy_path(stage: str) -> str:
    return f"/{APP_NAME}/{stage}/github-deployer/"


def policy_name(stage: str, group: str) -> str:
    return f"{APP_NAME}-{stage}-github-deployer-{group}"


def policy_arn(stage: str, group: str) -> str:
    return f"arn:aws:iam::{ACCOUNT_ID}:policy{policy_path(stage)}{policy_name(stage, group)}"


def github_subject(stage: str) -> str:
    return f"repo:{REPOSITORY}:environment:jm8-{stage}"


def trust_policy(stage: str) -> dict[str, Any]:
    if stage not in STAGES:
        raise OidcBootstrapError("Unsupported deployment stage.")
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {
                "Federated": (
                    f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/{OIDC_HOST}"
                )
            },
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
                "StringEquals": {
                    f"{OIDC_HOST}:aud": OIDC_AUDIENCE,
                    f"{OIDC_HOST}:sub": github_subject(stage),
                }
            },
        }],
    }


def _transform(value: Any, stage: str) -> Any:
    if isinstance(value, str):
        if value == "prod":
            return stage
        return value.replace(
            f"{APP_NAME}-prod", f"{APP_NAME}-{stage}"
        ).replace(f"{APP_NAME}/prod/", f"{APP_NAME}/{stage}/")
    if isinstance(value, list):
        return [_transform(item, stage) for item in value]
    if isinstance(value, dict):
        return {key: _transform(item, stage) for key, item in value.items()}
    return value


def stage_policies(stage: str, region: str) -> dict[str, dict[str, Any]]:
    """Reuse the reviewed deployment inventory with exact stage resources."""
    if stage not in STAGES:
        raise OidcBootstrapError("Unsupported deployment stage.")
    source = generate_policies(region)
    result: dict[str, dict[str, Any]] = {}
    for source_name, document in source.items():
        group = source_name.rsplit("-", 1)[-1]
        result[group] = _transform(copy.deepcopy(document), stage)
    # API Gateway authorizes GetTags against its tag endpoint, not /apis/*.
    # Keep this read-only grant tied to the verified existing dev API inventory.
    if stage == "dev" and region == "us-east-1":
        result["compute"]["Statement"].append({
            "Sid": "ReadExactDevHttpApiTags",
            "Effect": "Allow",
            "Action": ["apigateway:GET"],
            "Resource": (
                "arn:aws:apigateway:us-east-1::/tags/"
                "arn:aws:apigateway:us-east-1::/apis/u06tdrfsua"
            ),
        })
    if stage == "dev" and region == "us-east-1":
        api = "arn:aws:apigateway:us-east-1::/apis/u06tdrfsua"
        result["compute"]["Statement"].extend([
            {
                "Sid": "WriteExactDevHttpApiTags",
                "Effect": "Allow",
                "Action": ["apigateway:POST"],
                "Resource": f"arn:aws:apigateway:us-east-1::/tags/{api}",
                "Condition": {
                    "StringEquals": {
                        "aws:RequestTag/App": APP_NAME,
                        "aws:RequestTag/Stage": "dev",
                        "aws:RequestTag/ManagedBy": "aws-cli",
                    },
                    "ForAllValues:StringEquals": {
                        "aws:TagKeys": ["App", "Stage", "ManagedBy"],
                    },
                },
            },
            # HTTP API child resources do not inherit REST API resource tags.
            # Pin the API ID and enumerate only collections used by our scripts.
            {
                "Sid": "ManageExactDevHttpApiCollections",
                "Effect": "Allow",
                "Action": ["apigateway:GET", "apigateway:POST"],
                "Resource": [f"{api}/{kind}" for kind in
                             ("integrations", "routes", "authorizers", "stages")],
            },
            {
                "Sid": "ManageExactDevHttpApiChildren",
                "Effect": "Allow",
                "Action": ["apigateway:GET", "apigateway:PATCH"],
                "Resource": [f"{api}/{kind}/*" for kind in
                             ("integrations", "routes", "authorizers")]
                            + [f"{api}/stages/$default"],
            },
        ])
    validate_stage_policies(stage, result)
    return result


def validate_stage_policies(
    stage: str,
    policies: dict[str, dict[str, Any]],
) -> None:
    if stage not in STAGES:
        raise OidcBootstrapError("Unsupported deployment stage.")
    expected_groups = {
        "foundation", "compute", "iam", "frontend", "observability", "secrets"
    }
    if set(policies) != expected_groups:
        raise OidcBootstrapError("Stage policy groups are not canonical.")
    serialized = compact_json(policies)
    for other_stage in set(STAGES) - {stage}:
        if (
            f"{APP_NAME}-{other_stage}" in serialized
            or f"{APP_NAME}/{other_stage}/" in serialized
        ):
            raise OidcBootstrapError("Stage policy crosses an environment boundary.")
    if role_arn(stage) in serialized:
        raise OidcBootstrapError("A deployment policy permits role self-modification.")
    for group, document in policies.items():
        if len(compact_json(document)) > POLICY_SIZE_LIMIT:
            raise OidcBootstrapError(
                f"Managed policy {group} exceeds the IAM size limit."
            )


def _logical_id(prefix: str, stage: str, group: str = "") -> str:
    words = [prefix, stage, group]
    return "".join(word.title() for word in words if word)


def generate_templates(region: str) -> dict[str, dict[str, Any]]:
    provider_template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "JM8 GitHub Actions OIDC provider",
        "Resources": {
        "GitHubOidcProvider": {
            "Type": "AWS::IAM::OIDCProvider",
            "Properties": {
                "Url": OIDC_URL,
                "ClientIdList": [OIDC_AUDIENCE],
                "Tags": [
                    {"Key": "App", "Value": APP_NAME},
                    {"Key": "ManagedBy", "Value": "cloudformation"},
                ],
            },
        }},
        "Outputs": {
            "ProviderArn": {"Value": {"Ref": "GitHubOidcProvider"}},
        },
    }
    templates = {"provider": provider_template}
    for stage in STAGES:
        resources: dict[str, Any] = {}
        policy_refs = []
        for group, document in stage_policies(stage, region).items():
            logical_id = _logical_id("policy", stage, group)
            resources[logical_id] = {
                "Type": "AWS::IAM::ManagedPolicy",
                "Properties": {
                    "ManagedPolicyName": policy_name(stage, group),
                    "Path": policy_path(stage),
                    "Description": f"JM8 {stage} GitHub deployer permissions: {group}",
                    "PolicyDocument": document,
                },
            }
            policy_refs.append({"Ref": logical_id})
        role_id = _logical_id("role", stage)
        resources[role_id] = {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "RoleName": role_name(stage),
                "Description": f"GitHub Actions deployer for the JM8 {stage} environment",
                "MaxSessionDuration": 3600,
                "AssumeRolePolicyDocument": trust_policy(stage),
                "ManagedPolicyArns": policy_refs,
                "Tags": [
                    {"Key": "App", "Value": APP_NAME},
                    {"Key": "Stage", "Value": stage},
                    {"Key": "ManagedBy", "Value": "cloudformation"},
                ],
            },
        }
        templates[stage] = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Description": f"JM8 {stage} GitHub Actions deployment role",
            "Resources": resources,
            "Outputs": {
                "RoleArn": {"Value": {"Fn::GetAtt": [role_id, "Arn"]}},
            },
        }
    return templates


def write_artifacts(
    templates: dict[str, dict[str, Any]], output_dir: Path, region: str
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, template in templates.items():
        template_path = output_dir / f"github-oidc-{name}.template.json"
        template_path.write_text(
            json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        paths[name] = template_path
    manifest = {
        "accountId": ACCOUNT_ID,
        "region": region,
        "repository": REPOSITORY,
        "providerStackName": PROVIDER_STACK_NAME,
        "providerUrl": OIDC_URL,
        "roles": [
            {"stage": stage, "stackName": role_stack_name(stage),
             "name": role_name(stage), "arn": role_arn(stage),
             "subject": github_subject(stage)}
            for stage in STAGES
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return paths


class AwsCli:
    def __init__(self, profile: str, region: str) -> None:
        self.profile = profile
        self.region = region

    def run(self, service: str, operation: str, *arguments: str) -> dict[str, Any]:
        command = ["aws", service, operation, *arguments, "--profile", self.profile,
                   "--region", self.region, "--output", "json"]
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OidcBootstrapError(
                f"AWS command could not run: {service} {operation}."
            ) from error
        if result.returncode != 0:
            match = re.search(r"\(([A-Za-z0-9]+)\)", result.stderr or "")
            candidate = match.group(1) if match else ""
            code = candidate if candidate in SAFE_ERROR_CODES else "AwsCliError"
            raise OidcBootstrapError(
                f"AWS command failed: {service} {operation} ({code})."
            )
        try:
            value = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise OidcBootstrapError(
                f"AWS response was malformed: {service} {operation}."
            ) from error
        if not isinstance(value, dict):
            raise OidcBootstrapError(
                f"AWS response was malformed: {service} {operation}."
            )
        return value

    def deploy(self, stack_name: str, template_path: Path) -> None:
        command = [
            "aws", "cloudformation", "deploy", "--stack-name", stack_name,
            "--template-file", str(template_path), "--capabilities",
            "CAPABILITY_NAMED_IAM", "--no-fail-on-empty-changeset",
            "--tags", f"App={APP_NAME}", "ManagedBy=cloudformation",
            "--profile", self.profile, "--region", self.region,
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=900, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OidcBootstrapError("CloudFormation deployment could not run.") from error
        if result.returncode != 0:
            raise OidcBootstrapError("CloudFormation deployment failed; inspect stack events.")


def _decode_policy(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(unquote(value))
        except json.JSONDecodeError as error:
            raise OidcBootstrapError("IAM policy document is malformed.") from error
    if not isinstance(value, dict):
        raise OidcBootstrapError("IAM policy document is malformed.")
    return value


def verify_identity(aws: AwsCli) -> None:
    identity = aws.run("sts", "get-caller-identity")
    arn = identity.get("Arn")
    if identity.get("Account") != ACCOUNT_ID or not isinstance(arn, str):
        raise OidcBootstrapError("AWS caller is not in the approved account.")
    if not re.fullmatch(
        rf"arn:aws:(?:iam|sts)::{ACCOUNT_ID}:(?:user|role|assumed-role)/[^\s]+", arn
    ):
        raise OidcBootstrapError("AWS caller identity is malformed.")


def _stack(aws: AwsCli, stack_name: str) -> dict[str, Any] | None:
    try:
        response = aws.run("cloudformation", "describe-stacks", "--stack-name", stack_name)
    except OidcBootstrapError as error:
        if "(ValidationError)" in str(error):
            return None
        raise
    stacks = response.get("Stacks")
    if not isinstance(stacks, list) or len(stacks) != 1 or not isinstance(stacks[0], dict):
        raise OidcBootstrapError("CloudFormation stack response is malformed.")
    stack = stacks[0]
    expected_stack_arn = rf"arn:aws:cloudformation:[^:]+:{ACCOUNT_ID}:stack/{re.escape(stack_name)}/[^\s]+"
    tags = {
        item.get("Key"): item.get("Value")
        for item in stack.get("Tags", [])
        if isinstance(item, dict)
    }
    if (
        not isinstance(stack.get("StackId"), str)
        or not re.fullmatch(expected_stack_arn, stack["StackId"])
        or tags != {"App": APP_NAME, "ManagedBy": "cloudformation"}
    ):
        raise OidcBootstrapError("CloudFormation stack identity is incorrect.")
    return stack


def inspect_bootstrap_state(aws: AwsCli) -> dict[str, bool]:
    """Validate all existing components and reject unmanaged collisions."""
    state = {"provider": _stack(aws, PROVIDER_STACK_NAME) is not None}
    state.update({stage: _stack(aws, role_stack_name(stage)) is not None for stage in STAGES})
    providers = aws.run("iam", "list-open-id-connect-providers").get(
        "OpenIDConnectProviderList"
    )
    if not isinstance(providers, list):
        raise OidcBootstrapError("OIDC provider inventory is malformed.")
    expected_provider = f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/{OIDC_HOST}"
    provider_exists = any(
        isinstance(item, dict) and item.get("Arn") == expected_provider
        for item in providers
    )
    if provider_exists != state["provider"]:
        raise OidcBootstrapError("GitHub OIDC provider exists outside the managed stack.")
    for stage in STAGES:
        role_exists = True
        try:
            aws.run("iam", "get-role", "--role-name", role_name(stage))
        except OidcBootstrapError as error:
            if "(NoSuchEntity)" in str(error):
                role_exists = False
            else:
                raise
        if role_exists != state[stage]:
            raise OidcBootstrapError(
                f"Role {role_name(stage)} exists outside the managed stack."
            )
    return state


def verify_boundary(aws: AwsCli, region: str) -> None:
    stack = _stack(aws, PROVIDER_STACK_NAME)
    if stack is None or stack.get("StackStatus") not in COMPLETE_STACK_STATES:
        raise OidcBootstrapError("OIDC bootstrap stack is absent or not complete.")
    provider_arn = f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/{OIDC_HOST}"
    provider = aws.run(
        "iam", "get-open-id-connect-provider", "--open-id-connect-provider-arn",
        provider_arn,
    )
    if (
        provider.get("Url") != OIDC_HOST
        or provider.get("ClientIDList") != [OIDC_AUDIENCE]
    ):
        raise OidcBootstrapError("GitHub OIDC provider configuration is incorrect.")

    for stage in STAGES:
        stage_stack = _stack(aws, role_stack_name(stage))
        if stage_stack is None or stage_stack.get("StackStatus") not in COMPLETE_STACK_STATES:
            raise OidcBootstrapError(f"OIDC role stack is absent or not complete for {stage}.")
        response = aws.run("iam", "get-role", "--role-name", role_name(stage))
        role = response.get("Role")
        if not isinstance(role, dict) or role.get("Arn") != role_arn(stage):
            raise OidcBootstrapError(f"Role identity is incorrect for {stage}.")
        if compact_json(_decode_policy(role.get("AssumeRolePolicyDocument"))) != compact_json(trust_policy(stage)):
            raise OidcBootstrapError(f"Role trust is incorrect for {stage}.")
        expected_tags = {
            "App": APP_NAME,
            "Stage": stage,
            "ManagedBy": "cloudformation",
        }
        actual_tags = {
            item.get("Key"): item.get("Value")
            for item in role.get("Tags", [])
            if isinstance(item, dict)
        }
        if actual_tags != expected_tags:
            raise OidcBootstrapError(f"Role tags are incorrect for {stage}.")
        inline = aws.run("iam", "list-role-policies", "--role-name", role_name(stage))
        if inline.get("PolicyNames") != []:
            raise OidcBootstrapError(f"Unexpected inline policy on {stage} role.")
        expected = {policy_arn(stage, group) for group in stage_policies(stage, region)}
        attached_response = aws.run(
            "iam", "list-attached-role-policies", "--role-name", role_name(stage)
        )
        attached = attached_response.get("AttachedPolicies")
        if not isinstance(attached, list) or attached_response.get("IsTruncated") is True:
            raise OidcBootstrapError("Attached policy response is malformed.")
        actual = {
            item.get("PolicyArn") for item in attached if isinstance(item, dict)
        }
        if actual != expected or len(actual) != len(attached):
            raise OidcBootstrapError(f"Managed policy attachments are incorrect for {stage}.")
        for group, expected_document in stage_policies(stage, region).items():
            arn = policy_arn(stage, group)
            metadata = aws.run("iam", "get-policy", "--policy-arn", arn).get("Policy")
            if (
                not isinstance(metadata, dict)
                or metadata.get("Arn") != arn
                or metadata.get("Path") != policy_path(stage)
                or metadata.get("PolicyName") != policy_name(stage, group)
            ):
                raise OidcBootstrapError(f"Managed policy metadata is incorrect for {stage}.")
            version_id = metadata.get("DefaultVersionId")
            if not isinstance(version_id, str) or not re.fullmatch(r"v[1-9][0-9]*", version_id):
                raise OidcBootstrapError("Managed policy version is malformed.")
            version = aws.run(
                "iam", "get-policy-version", "--policy-arn", arn,
                "--version-id", version_id,
            ).get("PolicyVersion")
            if not isinstance(version, dict):
                raise OidcBootstrapError("Managed policy version is malformed.")
            if compact_json(_decode_policy(version.get("Document"))) != compact_json(expected_document):
                raise OidcBootstrapError(f"Managed policy document is incorrect for {stage}.")
            entities = aws.run("iam", "list-entities-for-policy", "--policy-arn", arn)
            policy_roles = entities.get("PolicyRoles")
            attached_role_names = {
                item.get("RoleName")
                for item in policy_roles
                if isinstance(item, dict)
            } if isinstance(policy_roles, list) else set()
            if (
                entities.get("PolicyGroups") != []
                or entities.get("PolicyUsers") != []
                or attached_role_names != {role_name(stage)}
                or not isinstance(policy_roles, list)
                or len(policy_roles) != 1
                or entities.get("IsTruncated") is True
            ):
                raise OidcBootstrapError(f"Managed policy attachment scope is incorrect for {stage}.")


def execute(mode: str, aws: AwsCli, output_dir: Path, region: str) -> None:
    template_paths = write_artifacts(generate_templates(region), output_dir, region)
    if mode == "generate":
        return
    verify_identity(aws)
    if mode == "apply":
        state = inspect_bootstrap_state(aws)
        if any(state.values()):
            if not all(state.values()):
                raise OidcBootstrapError(
                    "Bootstrap is partially managed; inspect CloudFormation before retrying."
                )
            verify_boundary(aws, region)
        aws.deploy(PROVIDER_STACK_NAME, template_paths["provider"])
        for stage in STAGES:
            aws.deploy(role_stack_name(stage), template_paths[stage])
        verify_boundary(aws, region)
    elif mode == "verify":
        verify_boundary(aws, region)
    else:
        raise OidcBootstrapError("Mode must be generate, apply, or verify.")


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
        raise SystemExit("GitHub OIDC provisioning requires AWS_PROFILE=jm8-dev.")
    if args.expected_account_id != ACCOUNT_ID:
        raise SystemExit("GitHub OIDC provisioning requires the approved AWS account.")
    try:
        execute(
            args.mode,
            AwsCli(args.aws_profile, args.aws_region),
            args.output_dir,
            args.aws_region,
        )
    except OidcBootstrapError as error:
        print(f"GITHUB_OIDC_BOOTSTRAP_ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"GitHub OIDC {args.mode} completed; roles={len(STAGES)}; account={ACCOUNT_ID}.")


if __name__ == "__main__":
    main()
