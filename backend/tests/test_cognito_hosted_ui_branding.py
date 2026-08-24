from __future__ import annotations

import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
ASSET_DIR = BACKEND_ROOT / "infra" / "cognito"
CREATE_AUTH = BIN_DIR / "create-auth"
sys.path.insert(0, str(BIN_DIR))

from jm8_cognito_branding import (  # noqa: E402
    ACCOUNT_ID,
    APP_NAME,
    REGION,
    AwsCli,
    BrandingBoundary,
    BrandingTarget,
    CognitoBrandingError,
    apply_branding,
    canonical_css,
    canonical_logo,
    generate_assets,
    inspect_customization,
    main as branding_main,
    validate_app_client,
    validate_boundary,
    validate_css,
    validate_logo,
    validate_target,
    verify_aws_identity_and_target,
    verify_branding,
)


CLIENT_ID = "a" * 26
POOL_SUFFIX = "JM8Pool123"


def boundary(stage: str = "prod") -> BrandingBoundary:
    profile = "jm8-prod" if stage == "prod" else "jm8-dev"
    return BrandingBoundary(
        app_name=APP_NAME,
        stage=stage,
        aws_profile=profile,
        account_id=ACCOUNT_ID,
        region=REGION,
        deploy_confirmation=stage if stage in {"staging", "prod"} else "",
        production_isolation_mode=(
            "stage-scoped-same-account" if stage == "prod" else ""
        ),
    )


def target(stage: str = "prod") -> BrandingTarget:
    host = "localhost" if stage == "dev" else f"{stage}.example.com"
    scheme = "http" if stage == "dev" else "https"
    return BrandingTarget(
        boundary=boundary(stage),
        user_pool_id=f"{REGION}_{POOL_SUFFIX}",
        app_client_id=CLIENT_ID,
        callback_url=f"{scheme}://{host}/callback",
        logout_url=f"{scheme}://{host}/",
    )


def client_document(expected: BrandingTarget, *, generate_secret: object = "omitted") -> dict:
    client = {
        "UserPoolId": expected.user_pool_id,
        "ClientId": expected.app_client_id,
        "ClientName": expected.boundary.app_client_name,
        "AllowedOAuthFlowsUserPoolClient": True,
        "AllowedOAuthFlows": ["code"],
        "AllowedOAuthScopes": ["openid", "email", "profile"],
        "CallbackURLs": [expected.callback_url],
        "LogoutURLs": [expected.logout_url],
        "SupportedIdentityProviders": ["COGNITO"],
    }
    if generate_secret != "omitted":
        client["GenerateSecret"] = generate_secret
    return {"UserPoolClient": client}


class FakeAws:
    def __init__(self, expected: BrandingTarget, customization: dict | None = None) -> None:
        self.expected = expected
        self.customization = copy.deepcopy(customization or {})
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.set_count = 0

    def call(self, service: str, operation: str, *arguments: str) -> dict:
        self.calls.append((service, operation, arguments))
        expected = self.expected
        boundary_value = expected.boundary

        def require_argument(name: str, value: str) -> None:
            try:
                actual = arguments[arguments.index(name) + 1]
            except (ValueError, IndexError) as error:
                raise AssertionError(f"Missing AWS argument: {name}") from error
            if actual != value:
                raise AssertionError(
                    f"Incorrect AWS argument boundary for {operation}: {name}"
                )

        if (service, operation) == ("sts", "get-caller-identity"):
            return {
                "Account": boundary_value.account_id,
                "Arn": (
                    f"arn:aws:sts::{boundary_value.account_id}:assumed-role/"
                    f"{boundary_value.app_name}-{boundary_value.stage}-deployer/session"
                ),
            }
        if operation == "list-user-pools":
            return {
                "UserPools": [{
                    "Id": expected.user_pool_id,
                    "Name": boundary_value.user_pool_name,
                }]
            }
        if operation == "describe-user-pool":
            require_argument("--user-pool-id", expected.user_pool_id)
            return {
                "UserPool": {
                    "Id": expected.user_pool_id,
                    "Name": boundary_value.user_pool_name,
                    "Arn": expected.user_pool_arn,
                }
            }
        if operation == "list-tags-for-resource":
            require_argument("--resource-arn", expected.user_pool_arn)
            return {
                "Tags": {
                    "App": APP_NAME,
                    "Stage": boundary_value.stage,
                    "ManagedBy": "aws-cli",
                }
            }
        if operation == "list-user-pool-clients":
            require_argument("--user-pool-id", expected.user_pool_id)
            return {
                "UserPoolClients": [{
                    "ClientId": expected.app_client_id,
                    "ClientName": boundary_value.app_client_name,
                }]
            }
        if operation == "describe-user-pool-client":
            require_argument("--user-pool-id", expected.user_pool_id)
            require_argument("--client-id", expected.app_client_id)
            return client_document(expected)
        if operation == "describe-user-pool-domain":
            require_argument("--domain", boundary_value.domain_prefix)
            return {
                "DomainDescription": {
                    "Domain": boundary_value.domain_prefix,
                    "UserPoolId": expected.user_pool_id,
                    "Status": "ACTIVE",
                    "ManagedLoginVersion": 1,
                }
            }
        if operation == "get-ui-customization":
            require_argument("--user-pool-id", expected.user_pool_id)
            require_argument("--client-id", expected.app_client_id)
            return {"UICustomization": copy.deepcopy(self.customization)}
        if operation == "set-ui-customization":
            require_argument("--user-pool-id", expected.user_pool_id)
            require_argument("--client-id", expected.app_client_id)
            self.set_count += 1
            css_index = arguments.index("--css") + 1
            self.customization = {
                "UserPoolId": expected.user_pool_id,
                "ClientId": expected.app_client_id,
                "CSS": arguments[css_index],
                "CSSVersion": str(self.set_count),
                "ImageUrl": (
                    f"{boundary_value.cognito_domain}/{expected.app_client_id}/"
                    "version/assets/images/image.png"
                ),
            }
            return {"UICustomization": copy.deepcopy(self.customization)}
        raise AssertionError(f"Unexpected AWS call: {service} {operation}")


