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
    return {"stack_name": stack.get("StackName"), "status": stack.get("StackStatus"),
            "last_updated": stack.get("LastUpdatedTime") or stack.get("CreationTime"),
            "output_keys": sorted(outputs), "outputs": statuses}


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


def cloudfront_checks(path: str, bucket_domain: str, expected_oac_id: str, expected_distribution_domain: str) -> dict[str, Any]:
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

    default_behavior = config.get("DefaultCacheBehavior", {}) if isinstance(config.get("DefaultCacheBehavior", {}), dict) else {}
    viewer_policy = default_behavior.get("ViewerProtocolPolicy")
    if viewer_policy != "redirect-to-https":
        add_validation(checks, "viewer_protocol_policy", "redirect-to-https", viewer_policy or "<missing>", "CLOUDFRONT_VIEWER_PROTOCOL_POLICY_INVALID", "Viewer protocol policy must be redirect-to-https.")
    cache_policy_id = default_behavior.get("CachePolicyId")
    if cache_policy_id != "658327ea-f89d-4fab-a63d-7e88639e58f6":
        add_validation(checks, "cache_policy_id", "658327ea-f89d-4fab-a63d-7e88639e58f6", cache_policy_id or "<missing>", "CLOUDFRONT_CACHE_POLICY_INVALID", "Cache policy must use the required AWS-managed cache policy ID.")
    function_items = default_behavior.get("FunctionAssociations", {}).get("Items", []) if isinstance(default_behavior.get("FunctionAssociations", {}), dict) else []
    has_viewer_request = sum(1 for x in function_items if x.get("EventType") == "viewer-request") == 1
    if not has_viewer_request:
        add_validation(checks, "viewer_request_function", 1, len([x for x in function_items if x.get("EventType") == "viewer-request"]), "CLOUDFRONT_FUNCTION_ASSOCIATION_INVALID", "Exactly one viewer-request CloudFront Function association is expected.")
    for error_code in (403, 404):
        match = next((item for item in error_items if str(item.get("ErrorCode")) == str(error_code) and str(item.get("ResponseCode")) == "200" and item.get("ResponsePagePath") == "/index.html"), None)
        if match is None:
            code_name = "CLOUDFRONT_SPA_403_FALLBACK_INVALID" if error_code == 403 else "CLOUDFRONT_SPA_404_FALLBACK_INVALID"
            add_validation(checks, f"spa_{error_code}_fallback", {"ResponseCode": 200, "ResponsePagePath": "/index.html"}, {"ErrorCode": error_code, "ResponseCode": None, "ResponsePagePath": None}, code_name, f"The {error_code} fallback must return /index.html with HTTP 200.")

    failed = [item for item in checks if item["status"] == "FAIL"]
    return {"passed": not failed, "checks": checks, "error_code": failed[0]["stable_error_code"] if failed else None}


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
    elif args.command == "resource-check":
        print(json.dumps(resource_result(args.path)))
    elif args.command == "events":
        print(json.dumps(events_result(args.path)))
    elif args.command == "sanitize-error":
        print(sanitized_error(args.path), end="")
    elif args.command == "cloudfront-verify":
        result = cloudfront_checks(args.distribution_json, args.bucket_domain, args.expected_oac_id, args.expected_distribution_domain)
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
