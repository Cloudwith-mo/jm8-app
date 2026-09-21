#!/usr/bin/env python3
"""Prepare only the two dev DynamoDB semantic prerequisites; never activate consumers."""
import argparse
import json
import os
import re
import subprocess
import time

import jm8_environment_contract as contract

ACCOUNT = "114743615542"
REGION = "us-east-1"
PROFILE = "jm8-dev"
MAIN = "journalm8-dev-main"
CHUNKS = "journalm8-dev-entry-chunks"
CONFIRMATION = "bootstrap-journalm8-dev-semantic-tables"
TAGS = {"App": "journalm8", "Stage": "dev", "ManagedBy": "aws-cli"}
COMMON = dict(app_name="journalm8", stage="dev", account_id=ACCOUNT, region=REGION)


class Stop(RuntimeError):
    pass


def aws(service, operation, *args, missing_ok=False):
    env = dict(os.environ, AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off")
    # Use only the selected profile, not inherited short-lived deployment credentials.
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        env.pop(key, None)
    result = subprocess.run(
        ["aws", service, operation, *args, "--profile", PROFILE,
         "--region", REGION, "--output", "json", "--no-cli-pager"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        match = re.search(r"An error occurred \(([^)]+)\)", result.stderr)
        code = match.group(1) if match else "UnclassifiedCLIError"
        if missing_ok and code == "ResourceNotFoundException":
            return None
        raise Stop(f"{service}:{operation} failed ({code}); no automatic retry.")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def arn(name):
    return f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{name}"


def description(name, missing_ok=False):
    return aws("dynamodb", "describe-table", "--table-name", name, missing_ok=missing_ok)


def tags(name, exact=False):
    document = aws("dynamodb", "list-tags-of-resource", "--resource-arn", arn(name))
    contract.validate_entry_chunks_table_tags(
        document, app_name="journalm8", stage="dev", before_reconcile=not exact)


def pitr():
    return aws("dynamodb", "describe-continuous-backups", "--table-name", CHUNKS)


def consumers_disabled():
    document = aws("lambda", "list-event-source-mappings")
    mappings = document.get("EventSourceMappings")
    if not isinstance(mappings, list):
        raise Stop("Malformed event-source mapping response.")
    for mapping in mappings:
        source = mapping.get("EventSourceArn", "")
        function = mapping.get("FunctionArn", "")
        relevant = any(source.startswith(arn(name) + "/stream/") for name in (MAIN, CHUNKS))
        relevant |= any(function.startswith(
            f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:journalm8-dev-{worker}"
        ) for worker in ("semantic-memory-worker", "semantic-embedding-worker"))
        if relevant and mapping.get("State") != "Disabled":
            raise Stop("Relevant consumer is not Disabled; stop before enabling any stream.")


def inspect(require_ready=False):
    if aws("sts", "get-caller-identity").get("Account") != ACCOUNT:
        raise Stop("Wrong AWS account.")
    consumers_disabled()
    main = description(MAIN)
    action = contract.validate_main_table_stream_description(
        main, **COMMON, table_name=MAIN, require_stream=require_ready)
    tags(MAIN)
    chunks = description(CHUNKS, missing_ok=not require_ready)
    if chunks is not None:
        contract.validate_entry_chunks_table_description(
            chunks, **COMMON, table_name=CHUNKS, require_deletion_protection=require_ready)
        # Never silently adopt an existing table with unknown ownership.
        tags(CHUNKS, exact=True)
        backups = pitr()
        if require_ready:
            contract.validate_entry_chunks_table_pitr(backups)
    return action, chunks


def wait_ready(name):
    for _ in range(60):
        document = description(name)
        if document.get("Table", {}).get("TableStatus") == "ACTIVE":
            return
        time.sleep(2)
    raise Stop(f"{name} did not become ACTIVE; inspect before retrying.")


def wait_backups_ready():
    # ACTIVE table status can precede continuous-backup availability.
    for attempt in range(60):
        status = pitr().get("ContinuousBackupsDescription", {}).get("ContinuousBackupsStatus")
        if status == "ENABLED":
            return
        if status != "DISABLED":
            raise Stop("Unexpected continuous-backup readiness response.")
        if attempt < 59:
            time.sleep(2)
    raise Stop("Continuous backups did not become available; PITR was not changed.")


def run(mode, confirmation=None):
    if mode == "apply" and confirmation != CONFIRMATION:
        raise Stop("Apply requires the exact dev bootstrap confirmation.")
    action, chunks = inspect(require_ready=mode == "verify")
    if mode != "apply":
        print(json.dumps({"mode": mode, "mainStream": action,
                          "entryChunks": "EXISTS" if chunks else "CREATE",
                          "consumers": "absent-or-disabled"}))
        return
    # All identity, schema, ownership and consumer checks precede the first write.
    if chunks is None:
        aws("dynamodb", "create-table", "--table-name", CHUNKS,
            "--attribute-definitions", "AttributeName=PK,AttributeType=S", "AttributeName=SK,AttributeType=S",
            "--key-schema", "AttributeName=PK,KeyType=HASH", "AttributeName=SK,KeyType=RANGE",
            "--billing-mode", "PAY_PER_REQUEST", "--deletion-protection-enabled",
            "--tags", *[f"Key={key},Value={value}" for key, value in TAGS.items()])
        wait_ready(CHUNKS)
    elif not chunks["Table"].get("DeletionProtectionEnabled"):
        aws("dynamodb", "update-table", "--table-name", CHUNKS, "--deletion-protection-enabled")
        wait_ready(CHUNKS)
    wait_backups_ready()
    aws("dynamodb", "update-continuous-backups", "--table-name", CHUNKS,
        "--point-in-time-recovery-specification", "PointInTimeRecoveryEnabled=true")
    for attempt in range(60):
        status = pitr().get("ContinuousBackupsDescription", {}).get("PointInTimeRecoveryDescription", {}).get("PointInTimeRecoveryStatus")
        if status == "ENABLED":
            break
        if attempt == 59:
            raise Stop("Entry-chunks PITR did not become enabled.")
        time.sleep(2)
    if action == "ENABLE":
        consumers_disabled()
        # Re-read before the change; do not replace an incompatible stream.
        action = contract.validate_main_table_stream_description(
            description(MAIN), **COMMON, table_name=MAIN, require_stream=False)
        if action == "ENABLE":
            aws("dynamodb", "update-table", "--table-name", MAIN,
                "--stream-specification", "StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES")
            wait_ready(MAIN)
    inspect(require_ready=True)
    print(json.dumps({"mode": "apply", "verified": True, "consumers": "absent-or-disabled"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "apply", "verify"))
    parser.add_argument("--confirmation")
    args = parser.parse_args()
    try:
        run(args.mode, args.confirmation)
    except (Stop, contract.EnvironmentContractError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"STOP: {error}")
