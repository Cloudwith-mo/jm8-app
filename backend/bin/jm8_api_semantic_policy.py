#!/usr/bin/env python3
"""Reconcile the API role's read-only semantic retrieval policy."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

from jm8_environment_contract import EnvironmentContractError, validate_environment_contract


def policy_document(app, stage, region, account, table):
    if app != "journalm8" or stage not in {"dev", "staging", "prod"}:
        raise ValueError("Invalid application or stage")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
        raise ValueError("Invalid AWS region")
    if account != "114743615542":
        raise ValueError("Unexpected AWS account")
    if table != f"{app}-{stage}-entry-chunks":
        raise ValueError("Entry chunks table must match the stage")
    arn = f"arn:aws:dynamodb:{region}:{account}:table/{table}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "SearchStageSemanticIndex", "Effect": "Allow",
             "Action": ["dynamodb:SearchVectors"],
             "Resource": arn + "/index/SemanticEmbeddingIndex"},
            {"Sid": "ReadStageSemanticEvidence", "Effect": "Allow",
             "Action": ["dynamodb:BatchGetItem"], "Resource": arn},
        ],
    }


def aws(config, *args):
    result = subprocess.run(
        ["aws", *args, "--profile", config["aws_profile"],
         "--region", config["aws_region"], "--output", "json", "--no-cli-pager"],
        text=True, capture_output=True,
    )
    if result.returncode:
        # Report the service error code without dumping requests or credentials.
        match = re.search(r"\(([A-Za-z0-9]+)\)", result.stderr)
        code = match.group(1) if match else "CommandFailed"
        raise RuntimeError(f"{args[0]}:{args[1]} failed ({code})")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def reconcile(config, mode, call=aws):
    document = policy_document(
        config["app_name"], config["stage"], config["aws_region"],
        config["account_id"], config["entry_chunks_table_name"],
    )
    role = f'{config["app_name"]}-{config["stage"]}-lambda-basic-role'
    name = f'{config["app_name"]}-{config["stage"]}-semantic-query-retrieval'
    expected_role = f'arn:aws:iam::{config["account_id"]}:role/{role}'
    actual = call(config, "iam", "get-role", "--role-name", role)
    if actual.get("Role", {}).get("Arn") != expected_role:
        raise ValueError("API role ARN mismatch")

    if mode == "check":
        return {"mode": mode, "role": expected_role, "policyName": name,
                "policy": document}
    if mode == "apply":
        call(config, "iam", "put-role-policy", "--role-name", role,
             "--policy-name", name, "--policy-document", json.dumps(document))
    applied = call(config, "iam", "get-role-policy", "--role-name", role,
                   "--policy-name", name)
    if (applied.get("RoleName") != role or applied.get("PolicyName") != name
            or applied.get("PolicyDocument") != document):
        raise ValueError("Applied semantic retrieval policy does not match")
    return {"mode": mode, "role": expected_role, "policyName": name,
            "verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "apply", "verify"))
    args = parser.parse_args()
    os.environ["JM8_OPERATION"] = "reconcile-api-semantic-policy"
    config = validate_environment_contract()
    print(json.dumps(reconcile(config, args.mode), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (EnvironmentContractError, ValueError, RuntimeError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        sys.exit(1)