def canonical_customization(expected: BrandingTarget) -> dict:
    return {
        "UserPoolId": expected.user_pool_id,
        "ClientId": expected.app_client_id,
        "CSS": canonical_css(),
        "CSSVersion": "7",
        "ImageUrl": (
            f"{expected.boundary.cognito_domain}/{expected.app_client_id}/"
            "version/assets/images/image.png"
        ),
    }


class CognitoHostedUiBrandingTests(unittest.TestCase):
    def test_assets_are_deterministic_and_stage_aware(self):
        for stage in ("dev", "staging", "prod"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
                first_manifest = generate_assets(boundary(stage), Path(first))
                second_manifest = generate_assets(boundary(stage), Path(second))
                self.assertEqual(first_manifest, second_manifest)
                self.assertEqual(first_manifest["stage"], stage)
                self.assertEqual(
                    first_manifest["appClientName"], f"journalm8-{stage}-web"
                )
                for name in (
                    "jm8-hosted-ui.css",
                    "jm8-hosted-ui-logo.png",
                    "manifest.json",
                ):
                    self.assertEqual(
                        (Path(first) / name).read_bytes(),
                        (Path(second) / name).read_bytes(),
                    )

    def test_logo_and_css_assets_obey_the_classic_hosted_ui_contract(self):
        css = canonical_css()
        logo = canonical_logo()
        validate_css(css)
        validate_logo(logo)
        self.assertLessEqual(len(css.encode()), 3 * 1024)
        self.assertLessEqual(len(logo), 100 * 1024)
        self.assertEqual(logo[:8], b"\x89PNG\r\n\x1a\n")
        source_logo = (ASSET_DIR / "jm8-hosted-ui-logo.svg").read_text()
        self.assertIn(">JM<", source_logo)
        self.assertIn(">8<", source_logo)
        self.assertIn(">JOURNALM8<", source_logo)
        for prohibited in (
            "@import",
            "http://",
            "https://",
            "url(",
            "javascript:",
            "demo-user",
            "x-user-id",
            "sk_live_",
            "whsec_",
        ):
            self.assertNotIn(prohibited, css.lower())
        self.assertNotIn("href=", source_logo.lower())
        self.assertNotIn("xlink:", source_logo.lower())

    def test_boundaries_require_exact_stage_profile_account_and_production_acknowledgments(self):
        for stage in ("dev", "staging", "prod"):
            validate_boundary(boundary(stage))
            validate_target(target(stage))
        production = boundary("prod")
        self.assertEqual(production.user_pool_name, "journalm8-prod-users")
        self.assertEqual(production.app_client_name, "journalm8-prod-web")
        self.assertEqual(
            production.domain_prefix, "journalm8-prod-114743615542"
        )
        mutations = (
            {"aws_profile": "jm8-dev"},
            {"account_id": "999999999999"},
            {"region": "us-west-2"},
            {"deploy_confirmation": "staging"},
            {"production_isolation_mode": "separate-account"},
        )
        original = boundary("prod")
        for changes in mutations:
            with self.subTest(changes=changes), self.assertRaises(CognitoBrandingError):
                validate_boundary(BrandingBoundary(**{**original.__dict__, **changes}))

    def test_noncanonical_stage_profile_combinations_fail_closed(self):
        for stage, profile in (
            ("staging", "jm8-prod"),
            ("staging", "arbitrary-profile"),
            ("staging", "jm8-staging"),
            ("prod", "jm8-dev"),
            ("prod", "arbitrary-profile"),
            ("dev", "jm8-prod"),
        ):
            rejected = boundary(stage)
            rejected = BrandingBoundary(
                **{**rejected.__dict__, "aws_profile": profile}
            )
            with self.subTest(stage=stage, profile=profile):
                with self.assertRaisesRegex(
                    CognitoBrandingError,
                    "AWS profile does not match the stage",
                ):
                    validate_boundary(rejected)

    def test_create_auth_staging_arguments_reach_real_apply_path_with_jm8_dev(self):
        expected = target("staging")
        aws = FakeAws(expected)

        class LogoResponse:
            status = 200
            headers = {"Content-Type": "image/png"}

            def __init__(self, request) -> None:
                self.url = request.full_url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return self.url

            def read(self, _limit):
                return canonical_logo()

        arguments = [
            "jm8_cognito_branding.py",
            "apply",
            "--app-name",
            APP_NAME,
            "--stage",
            "staging",
            "--aws-profile",
            "jm8-dev",
            "--expected-account-id",
            ACCOUNT_ID,
            "--aws-region",
            REGION,
            "--deploy-confirmation",
            "staging",
            "--production-isolation-mode",
            "",
            "--user-pool-id",
            expected.user_pool_id,
            "--app-client-id",
            expected.app_client_id,
            "--callback-url",
            expected.callback_url,
            "--logout-url",
            expected.logout_url,
        ]
        with (
            patch("sys.argv", arguments),
            patch("jm8_cognito_branding.AwsCli", return_value=aws) as aws_cli,
            patch(
                "jm8_cognito_branding.urlopen",
                side_effect=lambda request, timeout: LogoResponse(request),
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            branding_main()

        aws_cli.assert_called_once_with("jm8-dev", REGION)
        self.assertEqual(aws.set_count, 1)
        self.assertIn(
            ("cognito-idp", "set-ui-customization"),
            {(service, operation) for service, operation, _ in aws.calls},
        )
        self.assertIn("reconciled", output.getvalue())

    def test_target_rejects_cross_stage_or_malformed_values(self):
        original = target("prod")
        for changes in (
            {"user_pool_id": "us-west-2_Other"},
            {"app_client_id": "short"},
            {"callback_url": "https://staging.example.com/callback"},
            {"logout_url": "http://localhost:5173/"},
        ):
            with self.subTest(changes=changes), self.assertRaises(CognitoBrandingError):
                validate_target(BrandingTarget(**{**original.__dict__, **changes}))

    def test_resource_resolution_and_classic_domain_are_exact(self):
        expected = target("prod")
        verify_aws_identity_and_target(FakeAws(expected), expected)

        class MutatingFake(FakeAws):
            def __init__(self, expected_target: BrandingTarget, operation: str, response: dict):
                super().__init__(expected_target)
                self.operation = operation
                self.response = response

            def call(self, service: str, operation: str, *arguments: str) -> dict:
                if operation == self.operation:
                    return copy.deepcopy(self.response)
                return super().call(service, operation, *arguments)

        invalid_responses = (
            ("list-user-pools", {"UserPools": []}),
            (
                "list-user-pools",
                {"UserPools": [
                    {"Id": expected.user_pool_id, "Name": expected.boundary.user_pool_name},
                    {"Id": expected.user_pool_id, "Name": expected.boundary.user_pool_name},
                ]},
            ),
            ("list-user-pools", {"UserPools": [], "NextToken": []}),
            ("list-user-pool-clients", {"UserPoolClients": []}),
            (
                "list-user-pool-clients",
                {"UserPoolClients": [
                    {
                        "ClientId": expected.app_client_id,
                        "ClientName": expected.boundary.app_client_name,
                    },
                    {
                        "ClientId": expected.app_client_id,
                        "ClientName": expected.boundary.app_client_name,
                    },
                ]},
            ),
            ("describe-user-pool", {"UserPool": {"Id": expected.user_pool_id}}),
            (
                "list-tags-for-resource",
                {"Tags": {
                    "App": APP_NAME,
                    "Stage": "staging",
                    "ManagedBy": "aws-cli",
                }},
            ),
            (
                "describe-user-pool-domain",
                {"DomainDescription": {
                    "Domain": expected.boundary.domain_prefix,
                    "UserPoolId": expected.user_pool_id,
                    "Status": "ACTIVE",
                    "ManagedLoginVersion": 2,
                }},
            ),
        )
        for operation, response in invalid_responses:
            with self.subTest(operation=operation), self.assertRaises(CognitoBrandingError):
                verify_aws_identity_and_target(
                    MutatingFake(expected, operation, response), expected
                )

    def test_app_client_verification_preserves_exact_pkce_contract(self):
        expected = target("prod")
        validate_app_client(client_document(expected), expected)
        validate_app_client(client_document(expected, generate_secret=False), expected)
        for invalid in (True, None, 0, 1, "false", {}, []):
            with self.subTest(generate_secret=invalid), self.assertRaises(CognitoBrandingError):
                validate_app_client(
                    client_document(expected, generate_secret=invalid), expected
                )
        malformed_pool = client_document(expected)
        malformed_pool["UserPoolClient"]["UserPoolId"] = {}
        with self.assertRaises(CognitoBrandingError):
            validate_app_client(malformed_pool, expected)
        base = client_document(expected)["UserPoolClient"]
        mismatches = {
            "ClientId": "b" * 26,
            "ClientName": "journalm8-staging-web",
            "AllowedOAuthFlowsUserPoolClient": False,
            "AllowedOAuthFlows": ["implicit"],
            "AllowedOAuthScopes": ["openid", "email"],
            "CallbackURLs": ["https://staging.example.com/callback"],
            "LogoutURLs": ["https://staging.example.com/"],
            "SupportedIdentityProviders": ["Google"],
        }
        for field, value in mismatches.items():
            with self.subTest(field=field), self.assertRaises(CognitoBrandingError):
                validate_app_client(
                    {"UserPoolClient": {**base, field: value}}, expected
                )

    def test_customization_requires_exact_css_logo_pool_and_client(self):
        expected = target("prod")
        document = {"UICustomization": canonical_customization(expected)}
        self.assertTrue(
            inspect_customization(document, expected, lambda _url: canonical_logo())
        )
        self.assertIsNone(
            inspect_customization({"UICustomization": {}}, expected, lambda _url: canonical_logo())
        )
        changed = copy.deepcopy(document)
        changed["UICustomization"]["CSS"] = canonical_css().replace("#6d3de7", "#6d3de8", 1)
        self.assertFalse(
            inspect_customization(changed, expected, lambda _url: canonical_logo())
        )
        inherited = copy.deepcopy(document)
        inherited["UICustomization"]["ClientId"] = "ALL"
        inherited["UICustomization"]["ImageUrl"] = (
            f"{expected.boundary.cognito_domain}/assets/images/image.png"
        )
        self.assertFalse(
            inspect_customization(inherited, expected, lambda _url: canonical_logo())
        )
        for field, value in (
            ("UserPoolId", f"{REGION}_OtherPool"),
            ("ClientId", "b" * 26),
            ("ClientId", []),
            ("ImageUrl", "https://example.com/assets/images/image.png"),
            ("CSSVersion", None),
        ):
            malformed = copy.deepcopy(document)
            malformed["UICustomization"][field] = value
            with self.subTest(field=field), self.assertRaises(CognitoBrandingError):
                inspect_customization(malformed, expected, lambda _url: canonical_logo())

    def test_unsafe_css_and_branding_markers_fail_closed(self):
        for marker in (
            '@import "https://example.com/style.css";',
            ".background-customizable{background:url(https://example.com/a.png)}",
            ".background-customizable{content:'demo-user'}",
            ".background-customizable{content:'demo'}",
            ".background-customizable{content:'analytics tracker'}",
            ".background-customizable{content:'Javascript'}",
            ".background-customizable{content:'whsec_example'}",
            ".unsupported-customizable{color:#000}",
        ):
            with self.subTest(marker=marker), self.assertRaises(CognitoBrandingError):
                validate_css(canonical_css() + marker)

    def test_apply_is_idempotent_and_final_verification_is_mandatory(self):
        expected = target("prod")
        aws = FakeAws(expected)
        self.assertTrue(apply_branding(aws, expected, lambda _url: canonical_logo()))
        self.assertEqual(aws.set_count, 1)
        self.assertFalse(apply_branding(aws, expected, lambda _url: canonical_logo()))
        self.assertEqual(aws.set_count, 1)
        verify_branding(aws, expected, lambda _url: canonical_logo())
        self.assertGreaterEqual(
            sum(operation == "get-ui-customization" for _, operation, _ in aws.calls),
            3,
        )

        broken = FakeAws(expected)
        broken.customization = canonical_customization(expected)
        broken.customization["CSS"] = canonical_css().replace("#6d3de7", "#6d3de8", 1)
        with self.assertRaises(CognitoBrandingError):
            verify_branding(broken, expected, lambda _url: canonical_logo())

        class NonPersistingFake(FakeAws):
            def call(self, service: str, operation: str, *arguments: str) -> dict:
                if operation == "set-ui-customization":
                    self.set_count += 1
                    return {"UICustomization": {}}
                return super().call(service, operation, *arguments)

        with self.assertRaises(CognitoBrandingError):
            apply_branding(
                NonPersistingFake(expected),
                expected,
                lambda _url: canonical_logo(),
            )

    def test_aws_failures_and_malformed_output_are_secret_safe(self):
        cli = AwsCli("jm8-prod", REGION)
        failures = (
            subprocess.CompletedProcess([], 255, "", "AccessDeniedException sk_live_secret"),
            subprocess.CompletedProcess([], 255, "", "Unable to locate credentials whsec_secret"),
            subprocess.CompletedProcess([], 255, "", "Network failure AKIAABCDEFGHIJKLMNOP"),
            subprocess.CompletedProcess([], 0, "not-json", ""),
        )
        for result in failures:
            with self.subTest(result=result), patch("subprocess.run", return_value=result):
                with self.assertRaises(CognitoBrandingError) as raised:
                    cli.call("cognito-idp", "get-ui-customization")
                message = str(raised.exception)
                for secret in ("sk_live_secret", "whsec_secret", "AKIAABCDEFGHIJKLMNOP"):
                    self.assertNotIn(secret, message)
        for error in (
            OSError("network unavailable"),
            subprocess.TimeoutExpired(["aws"], 60),
        ):
            with self.subTest(error=error), patch("subprocess.run", side_effect=error):
                with self.assertRaises(CognitoBrandingError) as raised:
                    cli.call("cognito-idp", "get-ui-customization")
                self.assertNotIn("network unavailable", str(raised.exception))

    def test_create_auth_uses_one_shared_reconciler_before_environment_output(self):
        source = CREATE_AUTH.read_text(encoding="utf-8")
        helper_source = (BIN_DIR / "jm8_cognito_branding.py").read_text(
            encoding="utf-8"
        )
        environment_source = (BIN_DIR / "jm8_environment_contract.py").read_text(
            encoding="utf-8"
        )
        helper_index = source.index('python3 "$COGNITO_BRANDING_HELPER" apply')
        domain_index = source.index('echo "Checking Cognito Hosted UI domain')
        environment_index = source.index('COGNITO_ENV_TEMP="$(mktemp')
        self.assertLess(domain_index, helper_index)
        self.assertLess(helper_index, environment_index)
        self.assertEqual(source.count('jm8_cognito_branding.py'), 1)
        self.assertEqual(source.count('set-ui-customization'), 0)
        self.assertIn("--managed-login-version 1", source)
        self.assertNotIn("PROFILE_BY_STAGE", helper_source)
        self.assertNotIn('"jm8-staging"', helper_source)
        self.assertIn("validate_stage_aws_profile", helper_source)
        self.assertIn('"staging": "jm8-dev"', environment_source)
        for argument in (
            '--app-name "$APP_NAME"',
            '--stage "$STAGE"',
            '--aws-profile "$AWS_PROFILE"',
            '--expected-account-id "$EXPECTED_AWS_ACCOUNT_ID"',
            '--aws-region "$AWS_REGION"',
            '--user-pool-id "$USER_POOL_ID"',
            '--app-client-id "$APP_CLIENT_ID"',
            '--callback-url "$CALLBACK_URL"',
            '--logout-url "$LOGOUT_URL"',
        ):
            self.assertIn(argument, source)
        for prohibited in ("|| true", "eval ", "set -x"):
            self.assertNotIn(prohibited, source)


if __name__ == "__main__":
    unittest.main()
