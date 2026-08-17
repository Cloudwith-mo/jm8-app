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
    return re.sub(r"(?i)(authorization|password|token|digest|secret)[^\r\n]*", r"\1=<redacted>", text)


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
            "verification_phase_reached": args.phase,
            "final_success_failure_code": args.final_code,
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
