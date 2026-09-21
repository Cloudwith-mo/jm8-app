"""Explicit, dev-only tag ownership adoption; never changes pool configuration."""
import argparse
import json
import subprocess

ACCOUNT = "114743615542"
REGION = "us-east-1"
POOL = "us-east-1_bJcMC6yDw"
ARN = f"arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/{POOL}"
LEGACY = {"Environment": "dev", "ManagedBy": "Terraform", "Owner": "mko", "Project": "journalm8"}
DESIRED = {**LEGACY, "App": "journalm8", "Stage": "dev", "ManagedBy": "aws-cli"}
CONFIRM = "adopt-journalm8-dev-cognito"


def aws(service, operation, *args):
    result = subprocess.run(
        ["aws", service, operation, *args, "--profile", "jm8-dev",
         "--region", REGION, "--output", "json", "--no-cli-pager"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"AWS {service} {operation} failed; stop and inspect locally.")
    return json.loads(result.stdout or "{}")


def execute(mode, confirmation, call=aws):
    if mode not in ("check", "apply", "verify"):
        raise ValueError("Unknown mode")
    if mode == "apply" and confirmation != CONFIRM:
        raise ValueError("Explicit adoption confirmation required")
    identity = call("sts", "get-caller-identity")
    if identity.get("Account") != ACCOUNT:
        raise ValueError("Wrong AWS account")
    pool = call("cognito-idp", "describe-user-pool", "--user-pool-id", POOL).get("UserPool", {})
    if (pool.get("Id"), pool.get("Name"), pool.get("Arn")) != (POOL, "journalm8-dev-users", ARN):
        raise ValueError("Pool identity mismatch")
    tags = call("cognito-idp", "list-tags-for-resource", "--resource-arn", ARN).get("Tags")
    if tags not in (LEGACY, DESIRED):
        raise ValueError("Unexpected tags; review drift before adoption")
    if mode == "verify" and tags != DESIRED:
        raise ValueError("Pool has not been adopted")
    if mode == "apply" and tags != DESIRED:
        call("cognito-idp", "tag-resource", "--resource-arn", ARN,
             "--tags", json.dumps({"App": "journalm8", "Stage": "dev", "ManagedBy": "aws-cli"}))
        actual = call("cognito-idp", "list-tags-for-resource", "--resource-arn", ARN).get("Tags")
        if actual != DESIRED:
            raise RuntimeError("Post-write verification failed; do not deploy or retry blindly")
    return {"mode": mode, "pool": POOL, "alreadyAdopted": tags == DESIRED}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "apply", "verify"])
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args()
    print(json.dumps(execute(args.mode, args.confirmation)))
