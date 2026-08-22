#!/usr/bin/env python3
"""Safe, deterministic parsers for frontend-hosting deployment diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

REQUIRED_OUTPUTS = (
    "FrontendBucketName",
    "FrontendBucketRegionalDomainName",
    "DistributionId",
    "DistributionDomainName",
    "FrontendOrigin",
    "OriginAccessControlId",
)
SAFE_OUTPUTS = set(REQUIRED_OUTPUTS)
REQUIRED_TAGS = {
    "App": "journalm8",
    "ManagedBy": "aws-cli",
}


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _yaml_target(body: str) -> dict[str, Any]:
    """Parse the CloudFormation paths needed here without a third-party YAML package."""
    lines = body.splitlines()
    outputs_index = next((i for i, line in enumerate(lines) if line.strip() == "Outputs:"), None)
    if outputs_index is None:
        return {}
    outputs_indent = len(lines[outputs_index]) - len(lines[outputs_index].lstrip())
    result: dict[str, Any] = {"Outputs": {}}
    for index in range(outputs_index + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= outputs_indent:
            break
        if indent == outputs_indent + 2 and line.rstrip().endswith(":"):
            key = line.strip()[:-1]
            entry: dict[str, Any] = {}
            for child in lines[index + 1 :]:
                child_indent = len(child) - len(child.lstrip())
                if child.strip() and child_indent <= indent:
                    break
                if child.strip().startswith("Value:") and child_indent > indent:
                    value = child.strip()[len("Value:") :].strip()
                    if value == "!GetAtt FrontendOriginAccessControl.Id":
                        entry["Value"] = {"Fn::GetAtt": ["FrontendOriginAccessControl", "Id"]}
                    else:
                        entry["Value"] = value
            result["Outputs"][key] = entry
    return result


def load_template(value: str) -> Any:
    try:
        document = json.loads(value)
    except json.JSONDecodeError:
        return _yaml_target(value)
    if isinstance(document, str):
        try:
            return json.loads(document)
        except json.JSONDecodeError:
            return _yaml_target(document)
    if isinstance(document, dict) and "TemplateBody" in document:
        body = document["TemplateBody"]
        if isinstance(body, (dict, list)):
            return body
        if isinstance(body, str):
            return load_template(body)
    return document


def template_result(value: str) -> dict[str, Any]:
    document = load_template(value)
    output = document.get("Outputs", {}).get("OriginAccessControlId") if isinstance(document, dict) else None
    intrinsic = output.get("Value") if isinstance(output, dict) else None
    expected = {"Fn::GetAtt": ["FrontendOriginAccessControl", "Id"]}
    if output is None:
        code = "DEPLOYED_TEMPLATE_OUTPUT_MISSING"
    elif intrinsic != expected:
        code = "DEPLOYED_TEMPLATE_OUTPUT_INVALID"
    else:
        code = "OK"
    return {"code": code, "has_outputs": isinstance(document, dict) and "Outputs" in document,
            "has_origin_output": output is not None, "value": intrinsic}


def stack_result(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    stack = (data.get("Stacks") or [{}])[0]
    outputs = {item.get("OutputKey"): item.get("OutputValue") for item in (stack.get("Outputs") or []) if item.get("OutputKey")}
    statuses: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_OUTPUTS:
        value = outputs.get(name)
        status = "PRESENT"
        reason = "present"
        if name not in outputs:
            status, reason = "MISSING", "key absent"
        elif value is None:
            status, reason = "INVALID", "value equals None/null"
        elif not isinstance(value, str) or not value.strip():
            status, reason = "INVALID", "empty value"
        elif name == "OriginAccessControlId" and not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", value):
            status, reason = "INVALID", "malformed OAC ID"
        elif name == "DistributionId" and not re.fullmatch(r"[A-Za-z0-9]{3,64}", value):
            status, reason = "INVALID", "malformed distribution ID"
        elif name == "DistributionDomainName" and not value.endswith(".cloudfront.net"):
            status, reason = "INVALID", "must end in .cloudfront.net"
        statuses[name] = {"status": status, "reason": reason, "value": value if name in SAFE_OUTPUTS else None}
    parameters = {
        item.get("ParameterKey"): item.get("ParameterValue")
        for item in (stack.get("Parameters") or [])
        if item.get("ParameterKey") in {"AppName", "Stage", "FrontendBucketName"}
    }
    tags = {
        item.get("Key"): item.get("Value")
        for item in (stack.get("Tags") or [])
        if isinstance(item, dict) and item.get("Key")
    }
    return {"stack_name": stack.get("StackName"), "status": stack.get("StackStatus"),
            "last_updated": stack.get("LastUpdatedTime") or stack.get("CreationTime"),
            "output_keys": sorted(outputs), "outputs": statuses,
            "parameters": parameters, "tags": tags}


def resource_result(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for item in data.get("StackResourceSummaries", []):
        if item.get("LogicalResourceId") == "FrontendOriginAccessControl":
            return {key: item.get(key) for key in ("LogicalResourceId", "ResourceType", "ResourceStatus", "PhysicalResourceId")}
    return {"LogicalResourceId": None, "ResourceType": None, "ResourceStatus": None, "PhysicalResourceId": None}


def events_result(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{key: event.get(key) for key in ("Timestamp", "LogicalResourceId", "ResourceType", "ResourceStatus", "ResourceStatusReason")}
            for event in data.get("StackEvents", [])[:15]]


def sanitized_error(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    sanitized = re.sub(r"(?i)(authorization|password|token|digest|secret)[^\r\n]*", r"\1=<redacted>", text)
    sanitized = re.sub(r"(?i)(aws_access_key_id|aws_secret_access_key|aws_session_token)[^\r\n]*", r"\1=<redacted>", sanitized)
    return sanitized


def safe_actual(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [safe_actual(item) for item in value]
    if isinstance(value, dict):
        return {str(key): safe_actual(item) for key, item in value.items()}
    return str(value)


def add_validation(checks: list[dict[str, Any]], name: str, expected: Any, actual: Any, code: str, message: str) -> None:
    checks.append({
        "validation_name": name,
        "expected_value": safe_actual(expected),
        "safe_actual_value": safe_actual(actual),
        "stable_error_code": code,
        "message": message,
        "status": "FAIL",
    })


def _checks_result(checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [item for item in checks if item["status"] == "FAIL"]
    return {
        "passed": not failed,
        "checks": checks,
        "error_code": failed[0]["stable_error_code"] if failed else None,
    }


def stack_contract_checks(
    path: str,
    expected_stack_name: str,
    expected_stage: str,
    expected_bucket: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        stacks = payload.get("Stacks") if isinstance(payload, dict) else None
        if not isinstance(stacks, list) or len(stacks) != 1:
            raise ValueError("stack response must contain exactly one stack")
        summary = stack_result(path)
    except (OSError, ValueError, TypeError) as exc:
        add_validation(
            checks,
            "stack_json",
            "valid stack response",
            str(exc),
            "STACK_RESPONSE_INVALID",
            "CloudFormation stack response could not be parsed.",
        )
        return _checks_result(checks)

    expected_parameters = {
        "AppName": "journalm8",
        "Stage": expected_stage,
        "FrontendBucketName": expected_bucket,
    }
    if summary.get("stack_name") != expected_stack_name:
        add_validation(
            checks,
            "stack_name",
            expected_stack_name,
            summary.get("stack_name") or "<missing>",
            "STACK_IDENTITY_MISMATCH",
            "CloudFormation stack identity does not match the stage contract.",
        )
    if summary.get("parameters") != expected_parameters:
        add_validation(
            checks,
            "stack_parameters",
            expected_parameters,
            summary.get("parameters"),
            "STACK_PARAMETER_MISMATCH",
            "CloudFormation stack parameters do not match the selected stage.",
        )
    expected_tags = {**REQUIRED_TAGS, "Stage": expected_stage}
    actual_tags = summary.get("tags", {})
    if any(actual_tags.get(key) != value for key, value in expected_tags.items()):
        add_validation(
            checks,
            "stack_tags",
            expected_tags,
            {key: actual_tags.get(key) for key in expected_tags},
            "STACK_TAG_MISMATCH",
            "CloudFormation stack tags do not match the stage contract.",
        )
    return _checks_result(checks)


def _tag_map(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("tag response is not an object")
    items: Any
    if isinstance(payload.get("TagSet"), list):
        items = payload["TagSet"]
    elif isinstance(payload.get("Tags"), dict):
        items = payload["Tags"].get("Items")
    else:
        raise ValueError("tag response has no supported tag collection")
    if not isinstance(items, list):
        raise ValueError("tag collection is malformed")
    tags: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("tag entry is malformed")
        key = item.get("Key")
        value = item.get("Value")
        if not isinstance(key, str) or not isinstance(value, str) or key in tags:
            raise ValueError("tag entry is malformed or duplicated")
        tags[key] = value
    return tags


def tag_checks(path: str, expected_stage: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        tags = _tag_map(payload)
    except (OSError, ValueError, TypeError) as exc:
        add_validation(
            checks,
            "resource_tags",
            "valid tag response",
            str(exc),
            "RESOURCE_TAG_RESPONSE_INVALID",
            "Resource tags could not be parsed.",
        )
        return _checks_result(checks)
    expected = {**REQUIRED_TAGS, "Stage": expected_stage}
    if any(tags.get(key) != value for key, value in expected.items()):
        add_validation(
            checks,
            "resource_tags",
            expected,
            {key: tags.get(key) for key in expected},
            "RESOURCE_TAG_MISMATCH",
            "Resource tags do not match the stage contract.",
        )
    return _checks_result(checks)


def bucket_policy_checks(
    path: str,
    expected_bucket: str,
    expected_distribution_arn: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        policy = payload.get("Policy") if isinstance(payload, dict) else None
        if isinstance(policy, str):
            policy = json.loads(policy)
        statements = policy.get("Statement") if isinstance(policy, dict) else None
        if not isinstance(statements, list):
            raise ValueError("bucket policy statements are malformed")
    except (OSError, ValueError, TypeError) as exc:
        add_validation(
            checks,
            "bucket_policy",
            "valid policy response",
            str(exc),
            "BUCKET_POLICY_RESPONSE_INVALID",
            "Frontend bucket policy could not be parsed.",
        )
        return _checks_result(checks)

    bucket_arn = f"arn:aws:s3:::{expected_bucket}"
    object_arn = f"{bucket_arn}/*"
    allow_matches = [
        statement
        for statement in statements
        if statement.get("Effect") == "Allow"
        and statement.get("Principal") == {"Service": "cloudfront.amazonaws.com"}
        and statement.get("Action") == "s3:GetObject"
        and statement.get("Resource") == object_arn
        and statement.get("Condition", {}).get("StringEquals", {}).get("AWS:SourceArn")
        == expected_distribution_arn
    ]
    deny_matches = [
        statement
        for statement in statements
        if statement.get("Effect") == "Deny"
        and statement.get("Principal") == "*"
        and statement.get("Action") == "s3:*"
        and set(statement.get("Resource", [])) == {bucket_arn, object_arn}
        and str(
            statement.get("Condition", {}).get("Bool", {}).get(
                "aws:SecureTransport"
            )
        ).lower() == "false"
    ]
    if len(allow_matches) != 1:
        add_validation(
            checks,
            "cloudfront_read_policy",
            1,
            len(allow_matches),
            "BUCKET_POLICY_CLOUDFRONT_ACCESS_INVALID",
            "Bucket policy must allow only the exact distribution through OAC.",
        )
    if len(deny_matches) != 1:
        add_validation(
            checks,
            "https_only_policy",
            1,
            len(deny_matches),
            "BUCKET_POLICY_HTTPS_ONLY_INVALID",
            "Bucket policy must deny insecure transport for bucket and objects.",
        )
    return _checks_result(checks)


def cloudfront_checks(
    path: str,
    bucket_domain: str,
    expected_oac_id: str,
    expected_distribution_domain: str,
    expected_stage: str = "staging",
    expected_distribution_id: str = "",
    expected_auth_function_name: str = "",
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        add_validation(checks, "distribution_json", "valid JSON", str(exc), "CLOUDFRONT_DISTRIBUTION_STATUS_INVALID", "CloudFront distribution response could not be parsed.")
        return {"passed": False, "checks": checks, "error_code": checks[0]["stable_error_code"]}

    distribution = payload.get("Distribution", {}) if isinstance(payload, dict) else {}
    config = distribution.get("DistributionConfig", {}) if isinstance(distribution, dict) else {}
    origins = config.get("Origins", {}).get("Items", []) if isinstance(config.get("Origins", {}), dict) else []
    error_items = config.get("CustomErrorResponses", {}).get("Items", []) if isinstance(config.get("CustomErrorResponses", {}), dict) else []

    status = distribution.get("Status")
    if status != "Deployed":
        add_validation(checks, "distribution_status", "Deployed", status or "<missing>", "CLOUDFRONT_DISTRIBUTION_STATUS_INVALID", "CloudFront distribution status should be Deployed.")
    if expected_distribution_id and distribution.get("Id") != expected_distribution_id:
        add_validation(checks, "distribution_id", expected_distribution_id, distribution.get("Id") or "<missing>", "CLOUDFRONT_DISTRIBUTION_ID_MISMATCH", "Distribution ID does not match the stack output.")
    # AWS stores Enabled and DefaultRootObject under Distribution.DistributionConfig.
    if config.get("Enabled") is not True:
        add_validation(checks, "distribution_enabled", True, config.get("Enabled"), "CLOUDFRONT_DISTRIBUTION_DISABLED", "CloudFront distribution must be enabled.")
    domain_name = distribution.get("DomainName") or config.get("DomainName")
    if domain_name != expected_distribution_domain:
        add_validation(checks, "distribution_domain", expected_distribution_domain, domain_name or "<missing>", "CLOUDFRONT_DISTRIBUTION_DOMAIN_MISMATCH", "Distribution domain does not match the stack output.")
    if config.get("DefaultRootObject") != "index.html":
        add_validation(checks, "default_root_object", "index.html", config.get("DefaultRootObject"), "CLOUDFRONT_DEFAULT_ROOT_INVALID", "DefaultRootObject must be index.html.")
    if len(origins) != 1:
        add_validation(checks, "origin_count", 1, len(origins), "CLOUDFRONT_ORIGIN_COUNT_INVALID", "Exactly one origin should match the frontend bucket domain.")
    matching_origins = [origin for origin in origins if origin.get("DomainName") == bucket_domain]
    if len(matching_origins) != 1:
        add_validation(checks, "origin_domain_match", bucket_domain, [origin.get("DomainName") for origin in origins], "CLOUDFRONT_ORIGIN_DOMAIN_MISMATCH", "The frontend bucket regional domain must match exactly one origin.")
    origin = matching_origins[0] if matching_origins else {}
    if origin.get("OriginAccessControlId") != expected_oac_id:
        add_validation(checks, "origin_oac_id", expected_oac_id, origin.get("OriginAccessControlId") or "<missing>", "CLOUDFRONT_ORIGIN_OAC_MISMATCH", "Origin Access Control ID on the origin does not match the stack output.")
    if not isinstance(origin.get("S3OriginConfig"), dict) or origin.get("CustomOriginConfig") is not None:
        add_validation(checks, "private_s3_origin", "S3 origin through OAC", "non-S3 or custom origin", "CLOUDFRONT_PRIVATE_S3_ORIGIN_INVALID", "CloudFront must use the private S3 origin surface.")

    default_behavior = config.get("DefaultCacheBehavior", {}) if isinstance(config.get("DefaultCacheBehavior", {}), dict) else {}
    viewer_policy = default_behavior.get("ViewerProtocolPolicy")
    if viewer_policy != "redirect-to-https":
        add_validation(checks, "viewer_protocol_policy", "redirect-to-https", viewer_policy or "<missing>", "CLOUDFRONT_VIEWER_PROTOCOL_POLICY_INVALID", "Viewer protocol policy must be redirect-to-https.")
    cache_policy_id = default_behavior.get("CachePolicyId")
    if cache_policy_id != "658327ea-f89d-4fab-a63d-7e88639e58f6":
        add_validation(checks, "cache_policy_id", "658327ea-f89d-4fab-a63d-7e88639e58f6", cache_policy_id or "<missing>", "CLOUDFRONT_CACHE_POLICY_INVALID", "Cache policy must use the required AWS-managed cache policy ID.")
    function_items = default_behavior.get("FunctionAssociations", {}).get("Items", []) if isinstance(default_behavior.get("FunctionAssociations", {}), dict) else []
    viewer_request_items = [
        item for item in function_items if item.get("EventType") == "viewer-request"
    ]
    expected_viewer_request_count = 1 if expected_stage == "staging" else 0
    if len(viewer_request_items) != expected_viewer_request_count:
        add_validation(checks, "viewer_request_function", expected_viewer_request_count, len(viewer_request_items), "CLOUDFRONT_FUNCTION_ASSOCIATION_INVALID", "Viewer-request CloudFront Function association does not match the selected stage.")
    if expected_stage == "staging" and expected_auth_function_name and viewer_request_items:
        function_arn = viewer_request_items[0].get("FunctionARN", "")
        if not function_arn.endswith(f":function/{expected_auth_function_name}"):
            add_validation(checks, "viewer_request_function_name", expected_auth_function_name, function_arn or "<missing>", "CLOUDFRONT_FUNCTION_IDENTITY_INVALID", "Staging Basic Auth function does not match the stack contract.")
    for error_code in (403, 404):
        match = next((item for item in error_items if str(item.get("ErrorCode")) == str(error_code) and str(item.get("ResponseCode")) == "200" and item.get("ResponsePagePath") == "/index.html"), None)
        if match is None:
            code_name = "CLOUDFRONT_SPA_403_FALLBACK_INVALID" if error_code == 403 else "CLOUDFRONT_SPA_404_FALLBACK_INVALID"
            add_validation(checks, f"spa_{error_code}_fallback", {"ResponseCode": 200, "ResponsePagePath": "/index.html"}, {"ErrorCode": error_code, "ResponseCode": None, "ResponsePagePath": None}, code_name, f"The {error_code} fallback must return /index.html with HTTP 200.")

    return _checks_result(checks)


def oac_checks(path: str, expected_oac_id: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        add_validation(checks, "oac_json", "valid JSON", str(exc), "OAC_RESOURCE_ID_MISMATCH", "The OAC response could not be parsed.")
        return {"passed": False, "checks": checks, "error_code": checks[0]["stable_error_code"]}
    control = payload.get("OriginAccessControl", {}).get("OriginAccessControlConfig", {}) if isinstance(payload, dict) else {}
    actual_oac_id = payload.get("OriginAccessControl", {}).get("Id") if isinstance(payload, dict) else None
    if actual_oac_id != expected_oac_id:
        add_validation(checks, "oac_id", expected_oac_id, actual_oac_id or "<missing>", "OAC_RESOURCE_ID_MISMATCH", "The returned Origin Access Control ID does not match the stack output.")
    if control.get("OriginAccessControlOriginType") != "s3":
        add_validation(checks, "oac_origin_type", "s3", control.get("OriginAccessControlOriginType") or "<missing>", "OAC_ORIGIN_TYPE_INVALID", "OriginAccessControlOriginType must be s3.")
    if control.get("SigningBehavior") != "always":
        add_validation(checks, "oac_signing_behavior", "always", control.get("SigningBehavior") or "<missing>", "OAC_SIGNING_BEHAVIOR_INVALID", "SigningBehavior must be always.")
    if control.get("SigningProtocol") != "sigv4":
        add_validation(checks, "oac_signing_protocol", "sigv4", control.get("SigningProtocol") or "<missing>", "OAC_SIGNING_PROTOCOL_INVALID", "SigningProtocol must be sigv4.")
    failed = [item for item in checks if item["status"] == "FAIL"]
    return {"passed": not failed, "checks": checks, "error_code": failed[0]["stable_error_code"] if failed else None}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    file_parser = sub.add_parser("file-check")
    file_parser.add_argument("path")
    template_parser = sub.add_parser("template-check")
    template_parser.add_argument("path")
    stack_parser = sub.add_parser("stack-check")
    stack_parser.add_argument("path")
    stack_contract_parser = sub.add_parser("stack-contract-verify")
    stack_contract_parser.add_argument("path")
    stack_contract_parser.add_argument("expected_stack_name")
    stack_contract_parser.add_argument("expected_stage", choices=("staging", "prod"))
    stack_contract_parser.add_argument("expected_bucket")
    tags_parser = sub.add_parser("tags-verify")
    tags_parser.add_argument("path")
    tags_parser.add_argument("expected_stage", choices=("staging", "prod"))
    bucket_policy_parser = sub.add_parser("bucket-policy-verify")
    bucket_policy_parser.add_argument("path")
    bucket_policy_parser.add_argument("expected_bucket")
    bucket_policy_parser.add_argument("expected_distribution_arn")
    resource_parser = sub.add_parser("resource-check")
    resource_parser.add_argument("path")
    events_parser = sub.add_parser("events")
    events_parser.add_argument("path")
    error_parser = sub.add_parser("sanitize-error")
    error_parser.add_argument("path")
    cloudfront_parser = sub.add_parser("cloudfront-verify")
    cloudfront_parser.add_argument("distribution_json")
    cloudfront_parser.add_argument("bucket_domain")
    cloudfront_parser.add_argument("expected_oac_id")
    cloudfront_parser.add_argument("expected_distribution_domain")
    cloudfront_parser.add_argument(
        "expected_stage",
        choices=("staging", "prod"),
        nargs="?",
        default="staging",
    )
    cloudfront_parser.add_argument("expected_distribution_id", nargs="?", default="")
    cloudfront_parser.add_argument("expected_auth_function_name", nargs="?", default="")
    oac_parser = sub.add_parser("oac-verify")
    oac_parser.add_argument("oac_json")
    oac_parser.add_argument("expected_oac_id")
    report_parser = sub.add_parser("write-report")
    report_parser.add_argument("path")
    report_parser.add_argument("--operation", required=True)
    report_parser.add_argument("--stage", required=True)
    report_parser.add_argument("--stack-name", required=True)
    report_parser.add_argument("--template-path", required=True)
    report_parser.add_argument("--local-checksum", required=True)
    report_parser.add_argument("--deployed-checksum", default="")
    report_parser.add_argument("--pre-status", default="")
    report_parser.add_argument("--post-status", default="")
    report_parser.add_argument("--post-timestamp", default="")
    report_parser.add_argument("--phase", required=True)
    report_parser.add_argument("--final-code", required=True)
    report_parser.add_argument("--exit-code", type=int, default=0)
    report_parser.add_argument("--stable-error-code", default="")
    report_parser.add_argument("--sanitized-error-message", default="")
    report_parser.add_argument("--timestamp", default="")
    report_parser.add_argument("--stack-json")
    report_parser.add_argument("--resource-json")
    args = parser.parse_args()
    if args.command == "file-check":
        print(json.dumps({"checksum": sha256_file(args.path), "path": str(Path(args.path).resolve())}))
    elif args.command == "template-check":
        body = Path(args.path).read_text(encoding="utf-8")
        result = template_result(body)
        result["checksum"] = sha256_file(args.path)
        result["path"] = str(Path(args.path).resolve())
        print(json.dumps(result))
    elif args.command == "stack-check":
        print(json.dumps(stack_result(args.path)))
    elif args.command == "stack-contract-verify":
        result = stack_contract_checks(
            args.path,
            args.expected_stack_name,
            args.expected_stage,
            args.expected_bucket,
        )
        print(json.dumps(result))
        return 0 if result["passed"] else 1
    elif args.command == "tags-verify":
        result = tag_checks(args.path, args.expected_stage)
        print(json.dumps(result))
        return 0 if result["passed"] else 1
    elif args.command == "bucket-policy-verify":
        result = bucket_policy_checks(
            args.path,
            args.expected_bucket,
            args.expected_distribution_arn,
        )
        print(json.dumps(result))
        return 0 if result["passed"] else 1
    elif args.command == "resource-check":
        print(json.dumps(resource_result(args.path)))
    elif args.command == "events":
        print(json.dumps(events_result(args.path)))
    elif args.command == "sanitize-error":
        print(sanitized_error(args.path), end="")
    elif args.command == "cloudfront-verify":
        result = cloudfront_checks(
            args.distribution_json,
            args.bucket_domain,
            args.expected_oac_id,
            args.expected_distribution_domain,
            args.expected_stage,
            args.expected_distribution_id,
            args.expected_auth_function_name,
        )
        print(json.dumps(result))
        return 0 if result["passed"] else 1
    elif args.command == "oac-verify":
        result = oac_checks(args.oac_json, args.expected_oac_id)
        print(json.dumps(result))
        return 0 if result["passed"] else 1
    else:
        report: dict[str, Any] = {
            "operation": args.operation,
            "stage": args.stage,
            "stack_name": args.stack_name,
            "template_path": str(Path(args.template_path).resolve()),
            "local_template_checksum": args.local_checksum,
            "deployed_template_checksum": args.deployed_checksum,
            "pre_stack_status": args.pre_status,
            "post_stack_status": args.post_status,
            "post_stack_timestamp": args.post_timestamp,
            "phase_reached": args.phase,
            "final_status": "success" if args.final_code == "SUCCESS" else "failed",
            "exit_code": args.exit_code,
            "final_code": args.final_code,
            "stable_error_code": args.stable_error_code or ("" if args.final_code == "SUCCESS" else args.final_code),
            "sanitized_error_message": args.sanitized_error_message,
            "timestamp": args.timestamp or __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if args.stack_json and Path(args.stack_json).exists():
            report["stack"] = stack_result(args.stack_json)
        if args.resource_json and Path(args.resource_json).exists():
            report["physical_oac_resource"] = resource_result(args.resource_json)
        destination = Path(args.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(str(destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
