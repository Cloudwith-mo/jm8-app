#!/usr/bin/env python3
"""JM8 S3 helper utilities for policy/lifecycle/tag/CORS merging and validation.

This module is intended to be invoked from `bin/create-resources` and from
unit tests. Keep logic deterministic and fail-fast on unexpected inputs.
"""
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional


def load_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        raise


def normalize_policy_document(policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not policy:
        return {}
    if isinstance(policy, str):
        try:
            policy = json.loads(policy)
        except Exception:
            raise ValueError("Bucket policy is not valid JSON")
    if isinstance(policy, dict) and "Policy" in policy:
        inner = policy["Policy"]
        if isinstance(inner, str):
            return normalize_policy_document(json.loads(inner))
        if isinstance(inner, dict):
            return normalize_policy_document(inner)
    if not isinstance(policy, dict):
        raise ValueError("Bucket policy must be an object")
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        policy = dict(policy)
        policy["Statement"] = [statements]
    return policy


def parse_aws_error(path: str) -> Optional[str]:
    """Parse AWS CLI stderr and return a code when one is detected."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return None

    if not raw:
        return None

    # AWS CLI JSON error payloads
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        err = parsed.get("Error") or parsed.get("error") or {}
        if isinstance(err, dict):
            code = err.get("Code") or err.get("code")
            if code:
                return str(code)
        if isinstance(parsed.get("code"), str):
            return str(parsed["code"])

    matches = [
        'NoSuchBucketPolicy',
        'NoSuchLifecycleConfiguration',
        'NoSuchTagSet',
        'NoSuchCORSConfiguration',
        'AccessDenied',
        'AccessDeniedException',
        'Forbidden',
    ]
    for marker in matches:
        if marker in raw:
            return marker

    text_match = re.search(r"An error occurred \(([^)]+)\)", raw)
    if text_match:
        return text_match.group(1)

    if "NoSuchBucketPolicy" in raw or "NoSuchLifecycleConfiguration" in raw:
        return 'NoSuchBucketPolicy' if 'NoSuchBucketPolicy' in raw else 'NoSuchLifecycleConfiguration'
    if "NoSuchTagSet" in raw:
        return 'NoSuchTagSet'
    if "NoSuchCORSConfiguration" in raw:
        return 'NoSuchCORSConfiguration'

    return "UNRECOGNIZED_AWS_ERROR"


def jm8_https_statement(bucket: str) -> Dict[str, Any]:
    return {
        "Sid": "EnforceHttpsTransport",
        "Effect": "Deny",
        "Principal": "*",
        "Action": "s3:*",
        "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
    }


def merge_bucket_policy(existing: Optional[Dict[str, Any]], bucket: str) -> Dict[str, Any]:
    stmt = jm8_https_statement(bucket)
    out = {"Version": "2012-10-17", "Statement": []}
    policy = normalize_policy_document(existing)
    statements = policy.get("Statement", [])
    if not isinstance(statements, list):
        statements = [statements]
    statements = [s for s in statements if not (isinstance(s, dict) and s.get("Sid") == "EnforceHttpsTransport")]
    out["Statement"] = [stmt] + statements
    return out


def merge_lifecycle(existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rule_abort = {
        "ID": "AbortIncompleteMultipartAfter7Days",
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
    }

    rule_noncurrent = {
        "ID": "DeleteNoncurrentVersionsAfter30Days",
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
    }

    rules: List[Dict[str, Any]] = []
    policy = existing or {}
    existing_rules = policy.get("Rules", [])
    if not isinstance(existing_rules, list):
        existing_rules = [existing_rules]
    rules = [r for r in existing_rules if r.get("ID") not in {rule_abort["ID"], rule_noncurrent["ID"]}]
    out = {"Rules": [rule_abort, rule_noncurrent] + rules}
    return out


def merge_tags(existing: Optional[Dict[str, Any]], app_name: str, stage: str) -> Dict[str, Any]:
    required = {
        "App": app_name,
        "Stage": stage,
        "ManagedBy": "aws-cli",
        "DataClassification": "Sensitive",
    }

    tag_map: Dict[str, str] = {}
    if existing:
        tagset = existing.get("TagSet", [])
        if not isinstance(tagset, list):
            tagset = [tagset]
        for t in tagset:
            if isinstance(t, dict):
                tag_map[str(t.get("Key"))] = str(t.get("Value"))
    for k, v in required.items():
        tag_map[k] = v
    out = {"TagSet": [{"Key": k, "Value": v} for k, v in tag_map.items()]}
    return out


def desired_dev_cors() -> Dict[str, Any]:
    return {
        "CORSRules": [
            {
                "AllowedOrigins": ["http://localhost:5173", "http://127.0.0.1:5173"],
                "AllowedMethods": ["PUT", "GET", "HEAD"],
                "AllowedHeaders": ["*"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": 3000,
            }
        ]
    }


def decide_cors_apply(existing: Optional[Dict[str, Any]], stage: str) -> Optional[Dict[str, Any]]:
    if stage != "dev":
        return None
    desired = desired_dev_cors()
    if existing and existing.get("CORSRules") == desired.get("CORSRules"):
        return None
    return desired


def normalize_policy_for_verification(policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return normalize_policy_document(policy)


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: jm8_s3_helpers.py <cmd> [args]")
        return 2

    cmd = argv[1]
    try:
        if cmd == "merge-policy":
            existing_path, bucket, out_path = argv[2:5]
            existing = load_json(existing_path)
            merged = merge_bucket_policy(existing, bucket)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
            return 0

        if cmd == "check-error":
            err_path = argv[2]
            allowed = []
            if len(argv) > 3:
                allowed = [x for x in argv[3].split(',') if x]

            if not os.path.exists(err_path):
                return 0
            with open(err_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if not raw:
                return 0
            code = parse_aws_error(err_path)
            if code in (None, 'UNRECOGNIZED_AWS_ERROR'):
                print(f"Unexpected AWS error output: {raw[:200]}", file=sys.stderr)
                return 4
            if code in allowed:
                return 0
            print(f"Unexpected AWS error code: {code}", file=sys.stderr)
            return 4

        if cmd == "merge-lifecycle":
            existing_path, out_path = argv[2:4]
            existing = load_json(existing_path)
            merged = merge_lifecycle(existing)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
            return 0

        if cmd == "merge-tags":
            existing_path, app_name, stage, out_path = argv[2:6]
            existing = load_json(existing_path)
            merged = merge_tags(existing, app_name, stage)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
            return 0

        if cmd == "decide-cors":
            existing_path, stage, out_path = argv[2:5]
            existing = load_json(existing_path)
            result = decide_cors_apply(existing, stage)
            if result is None:
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                except Exception:
                    pass
                return 0
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            return 0

        if cmd == "verify-postdeploy":
            cb_path, desc_path, policy_path, lif_path, tags_path, cors_path, bucket, app_name, stage = argv[2:11]
            errors: List[str] = []

            cb = load_json(cb_path) or {}
            pitr = None
            cbd = cb.get('ContinuousBackupsDescription') or {}
            pird = cbd.get('PointInTimeRecoveryDescription') or {}
            pitr = pird.get('PointInTimeRecoveryStatus')
            if pitr not in ('ENABLED', 'ENABLING'):
                errors.append(f'PITR not enabled (status={pitr})')

            desc = load_json(desc_path) or {}
            dp = (desc.get('Table') or {}).get('DeletionProtectionEnabled')
            if dp is not True:
                errors.append(f'DeletionProtectionEnabled not true (value={dp})')

            policy = normalize_policy_for_verification(load_json(policy_path))
            statements = policy.get('Statement', [])
            if not isinstance(statements, list):
                statements = [statements]
            jm8 = next((s for s in statements if isinstance(s, dict) and s.get('Sid') == 'EnforceHttpsTransport'), None)
            if not jm8:
                errors.append('EnforceHttpsTransport statement missing')
            else:
                if jm8.get('Effect') != 'Deny':
                    errors.append('EnforceHttpsTransport Effect not Deny')
                if jm8.get('Principal') != '*':
                    errors.append('EnforceHttpsTransport Principal not *')
                action = jm8.get('Action')
                if action != 's3:*' and ('s3:*' not in (action or [])):
                    errors.append('EnforceHttpsTransport Action does not include s3:*')
                resource = jm8.get('Resource') or []
                if isinstance(resource, str):
                    resource = [resource]
                if f'arn:aws:s3:::{bucket}' not in resource or f'arn:aws:s3:::{bucket}/*' not in resource:
                    errors.append('EnforceHttpsTransport Resource ARNs do not cover bucket and objects')
                cond = ((jm8.get('Condition') or {}).get('Bool') or {}).get('aws:SecureTransport')
                if str(cond).lower() != 'false':
                    errors.append('EnforceHttpsTransport Condition aws:SecureTransport not false')

            lif = load_json(lif_path) or {}
            rules = lif.get('Rules', []) if isinstance(lif, dict) else []
            ids = {str(r.get('ID')): r for r in rules if isinstance(r, dict)}
            r1 = ids.get('AbortIncompleteMultipartAfter7Days')
            r2 = ids.get('DeleteNoncurrentVersionsAfter30Days')
            if not r1 or r1.get('Status') != 'Enabled' or r1.get('AbortIncompleteMultipartUpload', {}).get('DaysAfterInitiation') != 7:
                errors.append('AbortIncompleteMultipartAfter7Days missing or incorrect')
            if not r2 or r2.get('Status') != 'Enabled' or r2.get('NoncurrentVersionExpiration', {}).get('NoncurrentDays') != 30:
                errors.append('DeleteNoncurrentVersionsAfter30Days missing or incorrect')
            for rid in ('AbortIncompleteMultipartAfter7Days', 'DeleteNoncurrentVersionsAfter30Days'):
                rr = ids.get(rid) or {}
                if rr.get('Expiration') is not None:
                    errors.append(f'{rid} should not have Expiration')
                if rr.get('Transitions') is not None:
                    errors.append(f'{rid} should not have Transitions')

            tags = load_json(tags_path) or {}
            tagset = {str(t.get('Key')): str(t.get('Value')) for t in (tags.get('TagSet', []) or []) if isinstance(t, dict)}
            for k, v in (('App', app_name), ('Stage', stage), ('ManagedBy', 'aws-cli'), ('DataClassification', 'Sensitive')):
                if tagset.get(k) != v:
                    errors.append(f'Tag {k} missing or incorrect')

            cors = load_json(cors_path) if cors_path and os.path.exists(cors_path) else None
            if stage == 'dev':
                desired = desired_dev_cors()
                if cors != desired:
                    errors.append('Dev CORS configuration differs from expected')

            if errors:
                for e in errors:
                    print('VERIFY ERROR: ' + e, file=sys.stderr)
                return 5
            return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
