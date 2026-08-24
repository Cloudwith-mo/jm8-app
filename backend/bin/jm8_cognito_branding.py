#!/usr/bin/env python3
"""Generate, reconcile, and verify JM8 Cognito Hosted UI (classic) branding."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from jm8_environment_contract import (
    EnvironmentContractError,
    JM8_AWS_ACCOUNT_ID,
    PRODUCTION_ISOLATION_MODE as ENVIRONMENT_PRODUCTION_ISOLATION_MODE,
    validate_stage,
    validate_stage_aws_profile,
)


APP_NAME = "journalm8"
ACCOUNT_ID = JM8_AWS_ACCOUNT_ID
REGION = "us-east-1"
CLASSIC_HOSTED_UI_VERSION = 1
PRODUCTION_ISOLATION_MODE = ENVIRONMENT_PRODUCTION_ISOLATION_MODE
CANONICAL_TAGS = {
    "App": APP_NAME,
    "ManagedBy": "aws-cli",
}
ASSET_DIR = Path(__file__).resolve().parents[1] / "infra" / "cognito"
CSS_PATH = ASSET_DIR / "jm8-hosted-ui.css"
LOGO_BASE64_PATH = ASSET_DIR / "jm8-hosted-ui-logo.png.b64"
CSS_SIZE_LIMIT = 3 * 1024
LOGO_SIZE_LIMIT = 100 * 1024
LOGO_WIDTH = 350
LOGO_HEIGHT = 178
ALLOWED_CSS_CLASSES = {
    "background-customizable",
    "banner-customizable",
    "errorMessage-customizable",
    "idpButton-customizable",
    "idpDescription-customizable",
    "inputField-customizable",
    "label-customizable",
    "legalText-customizable",
    "logo-customizable",
    "passwordCheck-notValid-customizable",
    "passwordCheck-valid-customizable",
    "redirect-customizable",
    "socialButton-customizable",
    "submitButton-customizable",
    "textDescription-customizable",
}
FORBIDDEN_BRANDING_PATTERNS = (
    re.compile(r"@(?:import|supports|page|media)\b", re.IGNORECASE),
    re.compile(r"(?:https?:|data:|javascript\b|url\s*\(|expression\s*\()", re.IGNORECASE),
    re.compile(
        r"(?:localhost|127\.0\.0\.1|x-user-id|\bdemo\b|"
        r"development[-_ ]?(?:mode|endpoint)?|tracker|analytics)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:sk_(?:live|test)_|whsec_|AKIA[0-9A-Z]{16})", re.IGNORECASE),
)
SAFE_AWS_ERROR_CODES = {
    "AccessDenied",
    "AccessDeniedException",
    "InvalidClientTokenId",
    "NotAuthorizedException",
    "ResourceNotFoundException",
    "RequestExpired",
    "Throttling",
    "ThrottlingException",
    "UnrecognizedClientException",
    "ValidationError",
    "ValidationException",
}


class CognitoBrandingError(RuntimeError):
    """Raised when branding cannot be generated or verified safely."""


@dataclass(frozen=True)
class BrandingBoundary:
    app_name: str
    stage: str
    aws_profile: str
    account_id: str
    region: str
    deploy_confirmation: str = ""
    production_isolation_mode: str = ""

    @property
    def user_pool_name(self) -> str:
        return f"{self.app_name}-{self.stage}-users"

    @property
    def app_client_name(self) -> str:
        return f"{self.app_name}-{self.stage}-web"

    @property
    def domain_prefix(self) -> str:
        return f"{self.app_name}-{self.stage}-{self.account_id}"

    @property
    def cognito_domain(self) -> str:
        return (
            f"https://{self.domain_prefix}.auth."
            f"{self.region}.amazoncognito.com"
        )


@dataclass(frozen=True)
class BrandingTarget:
    boundary: BrandingBoundary
    user_pool_id: str
    app_client_id: str
    callback_url: str
    logout_url: str

    @property
    def user_pool_arn(self) -> str:
        return (
            f"arn:aws:cognito-idp:{self.boundary.region}:"
            f"{self.boundary.account_id}:userpool/{self.user_pool_id}"
        )


def _fail(message: str) -> None:
    raise CognitoBrandingError(message)


def validate_boundary(boundary: BrandingBoundary) -> None:
    if boundary.app_name != APP_NAME:
        _fail("Cognito branding requires the canonical application name.")
    try:
        validate_stage(boundary.stage)
    except EnvironmentContractError:
        _fail("Cognito branding stage is invalid.")
    try:
        validate_stage_aws_profile(boundary.stage, boundary.aws_profile)
    except EnvironmentContractError:
        _fail("Cognito branding AWS profile does not match the stage.")
    if boundary.account_id != ACCOUNT_ID:
        _fail("Cognito branding account does not match the approved account.")
    if boundary.region != REGION:
        _fail("Cognito branding region does not match the approved region.")
    if boundary.stage == "staging" and boundary.deploy_confirmation != "staging":
        _fail("Staging Cognito branding requires deployment confirmation.")
    if boundary.stage == "prod":
        if boundary.deploy_confirmation != "prod":
            _fail("Production Cognito branding requires deployment confirmation.")
        if boundary.production_isolation_mode != PRODUCTION_ISOLATION_MODE:
            _fail("Production Cognito branding isolation acknowledgment is invalid.")


def _validate_url(label: str, value: str, stage: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        _fail(f"{label} is malformed.")
    if parsed.username or parsed.password or parsed.fragment:
        _fail(f"{label} is malformed.")
    host = parsed.hostname.lower()
    if stage != "dev" and (parsed.scheme != "https" or host in {"localhost", "127.0.0.1"}):
        _fail(f"{label} does not satisfy the stage HTTPS boundary.")
    other_stages = {"dev", "staging", "prod"} - {stage}
    if any(re.search(rf"(^|[.\-_/]){other}([.\-_/]|$)", value, re.IGNORECASE) for other in other_stages):
        _fail(f"{label} contains a cross-stage reference.")


def validate_target(target: BrandingTarget) -> None:
    validate_boundary(target.boundary)
    expected_pool_pattern = re.compile(
        rf"{re.escape(target.boundary.region)}_[A-Za-z0-9]+"
    )
    if expected_pool_pattern.fullmatch(target.user_pool_id) is None:
        _fail("Cognito user-pool ID is malformed or cross-region.")
    if re.fullmatch(r"[a-z0-9]{20,128}", target.app_client_id) is None:
        _fail("Cognito app-client ID is malformed.")
    _validate_url("Cognito callback URL", target.callback_url, target.boundary.stage)
    _validate_url("Cognito logout URL", target.logout_url, target.boundary.stage)


def canonical_css() -> str:
    try:
        css = CSS_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise CognitoBrandingError("Canonical Cognito CSS asset is unavailable.") from error
    validate_css(css)
    return css


def validate_css(css: Any) -> None:
    if not isinstance(css, str) or not css:
        _fail("Cognito branding CSS is missing or malformed.")
    if len(css.encode("utf-8")) > CSS_SIZE_LIMIT:
        _fail("Cognito branding CSS exceeds the documented safe size.")
    if any(pattern.search(css) for pattern in FORBIDDEN_BRANDING_PATTERNS):
        _fail("Cognito branding CSS contains a forbidden external or unsafe marker.")
    classes = set(re.findall(r"\.([A-Za-z][A-Za-z0-9-]*)", css))
    if not classes or not classes.issubset(ALLOWED_CSS_CLASSES):
        _fail("Cognito branding CSS contains an unsupported selector.")
    if "submitButton-customizable" not in classes or "background-customizable" not in classes:
        _fail("Cognito branding CSS is incomplete.")


def _png_chunks(image: bytes) -> list[tuple[bytes, bytes]]:
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        _fail("Cognito branding logo is not a PNG image.")
    chunks: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset < len(image):
        if offset + 12 > len(image):
            _fail("Cognito branding logo is malformed.")
        length = struct.unpack(">I", image[offset:offset + 4])[0]
        chunk_type = image[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(image):
            _fail("Cognito branding logo is malformed.")
        data = image[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", image[offset + 8 + length:end])[0]
        actual_crc = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            _fail("Cognito branding logo is malformed.")
        chunks.append((chunk_type, data))
        offset = end
        if chunk_type == b"IEND":
            break
    if offset != len(image) or not chunks or chunks[-1][0] != b"IEND":
        _fail("Cognito branding logo is malformed.")
    return chunks


def validate_logo(image: Any) -> None:
    if not isinstance(image, bytes) or not image or len(image) > LOGO_SIZE_LIMIT:
        _fail("Cognito branding logo is missing or exceeds the documented limit.")
    chunks = _png_chunks(image)
    if [kind for kind, _ in chunks] != [b"IHDR", b"IDAT", b"IEND"]:
        _fail("Cognito branding logo contains unsupported metadata.")
    header = chunks[0][1]
    if len(header) != 13:
        _fail("Cognito branding logo header is malformed.")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    if (
        (width, height) != (LOGO_WIDTH, LOGO_HEIGHT)
        or bit_depth != 8
        or color_type != 6
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        _fail("Cognito branding logo dimensions or format are invalid.")


def canonical_logo() -> bytes:
    try:
        encoded = "".join(
            LOGO_BASE64_PATH.read_text(encoding="ascii").split()
        )
        image = base64.b64decode(encoded, validate=True)
    except (OSError, UnicodeError, binascii.Error) as error:
        raise CognitoBrandingError("Canonical Cognito logo asset is unavailable.") from error
    validate_logo(image)
    return image


def _exact_string_values(actual: Any, expected: list[str]) -> bool:
    return (
        isinstance(actual, list)
        and all(isinstance(value, str) for value in actual)
        and len(actual) == len(expected)
        and set(actual) == set(expected)
    )


def validate_app_client(document: Any, target: BrandingTarget) -> None:
    client = document.get("UserPoolClient") if isinstance(document, dict) else None
    if not isinstance(client, dict):
        _fail("Cognito app-client response is malformed.")
    generate_secret = client.get("GenerateSecret", False)
    if (
        client.get("UserPoolId") not in (None, target.user_pool_id)
        or client.get("ClientId") != target.app_client_id
        or client.get("ClientName") != target.boundary.app_client_name
        or generate_secret is not False
        or client.get("AllowedOAuthFlowsUserPoolClient") is not True
        or not _exact_string_values(client.get("AllowedOAuthFlows"), ["code"])
        or not _exact_string_values(
            client.get("AllowedOAuthScopes"), ["openid", "email", "profile"]
        )
        or not _exact_string_values(client.get("CallbackURLs"), [target.callback_url])
        or not _exact_string_values(client.get("LogoutURLs"), [target.logout_url])
        or not _exact_string_values(client.get("SupportedIdentityProviders"), ["COGNITO"])
    ):
        _fail("Cognito app-client configuration verification failed.")


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
            raise CognitoBrandingError(
                f"AWS command could not run: {service} {operation}."
            ) from error
        if result.returncode != 0:
            match = re.search(r"\(([A-Za-z0-9]+)\)", result.stderr or "")
            candidate = match.group(1) if match else ""
            code = candidate if candidate in SAFE_AWS_ERROR_CODES else "AwsCliError"
            raise CognitoBrandingError(
                f"AWS command failed: {service} {operation} ({code})."
            )
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise CognitoBrandingError(
                f"AWS response was malformed: {service} {operation}."
            ) from error
        if not isinstance(response, dict):
            raise CognitoBrandingError(
                f"AWS response was malformed: {service} {operation}."
            )
        return response


def _require_complete_inventory(response: dict[str, Any], key: str) -> list[Any]:
    items = response.get(key)
    next_token = response.get("NextToken")
    if (
        not isinstance(items, list)
        or response.get("IsTruncated") is True
        or next_token not in (None, "")
    ):
        _fail("Cognito inventory response is incomplete or malformed.")
    return items


def verify_aws_identity_and_target(aws: AwsCli, target: BrandingTarget) -> None:
    validate_target(target)
    boundary = target.boundary
    identity = aws.call("sts", "get-caller-identity")
    caller_arn = identity.get("Arn")
    if identity.get("Account") != boundary.account_id or not isinstance(caller_arn, str):
        _fail("Cognito branding AWS identity is incorrect.")
    if re.fullmatch(
        rf"arn:aws:(?:iam|sts)::{boundary.account_id}:"
        r"(?:user|role|assumed-role)/[^\s]+",
        caller_arn,
    ) is None:
        _fail("Cognito branding AWS identity is malformed.")

    pools = _require_complete_inventory(
        aws.call("cognito-idp", "list-user-pools", "--max-results", "60"),
        "UserPools",
    )
    pool_matches = [
        item for item in pools
        if isinstance(item, dict) and item.get("Name") == boundary.user_pool_name
    ]
    if len(pool_matches) != 1 or pool_matches[0].get("Id") != target.user_pool_id:
        _fail("Cognito user-pool resolution is missing, duplicated, or incorrect.")

    pool_response = aws.call(
        "cognito-idp", "describe-user-pool", "--user-pool-id", target.user_pool_id
    )
    pool = pool_response.get("UserPool") if isinstance(pool_response, dict) else None
    if not isinstance(pool, dict) or (
        pool.get("Id") != target.user_pool_id
        or pool.get("Name") != boundary.user_pool_name
        or pool.get("Arn") != target.user_pool_arn
    ):
        _fail("Cognito user-pool identity is malformed or cross-stage.")

    tag_response = aws.call(
        "cognito-idp", "list-tags-for-resource", "--resource-arn", target.user_pool_arn
    )
    tags = tag_response.get("Tags") if isinstance(tag_response, dict) else None
    expected_tags = {**CANONICAL_TAGS, "Stage": boundary.stage}
    if not isinstance(tags, dict) or any(tags.get(key) != value for key, value in expected_tags.items()):
        _fail("Cognito user-pool tags do not match the stage boundary.")
    cross_stage_values = {"dev", "staging", "prod"} - {boundary.stage}
    if any(str(value).lower() in cross_stage_values for value in tags.values()):
        _fail("Cognito user-pool tags contain a cross-stage value.")

    clients = _require_complete_inventory(
        aws.call(
            "cognito-idp",
            "list-user-pool-clients",
            "--user-pool-id",
            target.user_pool_id,
            "--max-results",
            "60",
        ),
        "UserPoolClients",
    )
    client_matches = [
        item for item in clients
        if isinstance(item, dict) and item.get("ClientName") == boundary.app_client_name
    ]
    if len(client_matches) != 1 or client_matches[0].get("ClientId") != target.app_client_id:
        _fail("Cognito app-client resolution is missing, duplicated, or incorrect.")
    validate_app_client(
        aws.call(
            "cognito-idp",
            "describe-user-pool-client",
            "--user-pool-id",
            target.user_pool_id,
            "--client-id",
            target.app_client_id,
        ),
        target,
    )

    domain_response = aws.call(
        "cognito-idp",
        "describe-user-pool-domain",
        "--domain",
        boundary.domain_prefix,
    )
    domain = domain_response.get("DomainDescription") if isinstance(domain_response, dict) else None
    if not isinstance(domain, dict) or (
        domain.get("Domain") != boundary.domain_prefix
        or domain.get("UserPoolId") != target.user_pool_id
        or domain.get("Status") != "ACTIVE"
        or domain.get("ManagedLoginVersion") != CLASSIC_HOSTED_UI_VERSION
    ):
        _fail("Cognito domain is not the expected active Classic Hosted UI domain.")


def _validate_image_url(
    value: Any,
    target: BrandingTarget,
    *,
    require_client_path: bool,
) -> str:
    if not isinstance(value, str) or not value:
        _fail("Cognito branding logo URL is missing.")
    parsed = urlsplit(value)
    expected_host = urlsplit(target.boundary.cognito_domain).hostname
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not host
        or (host != expected_host and not host.endswith(".cloudfront.net"))
        or (require_client_path and target.app_client_id not in parsed.path)
        or "/assets/images/" not in parsed.path
    ):
        _fail("Cognito branding logo URL is malformed or outside the AWS boundary.")
    return value


def download_logo(url: str) -> bytes:
    try:
        request = Request(url, headers={"User-Agent": "JM8-Cognito-Branding-Verifier/1"})
        with urlopen(request, timeout=15) as response:
            if getattr(response, "status", 200) != 200:
                _fail("Cognito branding logo could not be verified.")
            if response.geturl() != url:
                _fail("Cognito branding logo response redirected unexpectedly.")
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if "image/png" not in content_type:
                _fail("Cognito branding logo response has an invalid content type.")
            image = response.read(LOGO_SIZE_LIMIT + 1)
    except CognitoBrandingError:
        raise
    except Exception as error:
        raise CognitoBrandingError("Cognito branding logo could not be verified.") from error
    validate_logo(image)
    return image


LogoFetcher = Callable[[str], bytes]


def inspect_customization(
    document: Any,
    target: BrandingTarget,
    fetch_logo: LogoFetcher,
) -> bool | None:
    customization = document.get("UICustomization") if isinstance(document, dict) else None
    if customization == {}:
        return None
    if not isinstance(customization, dict):
        _fail("Cognito branding response is missing or malformed.")
    client_id = customization.get("ClientId")
    if customization.get("UserPoolId") != target.user_pool_id:
        _fail("Cognito branding response is cross-pool or cross-client.")
    if client_id not in (target.app_client_id, "ALL"):
        _fail("Cognito branding response is cross-pool or cross-client.")
    inherited_pool_branding = client_id == "ALL"
    applied_css = customization.get("CSS")
    validate_css(applied_css)
    image_url = _validate_image_url(
        customization.get("ImageUrl"),
        target,
        require_client_path=not inherited_pool_branding,
    )
    css_version = customization.get("CSSVersion")
    if not isinstance(css_version, str) or not css_version:
        _fail("Cognito branding response has no CSS version.")
    applied_logo = fetch_logo(image_url)
    validate_logo(applied_logo)
    return (
        not inherited_pool_branding
        and applied_css == canonical_css()
        and applied_logo == canonical_logo()
    )


def _get_customization(aws: AwsCli, target: BrandingTarget) -> dict[str, Any]:
    return aws.call(
        "cognito-idp",
        "get-ui-customization",
        "--user-pool-id",
        target.user_pool_id,
        "--client-id",
        target.app_client_id,
    )


def verify_branding(
    aws: AwsCli,
    target: BrandingTarget,
    fetch_logo: LogoFetcher = download_logo,
) -> None:
    verify_aws_identity_and_target(aws, target)
    if inspect_customization(_get_customization(aws, target), target, fetch_logo) is not True:
        _fail("Cognito Hosted UI branding does not match the canonical assets.")


def apply_branding(
    aws: AwsCli,
    target: BrandingTarget,
    fetch_logo: LogoFetcher = download_logo,
) -> bool:
    verify_aws_identity_and_target(aws, target)
    current = inspect_customization(_get_customization(aws, target), target, fetch_logo)
    changed = current is not True
    if changed:
        with tempfile.TemporaryDirectory(prefix="jm8-cognito-branding-") as temp_dir:
            logo_path = Path(temp_dir) / "jm8-hosted-ui-logo.png"
            logo_path.write_bytes(canonical_logo())
            aws.call(
                "cognito-idp",
                "set-ui-customization",
                "--user-pool-id",
                target.user_pool_id,
                "--client-id",
                target.app_client_id,
                "--css",
                canonical_css(),
                "--image-file",
                f"fileb://{logo_path}",
            )
    if inspect_customization(_get_customization(aws, target), target, fetch_logo) is not True:
        _fail("Applied Cognito Hosted UI branding failed final verification.")
    return changed


def generate_assets(boundary: BrandingBoundary, output_dir: Path) -> dict[str, Any]:
    validate_boundary(boundary)
    css = canonical_css()
    logo = canonical_logo()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "jm8-hosted-ui.css").write_text(css, encoding="utf-8")
    (output_dir / "jm8-hosted-ui-logo.png").write_bytes(logo)
    manifest = {
        "accountId": boundary.account_id,
        "appClientName": boundary.app_client_name,
        "brandingMode": "hosted-ui-classic",
        "brandingVersion": CLASSIC_HOSTED_UI_VERSION,
        "cssBytes": len(css.encode("utf-8")),
        "domainPrefix": boundary.domain_prefix,
        "logoBytes": len(logo),
        "logoFormat": "PNG",
        "logoHeight": LOGO_HEIGHT,
        "logoWidth": LOGO_WIDTH,
        "region": boundary.region,
        "stage": boundary.stage,
        "userPoolName": boundary.user_pool_name,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("generate", "apply", "verify"))
    parser.add_argument("--app-name", default=os.environ.get("APP_NAME", ""))
    parser.add_argument("--stage", default=os.environ.get("STAGE", ""))
    parser.add_argument("--aws-profile", default=os.environ.get("AWS_PROFILE", ""))
    parser.add_argument(
        "--expected-account-id",
        default=os.environ.get("EXPECTED_AWS_ACCOUNT_ID", ""),
    )
    parser.add_argument("--aws-region", default=os.environ.get("AWS_REGION", ""))
    parser.add_argument(
        "--deploy-confirmation", default=os.environ.get("DEPLOY_CONFIRMATION", "")
    )
    parser.add_argument(
        "--production-isolation-mode",
        default=os.environ.get("PRODUCTION_ISOLATION_MODE", ""),
    )
    parser.add_argument("--user-pool-id", default="")
    parser.add_argument("--app-client-id", default="")
    parser.add_argument("--callback-url", default="")
    parser.add_argument("--logout-url", default="")
    parser.add_argument("--output-dir", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    boundary = BrandingBoundary(
        app_name=args.app_name,
        stage=args.stage,
        aws_profile=args.aws_profile,
        account_id=args.expected_account_id,
        region=args.aws_region,
        deploy_confirmation=args.deploy_confirmation,
        production_isolation_mode=args.production_isolation_mode,
    )
    try:
        if args.mode == "generate":
            if args.output_dir is None:
                _fail("Cognito branding generate mode requires an output directory.")
            manifest = generate_assets(boundary, args.output_dir)
            print(
                "Cognito branding assets generated for "
                f"{manifest['appClientName']}; mode=hosted-ui-classic."
            )
            return
        target = BrandingTarget(
            boundary=boundary,
            user_pool_id=args.user_pool_id,
            app_client_id=args.app_client_id,
            callback_url=args.callback_url,
            logout_url=args.logout_url,
        )
        aws = AwsCli(boundary.aws_profile, boundary.region)
        changed = apply_branding(aws, target) if args.mode == "apply" else False
        if args.mode == "verify":
            verify_branding(aws, target)
        status = "reconciled" if changed else "verified"
        print(
            f"Cognito Hosted UI branding {status} for "
            f"{boundary.app_client_name}."
        )
    except CognitoBrandingError as error:
        print(f"COGNITO_BRANDING_ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
