#!/usr/bin/env python3
"""
Test suite for JM8 Environment Contract and Deployment Guards.

Validates that:
1. Environment variables follow the contract
2. Account validation prevents cross-stage deployment
3. Staging requires confirmation
4. Production is blocked until configured
5. Operation-specific script behavior remains safe
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add backend/bin to path so we can import jm8_environment_contract
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from jm8_environment_contract import (  # noqa: E402
    EnvironmentContractError,
    get_actual_aws_account_id,
    validate_app_name,
    validate_stage,
    validate_aws_configuration,
    validate_stage_account_mapping,
    validate_resource_names,
    validate_non_dev_urls,
    validate_stripe_credentials,
    validate_confirmation_gate,
    validate_allowed_origins,
    validate_environment_contract,
    validate_operation_specific,
)


BACKEND_ROOT = Path(__file__).resolve().parent.parent

SCRIPT_PATHS = [
    BACKEND_ROOT / "bin" / "create-api",
    BACKEND_ROOT / "bin" / "create-auth",
    BACKEND_ROOT / "bin" / "create-resources",
    BACKEND_ROOT / "bin" / "deploy",
    BACKEND_ROOT / "bin" / "deploy-analysis-observability",
    BACKEND_ROOT / "bin" / "deploy-bedrock-budget",
    BACKEND_ROOT / "bin" / "deploy-historical-reanalysis-workflow",
    BACKEND_ROOT / "bin" / "deploy-observability",
    BACKEND_ROOT / "bin" / "deploy-ocr-workflow",
    BACKEND_ROOT / "bin" / "provision-stripe-secret",
    BACKEND_ROOT / "bin" / "setup-stripe-catalog",
    BACKEND_ROOT / "bin" / "jm8_deployment_guard.sh",
]

NON_STRIPE_MUTATING_SCRIPTS = [
    BACKEND_ROOT / "bin" / "create-api",
    BACKEND_ROOT / "bin" / "create-auth",
    BACKEND_ROOT / "bin" / "create-resources",
    BACKEND_ROOT / "bin" / "deploy-analysis-observability",
    BACKEND_ROOT / "bin" / "deploy-bedrock-budget",
    BACKEND_ROOT / "bin" / "deploy-historical-reanalysis-workflow",
    BACKEND_ROOT / "bin" / "deploy-observability",
    BACKEND_ROOT / "bin" / "deploy-ocr-workflow",
]


class EnvironmentIsolationTestCase(unittest.TestCase):
    """Ensure tests do not leak environment variables into each other."""

    MANAGED_ENV_KEYS = {
        "APP_NAME",
        "STAGE",
        "AWS_REGION",
        "AWS_PROFILE",
        "EXPECTED_AWS_ACCOUNT_ID",
        "TABLE_NAME",
        "RAW_BUCKET",
        "DEPLOY_CONFIRMATION",
        "STRIPE_SECRET_KEY",
        "STRIPE_SECRET_ARN",
        "ALLOWED_ORIGINS",
        "ENV_NAME",
        "JM8_OPERATION",
    }

    def setUp(self) -> None:
        self._snapshot = {
            key: os.environ.get(key)
            for key in self.MANAGED_ENV_KEYS
        }

    def tearDown(self) -> None:
        for key in self.MANAGED_ENV_KEYS:
            original = self._snapshot.get(key)
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


class TestEnvironmentContractValidation(EnvironmentIsolationTestCase):
    """Test environment variable validation."""

    def test_app_name_must_equal_journalm8(self):
        """APP_NAME must equal journalm8."""
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_app_name("myapp")
        self.assertIn("journalm8", str(ctx.exception))

    def test_valid_app_name(self):
        """Valid APP_NAME passes."""
        validate_app_name("journalm8")

    def test_stage_must_be_dev_staging_or_prod(self):
        """STAGE must be exactly dev, staging, or prod."""
        for invalid_stage in ["development", "test", "staging-temp", "PROD", "Dev"]:
            with self.assertRaises(EnvironmentContractError) as ctx:
                validate_stage(invalid_stage)
            self.assertIn("one of", str(ctx.exception))

    def test_valid_stages(self):
        """Valid stages pass."""
        for valid_stage in ["dev", "staging", "prod"]:
            validate_stage(valid_stage)


class TestResourceNamingAndURLs(EnvironmentIsolationTestCase):

    def test_table_name_must_match_pattern(self):
        """TABLE_NAME must match ${APP_NAME}-${STAGE}-main."""
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_resource_names(
                "journalm8",
                "dev",
                "wrong-name",
                "journalm8-dev-raw-123456789012",
                "123456789012",
            )
        self.assertIn("journalm8-dev-main", str(ctx.exception))

    def test_raw_bucket_must_match_pattern(self):
        """RAW_BUCKET must match ${APP_NAME}-${STAGE}-raw-${ACCOUNT_ID}."""
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_resource_names(
                "journalm8",
                "dev",
                "journalm8-dev-main",
                "wrong-bucket-name",
                "123456789012",
            )
        self.assertIn("journalm8-dev-raw-123456789012", str(ctx.exception))

    def test_localhost_rejected_in_staging(self):
        """localhost URLs rejected in staging."""
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_non_dev_urls("staging", "http://localhost:5173/")
        self.assertIn("localhost", str(ctx.exception))

    def test_localhost_allowed_in_dev(self):
        """localhost URLs allowed in dev."""
        validate_non_dev_urls("dev", "http://localhost:5173/")

    def test_127_0_0_1_rejected_in_staging(self):
        """127.0.0.1 URLs rejected in staging."""
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_non_dev_urls("staging", "http://127.0.0.1:5173/")
        self.assertIn("127.0.0.1", str(ctx.exception))

    def test_allowed_origins_dev_allows_localhost(self):
        origins = validate_allowed_origins(
            "dev",
            "http://localhost:5173,http://127.0.0.1:5173",
        )
        self.assertEqual(
            origins,
            ["http://localhost:5173", "http://127.0.0.1:5173"],
        )

    def test_allowed_origins_staging_requires_https(self):
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_allowed_origins("staging", "http://staging.example.com")
        self.assertIn("https", str(ctx.exception))

    def test_allowed_origins_prod_rejects_localhost(self):
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_allowed_origins("prod", "https://app.example.com,https://localhost")
        self.assertIn("localhost", str(ctx.exception))

    def test_allowed_origins_rejects_paths_and_wildcards(self):
        with self.assertRaises(EnvironmentContractError):
            validate_allowed_origins("dev", "https://example.com/path")

        with self.assertRaises(EnvironmentContractError):
            validate_allowed_origins("dev", "https://*.example.com")

    def test_allowed_origins_rejects_duplicates(self):
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_allowed_origins(
                "dev",
                "https://example.com,https://example.com",
            )
        self.assertIn("duplicate", str(ctx.exception))

    def test_allowed_origins_rejects_malformed_port(self):
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_allowed_origins("dev", "https://example.com:abc")
        self.assertIn("invalid port", str(ctx.exception))


class TestStripeValidation(EnvironmentIsolationTestCase):

    def test_stripe_test_mode_required_for_dev(self):
        """Dev stage requires sk_test_ prefix."""
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_stripe_credentials("dev", stripe_secret_key="sk_live_123")
        self.assertIn("test-mode", str(ctx.exception))

    def test_stripe_test_mode_required_for_staging(self):
        """Staging stage requires sk_test_ prefix."""
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_stripe_credentials("staging", stripe_secret_key="sk_live_123")
        self.assertIn("test-mode", str(ctx.exception))

    def test_stripe_live_mode_required_for_prod(self):
        """Prod stage requires sk_live_ prefix."""
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_stripe_credentials("prod", stripe_secret_key="sk_test_123")
        self.assertIn("live-mode", str(ctx.exception))

    def test_valid_stripe_test_mode(self):
        """Valid test mode credentials pass."""
        validate_stripe_credentials("dev", stripe_secret_key="sk_test_4eC39HqLyjWDarhtT657tx3")
        validate_stripe_credentials("staging", stripe_secret_key="sk_test_4eC39HqLyjWDarhtT657tx3")

    def test_valid_stripe_live_mode(self):
        """Valid live mode credentials pass for prod."""
        validate_stripe_credentials("prod", stripe_secret_key="sk_live_4eC39HqLyjWDarhtT657tx3")


class TestAccountMapping(EnvironmentIsolationTestCase):

    def test_dev_requires_114743615542(self):
        validate_stage_account_mapping("dev", "114743615542")

    def test_dev_rejects_other_accounts(self):
        with self.assertRaises(EnvironmentContractError):
            validate_stage_account_mapping("dev", "999999999999")

    def test_staging_requires_114743615542(self):
        validate_stage_account_mapping("staging", "114743615542")

    def test_staging_rejects_other_accounts(self):
        with self.assertRaises(EnvironmentContractError):
            validate_stage_account_mapping("staging", "999999999999")

    def test_prod_blocked_unconditionally(self):
        with self.assertRaises(EnvironmentContractError):
            validate_stage_account_mapping("prod", "114743615542")


class TestConfirmationGates(EnvironmentIsolationTestCase):

    def test_dev_no_confirmation_required(self):
        validate_confirmation_gate("dev")
        os.environ["DEPLOY_CONFIRMATION"] = "dev"
        validate_confirmation_gate("dev")

    def test_staging_requires_correct_confirmation(self):
        os.environ.pop("DEPLOY_CONFIRMATION", None)
        with self.assertRaises(EnvironmentContractError):
            validate_confirmation_gate("staging")
        os.environ["DEPLOY_CONFIRMATION"] = "staging"
        validate_confirmation_gate("staging")

    def test_prod_requires_correct_confirmation(self):
        os.environ.pop("DEPLOY_CONFIRMATION", None)
        with self.assertRaises(EnvironmentContractError):
            validate_confirmation_gate("prod")
        os.environ["DEPLOY_CONFIRMATION"] = "prod"
        validate_confirmation_gate("prod")


class TestAWSAccountValidation(EnvironmentIsolationTestCase):

    @patch("jm8_environment_contract.subprocess.run")
    def test_sts_call_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="114743615542\n")
        account_id = get_actual_aws_account_id("jm8-dev", "us-east-1")
        self.assertEqual(account_id, "114743615542")

    @patch("jm8_environment_contract.subprocess.run")
    def test_sts_call_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        account_id = get_actual_aws_account_id("jm8-dev", "us-east-1")
        self.assertIsNone(account_id)

    @patch("jm8_environment_contract.subprocess.run")
    def test_validation_only_uses_read_only_sts_call(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="114743615542\n")

        result = get_actual_aws_account_id("jm8-dev", "us-east-1")
        self.assertEqual(result, "114743615542")

        self.assertTrue(mock_run.called)
        command = mock_run.call_args.args[0]
        self.assertEqual(command[0:3], ["aws", "sts", "get-caller-identity"])

        mutating_verbs = {
            "create",
            "update",
            "delete",
            "put",
            "attach",
            "detach",
            "start",
            "stop",
            "execute",
        }
        self.assertTrue(mutating_verbs.isdisjoint(set(command)))

    @patch("jm8_environment_contract.get_actual_aws_account_id")
    def test_account_mismatch_detected(self, mock_sts):
        mock_sts.return_value = "999999999999"
        with self.assertRaises(EnvironmentContractError):
            validate_aws_configuration("us-east-1", "jm8-dev", "114743615542")

    @patch("jm8_environment_contract.get_actual_aws_account_id")
    def test_account_match_passes(self, mock_sts):
        mock_sts.return_value = "114743615542"
        result = validate_aws_configuration("us-east-1", "jm8-dev", "114743615542")
        self.assertEqual(result, "114743615542")


class TestCompleteContractValidation(EnvironmentIsolationTestCase):

    @patch("jm8_environment_contract.get_actual_aws_account_id")
    def test_valid_dev_environment(self, mock_sts):
        mock_sts.return_value = "114743615542"
        os.environ.update({
            "APP_NAME": "journalm8",
            "STAGE": "dev",
            "AWS_REGION": "us-east-1",
            "AWS_PROFILE": "jm8-dev",
            "EXPECTED_AWS_ACCOUNT_ID": "114743615542",
            "TABLE_NAME": "journalm8-dev-main",
            "RAW_BUCKET": "journalm8-dev-raw-114743615542",
        })
        os.environ.pop("DEPLOY_CONFIRMATION", None)

        config = validate_environment_contract()
        self.assertEqual(config["stage"], "dev")
        self.assertEqual(config["account_id"], "114743615542")

    @patch("jm8_environment_contract.get_actual_aws_account_id")
    def test_valid_staging_environment(self, mock_sts):
        mock_sts.return_value = "114743615542"
        os.environ.update({
            "APP_NAME": "journalm8",
            "STAGE": "staging",
            "AWS_REGION": "us-east-1",
            "AWS_PROFILE": "jm8-staging",
            "EXPECTED_AWS_ACCOUNT_ID": "114743615542",
            "TABLE_NAME": "journalm8-staging-main",
            "RAW_BUCKET": "journalm8-staging-raw-114743615542",
            "DEPLOY_CONFIRMATION": "staging",
        })

        config = validate_environment_contract()
        self.assertEqual(config["stage"], "staging")
        self.assertEqual(config["account_id"], "114743615542")

    @patch("jm8_environment_contract.get_actual_aws_account_id")
    def test_production_always_blocked(self, mock_sts):
        mock_sts.return_value = "999999999999"
        os.environ.update({
            "APP_NAME": "journalm8",
            "STAGE": "prod",
            "AWS_REGION": "us-east-1",
            "AWS_PROFILE": "jm8-prod",
            "EXPECTED_AWS_ACCOUNT_ID": "999999999999",
            "TABLE_NAME": "journalm8-prod-main",
            "RAW_BUCKET": "journalm8-prod-raw-999999999999",
            "DEPLOY_CONFIRMATION": "prod",
        })

        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_environment_contract()
        self.assertIn("not yet configured", str(ctx.exception))

    @patch("jm8_environment_contract.get_actual_aws_account_id")
    def test_create_auth_env_name_must_match_stage_when_set(self, mock_sts):
        mock_sts.return_value = "114743615542"
        os.environ.update({
            "APP_NAME": "journalm8",
            "STAGE": "staging",
            "ENV_NAME": "dev",
            "AWS_REGION": "us-east-1",
            "AWS_PROFILE": "jm8-staging",
            "EXPECTED_AWS_ACCOUNT_ID": "114743615542",
            "TABLE_NAME": "journalm8-staging-main",
            "RAW_BUCKET": "journalm8-staging-raw-114743615542",
            "DEPLOY_CONFIRMATION": "staging",
        })

        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_environment_contract()
        self.assertIn("ENV_NAME", str(ctx.exception))


class TestOperationSpecificValidation(EnvironmentIsolationTestCase):

    def _read(self, relative_path: str) -> str:
        return (BACKEND_ROOT / relative_path).read_text(encoding="utf-8")

    def _assert_requires_var(self, script_text: str, var_name: str) -> None:
        self.assertIn(f': "${{{var_name}:?', script_text)

    def _assert_not_requires_var(self, script_text: str, var_name: str) -> None:
        self.assertNotIn(f': "${{{var_name}:?', script_text)

    def test_create_resources_does_not_require_raw_stripe_key(self):
        script_text = self._read("bin/create-resources")
        self._assert_not_requires_var(script_text, "STRIPE_SECRET_KEY")

    def test_create_api_requires_allowed_origins(self):
        script_text = self._read("bin/create-api")
        self._assert_requires_var(script_text, "ALLOWED_ORIGINS")
        self.assertNotIn('"AllowOrigins": ["*"]', script_text)

    def test_create_resources_requires_allowed_origins(self):
        script_text = self._read("bin/create-resources")
        self._assert_requires_var(script_text, "ALLOWED_ORIGINS")
        self.assertIn('allowed-origins-json', script_text)

    def test_non_stripe_mutating_scripts_do_not_require_raw_stripe_key(self):
        for script_path in NON_STRIPE_MUTATING_SCRIPTS:
            script_text = script_path.read_text(encoding="utf-8")
            with self.subTest(script=script_path.name):
                self._assert_not_requires_var(script_text, "STRIPE_SECRET_KEY")

    def test_deploy_requires_secret_arn_without_requiring_raw_key(self):
        script_text = self._read("bin/deploy")
        self._assert_requires_var(script_text, "STRIPE_SECRET_ARN")
        self._assert_not_requires_var(script_text, "STRIPE_SECRET_KEY")
        self.assertIn('if "STRIPE_SECRET_KEY" in variables:', script_text)

    def test_provision_stripe_secret_rejects_wrong_key_mode(self):
        script_text = self._read("bin/provision-stripe-secret")
        self._assert_requires_var(script_text, "STRIPE_SECRET_KEY")
        self.assertIn('export JM8_OPERATION="provision-stripe-secret"', script_text)
        self.assertIn('source "$SCRIPT_DIR/jm8_deployment_guard.sh"', script_text)
        self.assertIn("jm8_validate_contract_or_exit", script_text)
        self.assertIn('"dev": "sk_test_"', script_text)
        self.assertIn('"staging": "sk_test_"', script_text)
        self.assertIn('"prod": "sk_live_"', script_text)

    def test_setup_stripe_catalog_enforces_stage_appropriate_mode(self):
        script_text = self._read("bin/setup-stripe-catalog")
        self.assertIn('export JM8_OPERATION="setup-stripe-catalog"', script_text)
        self.assertIn('source "$SCRIPT_DIR/jm8_deployment_guard.sh"', script_text)
        self.assertIn("jm8_validate_contract_or_exit", script_text)
        self.assertIn('"dev": "sk_test_"', script_text)
        self.assertIn('"staging": "sk_test_"', script_text)
        self.assertIn("sk_live_", script_text)

    def test_create_auth_derives_env_name_from_stage(self):
        script_text = self._read("bin/create-auth")
        self.assertIn('ENV_NAME="$STAGE"', script_text)
        self.assertNotIn('ENV_NAME="${ENV_NAME:-', script_text)
        self.assertIn('export JM8_OPERATION="create-auth"', script_text)

    def test_deploy_sets_operation_name_for_contract_checks(self):
        script_text = self._read("bin/deploy")
        self.assertIn('export JM8_OPERATION="deploy"', script_text)

    def test_all_mutating_scripts_call_shared_guard(self):
        for script_path in SCRIPT_PATHS:
            script_text = script_path.read_text(encoding="utf-8")
            if script_path.name == "jm8_deployment_guard.sh":
                with self.subTest(script=script_path.name):
                    self.assertIn("jm8_validate_contract_or_exit", script_text)
                continue

            with self.subTest(script=script_path.name):
                self.assertIn('source "$SCRIPT_DIR/jm8_deployment_guard.sh"', script_text)
                self.assertIn("jm8_validate_contract_or_exit", script_text)

    def test_no_script_sources_env_example_files(self):
        for script_path in SCRIPT_PATHS:
            script_text = script_path.read_text(encoding="utf-8")
            with self.subTest(script=script_path.name):
                self.assertNotIn(".env.example", script_text)
                self.assertNotRegex(script_text, r"source\s+.*\.env\.example")


class TestOperationSpecificContractHooks(EnvironmentIsolationTestCase):

    @patch("jm8_environment_contract.get_actual_aws_account_id")
    def test_deploy_operation_specific_validation_requires_arn(self, mock_sts):
        mock_sts.return_value = "114743615542"
        os.environ.update({
            "APP_NAME": "journalm8",
            "STAGE": "dev",
            "AWS_REGION": "us-east-1",
            "AWS_PROFILE": "jm8-dev",
            "EXPECTED_AWS_ACCOUNT_ID": "114743615542",
            "TABLE_NAME": "journalm8-dev-main",
            "RAW_BUCKET": "journalm8-dev-raw-114743615542",
            "JM8_OPERATION": "deploy",
            "STRIPE_SECRET_KEY": "",
        })

        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_environment_contract()
        self.assertIn("STRIPE_SECRET_ARN", str(ctx.exception))

    def test_unrelated_operations_do_not_require_raw_stripe_key(self):
        validate_operation_specific("deploy-observability", "staging")

    def test_create_resources_requires_allowed_origins_contract(self):
        os.environ.pop("ALLOWED_ORIGINS", None)
        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_operation_specific("create-resources", "dev")
        self.assertIn("ALLOWED_ORIGINS", str(ctx.exception))

    def test_create_auth_rejects_non_dev_localhost_callback(self):
        os.environ["CALLBACK_URL"] = "http://localhost:5173/callback"
        os.environ["LOGOUT_URL"] = "https://staging.example.com"

        with self.assertRaises(EnvironmentContractError) as ctx:
            validate_operation_specific("create-auth", "staging")
        self.assertIn("localhost", str(ctx.exception))


class TestTemplatesAndIgnoreRules(EnvironmentIsolationTestCase):

    def test_templates_contain_no_real_secret_values(self):
        template_paths = [
            BACKEND_ROOT / "infra" / "environments" / "dev.env.example",
            BACKEND_ROOT / "infra" / "environments" / "staging.env.example",
            BACKEND_ROOT / "infra" / "environments" / "prod.env.example",
        ]

        forbidden_patterns = [
            r"AKIA[0-9A-Z]{16}",
            r"(?<!REPLACE_WITH_)(?<!REPLACE_WITH_PROD_)(?<!REPLACE_WITH_DEV_)(?<!REPLACE_WITH_STAGING_)sk_(?:test|live)_[A-Za-z0-9]{16,}",
            r"(?<!REPLACE_WITH_)(?<!REPLACE_WITH_PROD_)(?<!REPLACE_WITH_DEV_)(?<!REPLACE_WITH_STAGING_)whsec_[A-Za-z0-9]{16,}",
            r"-----BEGIN (?:RSA|EC|OPENSSH|PRIVATE) KEY-----",
        ]

        import re

        for template_path in template_paths:
            content = template_path.read_text(encoding="utf-8")
            for pattern in forbidden_patterns:
                with self.subTest(template=template_path.name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, content))

    def test_local_environment_files_are_ignored(self):
        command = [
            "git",
            "check-ignore",
            "-v",
            ".env",
            ".env.local",
            "backend/.env",
            "frontend/.env.local",
        ]
        result = subprocess.run(
            command,
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        stdout = result.stdout
        self.assertIn(".env", stdout)
        self.assertIn(".env.local", stdout)
        self.assertIn("backend/.env", stdout)
        self.assertIn("frontend/.env.local", stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
