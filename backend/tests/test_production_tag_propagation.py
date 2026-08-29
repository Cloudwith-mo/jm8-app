from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))

from jm8_production_deployer_policies import generate_policies  # noqa: E402
from jm8_cognito_branding import (  # noqa: E402
    BrandingBoundary,
    BrandingTarget,
    CognitoBrandingError,
    validate_app_client,
)
from jm8_resource_tag_contract import (  # noqa: E402
    TagContractError,
    _context,
    resolve_api,
    resolve_user_pool,
    validate_api,
    validate_lambda,
    validate_user_pool,
    verify_exact_tags,
    verify_tags,
)


ACCOUNT_ID = "114743615542"
REGION = "us-east-1"
APP_NAME = "journalm8"


class ResourceTagContractTests(unittest.TestCase):
    def test_approved_production_context_is_exact(self):
        _context(APP_NAME, "prod", ACCOUNT_ID, REGION)
        for app_name, account_id in (
            ("other-app", ACCOUNT_ID),
            (APP_NAME, "999999999999"),
        ):
            with self.subTest(app_name=app_name, account_id=account_id):
                with self.assertRaisesRegex(TagContractError, "not approved"):
                    _context(app_name, "prod", account_id, REGION)

    def test_dev_and_staging_contexts_remain_stage_aware(self):
        for stage in ("dev", "staging"):
            with self.subTest(stage=stage):
                _context(APP_NAME, stage, ACCOUNT_ID, REGION)
                verify_tags(
                    {
                        "Tags": {
                            "App": APP_NAME,
                            "Stage": stage,
                            "ManagedBy": "aws-cli",
                            "Unrelated": "preserved",
                        }
                    },
                    APP_NAME,
                    stage,
                    before_reconcile=False,
                )

        with self.assertRaisesRegex(TagContractError, "not approved"):
            _context("caller-selected-app", "dev", ACCOUNT_ID, REGION)

    def test_api_resolution_requires_one_exact_production_name(self):
        api_id = resolve_api(
            {
                "Items": [
                    {"ApiId": "dev1234567", "Name": "journalm8-dev-api"},
                    {"ApiId": "prod123456", "Name": "journalm8-prod-api"},
                ]
            },
            "journalm8-prod-api",
        )
        self.assertEqual(api_id, "prod123456")
        self.assertEqual(resolve_api({"Items": []}, "journalm8-prod-api"), "")

        duplicated = {
            "Items": [
                {"ApiId": "prod123456", "Name": "journalm8-prod-api"},
                {"ApiId": "prod654321", "Name": "journalm8-prod-api"},
            ]
        }
        with self.assertRaisesRegex(TagContractError, "duplicated"):
            resolve_api(duplicated, "journalm8-prod-api")

    def test_api_identity_mismatch_fails_closed(self):
        validate_api(
            {
                "ApiId": "prod123456",
                "Name": "journalm8-prod-api",
                "ProtocolType": "HTTP",
            },
            "prod123456",
            "journalm8-prod-api",
        )
        for response in (
            {
                "ApiId": "prod123456",
                "Name": "journalm8-staging-api",
                "ProtocolType": "HTTP",
            },
            {
                "ApiId": "prod123456",
                "Name": "journalm8-prod-api",
                "ProtocolType": "WEBSOCKET",
            },
            {"ApiId": "prod123456", "Name": "journalm8-prod-api"},
        ):
            with self.subTest(response=response):
                with self.assertRaisesRegex(TagContractError, "does not match"):
                    validate_api(
                        response,
                        "prod123456",
                        "journalm8-prod-api",
                    )

    def test_production_embedded_api_tags_require_exact_canonical_map(self):
        canonical = {
            "Tags": {
                "App": APP_NAME,
                "Stage": "prod",
                "ManagedBy": "aws-cli",
            }
        }
        verify_exact_tags(canonical, APP_NAME, "prod")

        invalid_responses = (
            {},
            {"Tags": []},
            {"Tags": {"App": APP_NAME, "Stage": "prod"}},
            {
                "Tags": {
                    **canonical["Tags"],
                    "Unrelated": "rejected",
                }
            },
            {
                "Tags": {
                    "App": APP_NAME,
                    "Stage": "dev",
                    "ManagedBy": "aws-cli",
                }
            },
            {
                "Tags": {
                    "App": APP_NAME,
                    "Stage": "staging",
                    "ManagedBy": "aws-cli",
                }
            },
            {
                "Tags": {
                    "App": "other",
                    "Stage": "prod",
                    "ManagedBy": "aws-cli",
                }
            },
            {
                "Tags": {
                    "App": APP_NAME,
                    "Stage": "prod",
                    "ManagedBy": "console",
                }
            },
            {
                "Tags": {
                    "App": APP_NAME,
                    "Stage": "prod",
                    "ManagedBy": 3,
                }
            },
        )
        for response in invalid_responses:
            with self.subTest(response=response):
                with self.assertRaises(TagContractError):
                    verify_exact_tags(response, APP_NAME, "prod")

    def test_cognito_identity_requires_exact_name_account_and_region(self):
        pool_id = "us-east-1_ProdPool123"
        inventory = {
            "UserPools": [
                {"Id": "us-east-1_StagePool", "Name": "journalm8-staging-users"},
                {"Id": pool_id, "Name": "journalm8-prod-users"},
            ]
        }
        self.assertEqual(
            resolve_user_pool(inventory, "journalm8-prod-users", REGION),
            pool_id,
        )
        arn = validate_user_pool(
            {
                "UserPool": {
                    "Id": pool_id,
                    "Name": "journalm8-prod-users",
                    "Arn": (
                        f"arn:aws:cognito-idp:{REGION}:{ACCOUNT_ID}:"
                        f"userpool/{pool_id}"
                    ),
                }
            },
            pool_id,
            "journalm8-prod-users",
            ACCOUNT_ID,
            REGION,
        )
        self.assertEqual(
            arn,
            f"arn:aws:cognito-idp:{REGION}:{ACCOUNT_ID}:userpool/{pool_id}",
        )
        with self.assertRaisesRegex(TagContractError, "does not match"):
            validate_user_pool(
                {
                    "UserPool": {
                        "Id": pool_id,
                        "Name": "journalm8-prod-users",
                        "Arn": (
                            f"arn:aws:cognito-idp:{REGION}:999999999999:"
                            f"userpool/{pool_id}"
                        ),
                    }
                },
                pool_id,
                "journalm8-prod-users",
                ACCOUNT_ID,
                REGION,
            )

    def test_lambda_identity_requires_exact_name_account_and_region(self):
        function_name = "journalm8-prod-api"
        expected_arn = (
            f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{function_name}"
        )
        self.assertEqual(
            validate_lambda(
                {
                    "Configuration": {
                        "FunctionName": function_name,
                        "FunctionArn": expected_arn,
                    }
                },
                function_name,
                ACCOUNT_ID,
                REGION,
            ),
            expected_arn,
        )
        with self.assertRaisesRegex(TagContractError, "does not match"):
            validate_lambda(
                {
                    "Configuration": {
                        "FunctionName": "journalm8-staging-api",
                        "FunctionArn": expected_arn,
                    }
                },
                function_name,
                ACCOUNT_ID,
                REGION,
            )

    def test_pre_reconcile_rejects_cross_stage_tags(self):
        verify_tags(
            {"Tags": {}},
            APP_NAME,
            "prod",
            before_reconcile=True,
        )
        verify_tags(
            {"Tags": {"App": APP_NAME, "Stage": "prod"}},
            APP_NAME,
            "prod",
            before_reconcile=True,
        )
        for tags in (
            {"App": APP_NAME, "Stage": "staging"},
            {"App": "other-app", "Stage": "prod"},
        ):
            with self.subTest(tags=tags):
                with self.assertRaisesRegex(TagContractError, "different"):
                    verify_tags(
                        {"Tags": tags},
                        APP_NAME,
                        "prod",
                        before_reconcile=True,
                    )

    def test_postcondition_requires_all_three_exact_tags(self):
        verify_tags(
            {
                "Tags": {
                    "App": APP_NAME,
                    "Stage": "prod",
                    "ManagedBy": "aws-cli",
                    "Unrelated": "preserved",
                }
            },
            APP_NAME,
            "prod",
            before_reconcile=False,
        )
        for tags in (
            {"App": APP_NAME, "Stage": "prod"},
            {"App": APP_NAME, "Stage": "prod", "ManagedBy": "console"},
        ):
            with self.subTest(tags=tags):
                with self.assertRaisesRegex(TagContractError, "not applied"):
                    verify_tags(
                        {"Tags": tags},
                        APP_NAME,
                        "prod",
                        before_reconcile=False,
                    )

    def test_malformed_tag_lookup_is_never_missing_tags(self):
        for response in ({}, {"Tags": []}, {"Tags": {"Stage": 3}}):
            with self.subTest(response=response):
                with self.assertRaisesRegex(TagContractError, "malformed"):
                    verify_tags(
                        response,
                        APP_NAME,
                        "prod",
                        before_reconcile=True,
                    )


class DeploymentTagWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {
            name: (BIN_DIR / name).read_text(encoding="utf-8")
            for name in (
                "create-auth",
                "create-api",
                "secure-api",
                "deploy",
                "deploy-ocr-workflow",
                "deploy-historical-reanalysis-workflow",
                "deploy-observability",
                "jm8_resource_tags.sh",
                "jm8_resource_tag_contract.py",
            )
        }

    @staticmethod
    def _run_api_tag_helper(
        function_name,
        stage,
        api_document,
        tag_document=None,
    ):
        environment = os.environ.copy()
        environment.update({
            "APP_NAME": APP_NAME,
            "AWS_PROFILE": f"jm8-{stage}",
            "AWS_REGION": REGION,
            "EXPECTED_AWS_ACCOUNT_ID": ACCOUNT_ID,
            "JM8_TEST_API_DOCUMENT": json.dumps(api_document),
            "JM8_TEST_TAG_DOCUMENT": json.dumps(
                tag_document if tag_document is not None else {}
            ),
            "STAGE": stage,
        })
        script = r'''
source "$1"
helper_function="$2"
api_id="$3"
expected_name="$4"

aws() {
  printf 'AWS_CALL:%s %s\n' "$1" "$2" >&2
  case "$1:$2" in
    apigatewayv2:get-api)
      printf '%s' "$JM8_TEST_API_DOCUMENT"
      ;;
    apigatewayv2:get-tags)
      printf '%s' "$JM8_TEST_TAG_DOCUMENT"
      ;;
    apigatewayv2:tag-resource)
      return 0
      ;;
    *)
      return 97
      ;;
  esac
}

"$helper_function" "$api_id" "$expected_name"
'''
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "api-tag-helper-test",
                str(BIN_DIR / "jm8_resource_tags.sh"),
                function_name,
                api_document.get("ApiId", "prod123456"),
                f"{APP_NAME}-{stage}-api",
            ],
            capture_output=True,
            env=environment,
            text=True,
            check=False,
        )

    @staticmethod
    def _run_lambda_tag_helper(function_name, tag_document):
        stage = "staging"
        environment = os.environ.copy()
        environment.update({
            "APP_NAME": APP_NAME,
            "AWS_PROFILE": f"jm8-{stage}",
            "AWS_REGION": REGION,
            "EXPECTED_AWS_ACCOUNT_ID": ACCOUNT_ID,
            "JM8_TEST_FUNCTION_DOCUMENT": json.dumps({
                "Configuration": {
                    "FunctionName": function_name,
                    "FunctionArn": (
                        f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:"
                        f"function:{function_name}"
                    ),
                }
            }),
            "JM8_TEST_TAG_DOCUMENT": json.dumps(tag_document),
            "STAGE": stage,
        })
        script = r'''
set -euo pipefail
source "$1"
function_name="$2"

aws() {
  printf 'AWS_CALL:%s %s\n' "$1" "$2" >&2
  case "$1:$2" in
    lambda:get-function)
      printf '%s' "$JM8_TEST_FUNCTION_DOCUMENT"
      ;;
    lambda:list-tags)
      printf '%s' "$JM8_TEST_TAG_DOCUMENT"
      ;;
    lambda:tag-resource)
      return 0
      ;;
    *)
      return 97
      ;;
  esac
}

jm8_reconcile_lambda_tags "$function_name"
'''
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "lambda-tag-helper-test",
                str(BIN_DIR / "jm8_resource_tags.sh"),
                function_name,
            ],
            capture_output=True,
            env=environment,
            text=True,
            check=False,
        )

    @staticmethod
    def _lambda_create_blocks(source):
        return re.findall(
            r"aws lambda create-function \\\n(.*?\n\s*>/dev/null)",
            source,
            flags=re.DOTALL,
        )

    def _assert_one_canonical_json_tag_argument(self, block, service):
        match = re.search(
            rf'aws {re.escape(service)} tag-resource .*?--tags \\\n'
            r'\s+"((?:\\.|[^"\\])*)" \\\n\s+--profile ',
            block,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertEqual(block.count("--tags"), 1)
        json_template = match.group(1).replace(r'\"', '"')

        for stage in ("dev", "staging", "prod"):
            with self.subTest(service=service, stage=stage):
                expanded_json = json_template.replace(
                    "${APP_NAME}", APP_NAME
                ).replace("${STAGE}", stage)
                self.assertEqual(
                    json.loads(expanded_json),
                    {
                        "App": APP_NAME,
                        "Stage": stage,
                        "ManagedBy": "aws-cli",
                    },
                )

        self.assertNotIn(
            "App=${APP_NAME},Stage=${STAGE},ManagedBy=aws-cli",
            block,
        )
        for separate_tag in (
            '"App=${APP_NAME}"',
            '"Stage=${STAGE}"',
            '"ManagedBy=aws-cli"',
        ):
            self.assertNotIn(separate_tag, block)

    def test_production_create_commands_supply_canonical_tags(self):
        create_auth = self.sources["create-auth"]
        create_api = self.sources["create-api"]
        self.assertIn("aws cognito-idp create-user-pool", create_auth)
        self.assertIn("aws apigatewayv2 create-api", create_api)
        self.assertIn(
            '"{\\"App\\":\\"${APP_NAME}\\",\\"Stage\\":\\"${STAGE}\\",'
            '\\"ManagedBy\\":\\"aws-cli\\"}"',
            create_auth,
        )
        self.assertIn(
            '"{\\"App\\":\\"${APP_NAME}\\",\\"Stage\\":\\"${STAGE}\\",'
            '\\"ManagedBy\\":\\"aws-cli\\"}"',
            create_api,
        )

    def test_api_create_tags_are_one_json_shell_argument(self):
        source = self.sources["create-api"]
        create_block = source[
            source.index("aws apigatewayv2 create-api \\"):
            source.index('echo "Created API: $API_ID"')
        ]
        match = re.search(
            r'--tags \\\n\s+"((?:\\.|[^"\\])*)" \\\n\s+--query ',
            create_block,
        )
        self.assertIsNotNone(match)
        self.assertEqual(create_block.count("--tags"), 1)
        json_template = match.group(1).replace(r'\"', '"')

        for stage in ("dev", "staging", "prod"):
            with self.subTest(stage=stage):
                expanded_json = json_template.replace(
                    "${APP_NAME}", APP_NAME
                ).replace("${STAGE}", stage)
                self.assertEqual(
                    json.loads(expanded_json),
                    {
                        "App": APP_NAME,
                        "Stage": stage,
                        "ManagedBy": "aws-cli",
                    },
                )

        self.assertNotIn(
            "App=${APP_NAME},Stage=${STAGE},ManagedBy=aws-cli",
            create_block,
        )
        for separate_tag in (
            '"App=${APP_NAME}"',
            '"Stage=${STAGE}"',
            '"ManagedBy=aws-cli"',
        ):
            self.assertNotIn(separate_tag, create_block)
        for list_tag_prefix in ("key=App", "Key=App"):
            self.assertNotIn(list_tag_prefix, create_block)

    def test_cognito_create_tags_are_one_json_shell_argument(self):
        source = self.sources["create-auth"]
        create_block = source[
            source.index("aws cognito-idp create-user-pool \\"):
            source.index("else\n  echo \"User pool already exists")
        ]
        match = re.search(
            r'--user-pool-tags \\\n\s+"((?:\\.|[^"\\])*)" \\',
            create_block,
        )
        self.assertIsNotNone(match)
        encoded_json = match.group(1)
        json_template = encoded_json.replace(r'\"', '"')

        for stage in ("dev", "staging", "prod"):
            with self.subTest(stage=stage):
                expanded_json = json_template.replace(
                    "${APP_NAME}", APP_NAME
                ).replace("${STAGE}", stage)
                self.assertEqual(
                    json.loads(expanded_json),
                    {
                        "App": APP_NAME,
                        "Stage": stage,
                        "ManagedBy": "aws-cli",
                    },
                )

        self.assertNotIn(
            'App=${APP_NAME},Stage=${STAGE},ManagedBy=aws-cli',
            create_block,
        )
        for separate_tag in (
            '"App=${APP_NAME}"',
            '"Stage=${STAGE}"',
            '"ManagedBy=aws-cli"',
        ):
            self.assertNotIn(separate_tag, create_block)

    def test_cognito_create_tags_remain_stage_aware_for_all_supported_stages(self):
        source = self.sources["create-auth"]
        self.assertIn("dev|staging|prod", source)
        self.assertIn(r'\"Stage\":\"${STAGE}\"', source)
        for hard_coded_stage in (
            r'\"Stage\":\"dev\"',
            r'\"Stage\":\"staging\"',
            r'\"Stage\":\"prod\"',
        ):
            self.assertNotIn(hard_coded_stage, source)

    def test_cognito_reconcile_tags_are_one_json_shell_argument(self):
        helper = self.sources["jm8_resource_tags.sh"]
        reconcile_block = helper[
            helper.index("jm8_reconcile_cognito_user_pool_tags()"):
            helper.index("jm8_reconcile_lambda_tags()")
        ]
        self._assert_one_canonical_json_tag_argument(
            reconcile_block,
            "cognito-idp",
        )
        self.assertIn('if [ "$APP_NAME" != "journalm8" ]', helper)
        self.assertIn("dev|staging|prod", helper)

    def test_api_reconcile_tags_are_one_json_shell_argument(self):
        helper = self.sources["jm8_resource_tags.sh"]
        reconcile_block = helper[
            helper.index("jm8_reconcile_http_api_tags()"):
            helper.index("jm8_resolve_cognito_user_pool_id()")
        ]
        self._assert_one_canonical_json_tag_argument(
            reconcile_block,
            "apigatewayv2",
        )

    def test_lambda_reconcile_tags_are_one_json_shell_argument(self):
        helper = self.sources["jm8_resource_tags.sh"]
        reconcile_block = helper[
            helper.index("jm8_reconcile_lambda_tags()"):
        ]
        self._assert_one_canonical_json_tag_argument(
            reconcile_block,
            "lambda",
        )

    def test_lambda_allowlist_accepts_account_export_worker_and_rejects_others(self):
        function_name = f"{APP_NAME}-staging-account-export-worker"
        canonical_tags = {
            "Tags": {
                "App": APP_NAME,
                "Stage": "staging",
                "ManagedBy": "aws-cli",
            }
        }
        accepted = self._run_lambda_tag_helper(function_name, canonical_tags)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(
            re.findall(r"AWS_CALL:lambda ([a-z-]+)", accepted.stderr),
            ["get-function", "list-tags", "tag-resource", "list-tags"],
        )

        rejected = self._run_lambda_tag_helper(
            f"{APP_NAME}-staging-arbitrary-worker",
            canonical_tags,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(
            "Lambda name does not match the stage contract.",
            rejected.stderr,
        )
        self.assertNotIn("AWS_CALL:", rejected.stderr)

        for mismatched_tags in (
            {
                "Tags": {
                    "App": "other-app",
                    "Stage": "staging",
                    "ManagedBy": "aws-cli",
                }
            },
            {
                "Tags": {
                    "App": APP_NAME,
                    "Stage": "prod",
                    "ManagedBy": "aws-cli",
                }
            },
        ):
            with self.subTest(tags=mismatched_tags):
                mismatch = self._run_lambda_tag_helper(
                    function_name,
                    mismatched_tags,
                )
                self.assertNotEqual(mismatch.returncode, 0)
                self.assertNotIn("AWS_CALL:lambda tag-resource", mismatch.stderr)

    def test_all_shared_tag_resource_maps_use_json(self):
        helper = self.sources["jm8_resource_tags.sh"]
        self.assertEqual(
            re.findall(r"aws ([a-z0-9-]+) tag-resource", helper),
            ["apigatewayv2", "cognito-idp", "lambda"],
        )
        self.assertEqual(
            helper.count(
                '"{\\"App\\":\\"${APP_NAME}\\",'
                '\\"Stage\\":\\"${STAGE}\\",'
                '\\"ManagedBy\\":\\"aws-cli\\"}"'
            ),
            3,
        )

    def test_map_valued_api_tags_remain_distinct_from_list_interfaces(self):
        helper = self.sources["jm8_resource_tags.sh"]
        self.assertNotIn("aws stepfunctions", helper)
        self.assertNotIn("aws iam", helper)

        create_api = self.sources["create-api"]
        self.assertIn(
            '--tags \\\n'
            '      "{\\"App\\":\\"${APP_NAME}\\",'
            '\\"Stage\\":\\"${STAGE}\\",'
            '\\"ManagedBy\\":\\"aws-cli\\"}" \\',
            create_api,
        )

        historical = self.sources["deploy-historical-reanalysis-workflow"]
        state_machine_create = historical[
            historical.index("aws stepfunctions create-state-machine"):
            historical.index('--query "stateMachineArn"')
        ]
        self.assertIn(
            '--tags \\\n'
            '        "key=App,value=${APP_NAME}" \\\n'
            '        "key=Stage,value=${STAGE}" \\\n'
            '        "key=ManagedBy,value=aws-cli" \\',
            state_machine_create,
        )

        cloudformation = (
            BIN_DIR / "create-frontend-hosting"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '--tags "App=$APP_NAME" "Stage=$STAGE" '
            '"ManagedBy=aws-cli"',
            cloudformation,
        )

        dynamodb = (BIN_DIR / "create-resources").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '--tags \\\n'
            '      Key=App,Value="$APP_NAME" \\\n'
            '      Key=Stage,Value="$STAGE" \\\n'
            '      Key=ManagedBy,Value=aws-cli \\',
            dynamodb,
        )

        secrets_manager = (BIN_DIR / "provision-stripe-secret").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '--tags \\\n'
            '        "Key=App,Value=${APP_NAME}" \\\n'
            '        "Key=Stage,Value=${STAGE}" \\\n'
            '        "Key=ManagedBy,Value=aws-cli" \\',
            secrets_manager,
        )

    def test_create_auth_has_safe_shell_argument_boundaries(self):
        source = self.sources["create-auth"]
        concatenated_option = re.compile(
            r'''(?:"[^"\n]*"|'[^'\n]*'|\$\{[A-Za-z_][A-Za-z0-9_]*\}|'''
            r'''\$[A-Za-z_][A-Za-z0-9_]*)(--[a-z][a-z0-9-]*)'''
        )
        self.assertEqual(concatenated_option.findall(source), [])
        for forbidden in ("|| true", "eval ", "set -x"):
            self.assertNotIn(forbidden, source)

    def test_create_auth_requires_final_app_client_verification(self):
        source = self.sources["create-auth"]
        client_branch_end = source.index(
            'fi\n\necho "Checking Cognito Hosted UI domain'
        )
        verifier_index = source.index(
            'python3 "$COGNITO_BRANDING_HELPER" apply'
        )
        environment_write_index = source.index('COGNITO_ENV_TEMP="$(mktemp')
        success_index = source.index('echo "Cognito auth foundation ready:"')

        self.assertGreater(verifier_index, client_branch_end)
        self.assertLess(verifier_index, environment_write_index)
        self.assertLess(verifier_index, success_index)
        self.assertIn('--user-pool-id "$USER_POOL_ID"', source)
        self.assertIn('--app-client-id "$APP_CLIENT_ID"', source)
        helper = (BIN_DIR / "jm8_cognito_branding.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"describe-user-pool-client"', helper)
        self.assertIn("validate_app_client(", helper)
        self.assertIn("verify_aws_identity_and_target(aws, target)", helper)

    def test_app_client_verifier_accepts_only_the_exact_contract(self):
        client_id = "a" * 26
        client_name = "journalm8-prod-web"
        callback_url = "https://prod.example.com/callback"
        logout_url = "https://prod.example.com"
        target = BrandingTarget(
            boundary=BrandingBoundary(
                app_name="journalm8",
                stage="prod",
                aws_profile="jm8-prod",
                account_id=ACCOUNT_ID,
                region=REGION,
                deploy_confirmation="prod",
                production_isolation_mode="stage-scoped-same-account",
            ),
            user_pool_id="us-east-1_Production123",
            app_client_id=client_id,
            callback_url=callback_url,
            logout_url=logout_url,
        )
        expected_client = {
            "UserPoolId": target.user_pool_id,
            "ClientId": client_id,
            "ClientName": client_name,
            "AllowedOAuthFlowsUserPoolClient": True,
            "AllowedOAuthFlows": ["code"],
            "AllowedOAuthScopes": ["openid", "email", "profile"],
            "CallbackURLs": [callback_url],
            "LogoutURLs": [logout_url],
            "SupportedIdentityProviders": ["COGNITO"],
        }

        validate_app_client({"UserPoolClient": dict(expected_client)}, target)

        explicit_false_client = dict(expected_client)
        explicit_false_client["GenerateSecret"] = False
        validate_app_client(
            {"UserPoolClient": explicit_false_client}, target
        )

        for invalid_generate_secret in (True, None, 0, 1, "false", {}, []):
            with self.subTest(generate_secret=invalid_generate_secret):
                client = dict(expected_client)
                client["GenerateSecret"] = invalid_generate_secret
                with self.assertRaises(CognitoBrandingError):
                    validate_app_client({"UserPoolClient": client}, target)

        mismatches = {
            "ClientId": "other-client-id",
            "ClientName": "journalm8-staging-web",
            "AllowedOAuthFlowsUserPoolClient": False,
            "AllowedOAuthFlows": ["implicit"],
            "AllowedOAuthScopes": ["openid", "email"],
            "CallbackURLs": ["https://staging.example.com/callback"],
            "LogoutURLs": ["https://staging.example.com"],
            "SupportedIdentityProviders": ["Google"],
        }
        for field, mismatched_value in mismatches.items():
            with self.subTest(field=field):
                client = dict(expected_client)
                client[field] = mismatched_value
                with self.assertRaises(CognitoBrandingError) as rejected:
                    validate_app_client({"UserPoolClient": client}, target)
                self.assertNotIn(
                    mismatched_value.__repr__(), str(rejected.exception)
                )

        with self.assertRaises(CognitoBrandingError):
            validate_app_client("not-json", target)

    def test_api_reuse_reconciles_before_any_update(self):
        source = self.sources["create-api"]
        self.assertLess(
            source.index("jm8_reconcile_http_api_tags"),
            source.index("aws apigatewayv2 update-api"),
        )
        helper = self.sources["jm8_resource_tags.sh"]
        self.assertLess(
            helper.index("verify-tags-before-reconcile"),
            helper.index("aws apigatewayv2 tag-resource"),
        )
        self.assertGreaterEqual(helper.count("aws apigatewayv2 get-tags"), 2)

    def test_production_api_helpers_use_only_get_api_embedded_tags(self):
        api_document = {
            "ApiId": "j56qvbzzpe",
            "Name": "journalm8-prod-api",
            "ProtocolType": "HTTP",
            "Tags": {
                "App": APP_NAME,
                "Stage": "prod",
                "ManagedBy": "aws-cli",
            },
        }
        for function_name in (
            "jm8_verify_http_api_tags",
            "jm8_reconcile_http_api_tags",
        ):
            with self.subTest(function=function_name):
                result = self._run_api_tag_helper(
                    function_name,
                    "prod",
                    api_document,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    re.findall(r"AWS_CALL:apigatewayv2 ([a-z-]+)", result.stderr),
                    ["get-api"],
                )
                self.assertNotIn("get-tags", result.stderr)
                self.assertNotIn("tag-resource", result.stderr)

    def test_production_api_embedded_tag_mismatches_fail_without_retagging(self):
        canonical = {
            "ApiId": "j56qvbzzpe",
            "Name": "journalm8-prod-api",
            "ProtocolType": "HTTP",
            "Tags": {
                "App": APP_NAME,
                "Stage": "prod",
                "ManagedBy": "aws-cli",
            },
        }
        invalid_tag_maps = (
            None,
            [],
            {},
            {"App": APP_NAME, "Stage": "prod"},
            {**canonical["Tags"], "Unrelated": "rejected"},
            {**canonical["Tags"], "App": "other"},
            {**canonical["Tags"], "Stage": "dev"},
            {**canonical["Tags"], "Stage": "staging"},
            {**canonical["Tags"], "ManagedBy": "console"},
        )
        for tags in invalid_tag_maps:
            with self.subTest(tags=tags):
                response = dict(canonical)
                if tags is None:
                    response.pop("Tags")
                else:
                    response["Tags"] = tags
                result = self._run_api_tag_helper(
                    "jm8_reconcile_http_api_tags",
                    "prod",
                    response,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    re.findall(r"AWS_CALL:apigatewayv2 ([a-z-]+)", result.stderr),
                    ["get-api"],
                )
                self.assertNotIn("get-tags", result.stderr)
                self.assertNotIn("tag-resource", result.stderr)

    def test_production_api_identity_is_validated_before_embedded_tags(self):
        for api_document in (
            {
                "ApiId": "j56qvbzzpe",
                "Name": "journalm8-staging-api",
                "ProtocolType": "HTTP",
                "Tags": [],
            },
            {
                "ApiId": "j56qvbzzpe",
                "Name": "journalm8-prod-api",
                "ProtocolType": "WEBSOCKET",
                "Tags": {},
            },
        ):
            with self.subTest(api_document=api_document):
                result = self._run_api_tag_helper(
                    "jm8_verify_http_api_tags",
                    "prod",
                    api_document,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("HTTP API identity does not match", result.stderr)
                self.assertNotIn("AWS tag response is malformed", result.stderr)
                self.assertEqual(
                    re.findall(r"AWS_CALL:apigatewayv2 ([a-z-]+)", result.stderr),
                    ["get-api"],
                )

    def test_dev_and_staging_api_reconciliation_remains_idempotent(self):
        for stage, api_id in (("dev", "dev1234567"), ("staging", "stage12345")):
            with self.subTest(stage=stage):
                tags = {
                    "Tags": {
                        "App": APP_NAME,
                        "Stage": stage,
                        "ManagedBy": "aws-cli",
                        "Unrelated": "preserved",
                    }
                }
                result = self._run_api_tag_helper(
                    "jm8_reconcile_http_api_tags",
                    stage,
                    {
                        "ApiId": api_id,
                        "Name": f"{APP_NAME}-{stage}-api",
                        "ProtocolType": "HTTP",
                    },
                    tags,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    re.findall(r"AWS_CALL:apigatewayv2 ([a-z-]+)", result.stderr),
                    [
                        "get-api",
                        "get-tags",
                        "tag-resource",
                        "get-api",
                        "get-tags",
                    ],
                )

    def test_cognito_reuse_reconciles_before_child_resources(self):
        source = self.sources["create-auth"]
        reconcile_index = source.index("jm8_reconcile_cognito_user_pool_tags")
        self.assertLess(
            reconcile_index, source.index("aws cognito-idp list-user-pool-clients")
        )
        self.assertLess(
            reconcile_index, source.index("aws cognito-idp create-user-pool-domain")
        )
        client_block = source[
            source.index("aws cognito-idp create-user-pool-client"):
            source.index("else\n  echo \"App client already exists")
        ]
        domain_block = source[
            source.index("aws cognito-idp create-user-pool-domain"):
            source.index('fi\n\npython3 "$COGNITO_BRANDING_HELPER" apply')
        ]
        self.assertNotIn("--tags", client_block)
        self.assertNotIn("--tags", domain_block)

    def test_lambda_reuse_reconciles_before_updates(self):
        deploy = self.sources["deploy"]
        self.assertLess(
            deploy.index('jm8_reconcile_lambda_tags "$FUNCTION_NAME"'),
            deploy.index("aws lambda update-function-code"),
        )
        ocr = self.sources["deploy-ocr-workflow"]
        self.assertLess(
            ocr.index('jm8_reconcile_lambda_tags "$WORKER_FUNCTION_NAME"'),
            ocr.index("aws lambda update-function-code"),
        )
        historical = self.sources["deploy-historical-reanalysis-workflow"]
        self.assertLess(
            historical.index('jm8_reconcile_lambda_tags "$function_name"'),
            historical.index("aws lambda update-function-code"),
        )

    def test_lambda_create_paths_reconcile_and_verify_after_creation(self):
        helper = self.sources["jm8_resource_tags.sh"]
        self.assertIn("aws lambda tag-resource", helper)
        self.assertGreaterEqual(helper.count("aws lambda list-tags"), 2)
        creation_paths = (
            ("deploy", "$FUNCTION_NAME"),
            ("deploy-ocr-workflow", "$WORKER_FUNCTION_NAME"),
            ("deploy-ocr-workflow", "$FAILURE_FUNCTION_NAME"),
            ("deploy-historical-reanalysis-workflow", "$function_name"),
        )
        search_offsets = {}
        for name, function_name in creation_paths:
            source = self.sources[name]
            create_index = source.index(
                "aws lambda create-function",
                search_offsets.get(name, 0),
            )
            reconcile = f'jm8_reconcile_lambda_tags "{function_name}"'
            reconcile_index = source.index(reconcile, create_index)
            self.assertGreater(reconcile_index, create_index)
            search_offsets[name] = create_index + 1

        historical = self.sources["deploy-historical-reanalysis-workflow"]
        for function_name in (
            "$WORKER_FUNCTION_NAME",
            "$COORDINATOR_FUNCTION_NAME",
        ):
            with self.subTest(historical_function=function_name):
                self.assertRegex(
                    historical,
                    rf'ensure_lambda_function \\\n\s+"{re.escape(function_name)}"',
                )

    def test_lambda_create_tags_are_one_exact_json_shell_argument(self):
        expected_command_counts = {
            "deploy": 1,
            "deploy-ocr-workflow": 2,
            "deploy-historical-reanalysis-workflow": 1,
        }
        for name, expected_count in expected_command_counts.items():
            blocks = self._lambda_create_blocks(self.sources[name])
            self.assertEqual(len(blocks), expected_count, name)
            for command_number, block in enumerate(blocks, start=1):
                with self.subTest(script=name, command=command_number):
                    match = re.search(
                        r'--tags \\\n\s+"((?:\\.|[^"\\])*)" \\',
                        block,
                    )
                    self.assertIsNotNone(match)
                    self.assertEqual(block.count("--tags"), 1)
                    json_template = match.group(1).replace(r'\"', '"')
                    for stage in ("dev", "staging", "prod"):
                        expanded = json_template.replace(
                            "${APP_NAME}", APP_NAME
                        ).replace("${STAGE}", stage)
                        self.assertEqual(
                            json.loads(expanded),
                            {
                                "App": APP_NAME,
                                "Stage": stage,
                                "ManagedBy": "aws-cli",
                            },
                        )

                    self.assertNotIn(
                        "App=${APP_NAME},Stage=${STAGE},ManagedBy=aws-cli",
                        block,
                    )
                    for separate_tag in (
                        '"App=${APP_NAME}"',
                        '"Stage=${STAGE}"',
                        '"ManagedBy=aws-cli"',
                    ):
                        self.assertNotIn(separate_tag, block)

    def test_lambda_create_paths_cover_all_five_functions(self):
        deploy = self.sources["deploy"]
        self.assertIn(
            'FUNCTION_NAME="${APP_NAME}-${STAGE}-api"',
            deploy,
        )
        self.assertIn('--function-name "$FUNCTION_NAME"', deploy)

        ocr = self.sources["deploy-ocr-workflow"]
        ocr_blocks = self._lambda_create_blocks(ocr)
        self.assertEqual(
            {
                re.search(r'--function-name "([^"]+)"', block).group(1)
                for block in ocr_blocks
            },
            {"$WORKER_FUNCTION_NAME", "$FAILURE_FUNCTION_NAME"},
        )

        historical = self.sources["deploy-historical-reanalysis-workflow"]
        self.assertIn('--function-name "$function_name"', historical)
        self.assertIn(
            'ensure_lambda_function \\\n  "$WORKER_FUNCTION_NAME"',
            historical,
        )
        self.assertIn(
            'ensure_lambda_function \\\n  "$COORDINATOR_FUNCTION_NAME"',
            historical,
        )

    def test_lambda_deployment_scripts_keep_strict_shell_behavior(self):
        concatenated_option = re.compile(
            r'''(?:"[^"\n]*"|'[^'\n]*'|\$\{[A-Za-z_][A-Za-z0-9_]*\}|'''
            r'''\$[A-Za-z_][A-Za-z0-9_]*)(--[a-z][a-z0-9-]*)'''
        )
        for name in (
            "deploy",
            "deploy-ocr-workflow",
            "deploy-historical-reanalysis-workflow",
        ):
            source = self.sources[name]
            with self.subTest(script=name):
                self.assertEqual(concatenated_option.findall(source), [])
                for forbidden in ("|| true", "eval ", "set -x"):
                    self.assertNotIn(forbidden, source)

    def test_downstream_api_paths_verify_tags(self):
        self.assertIn(
            "jm8_verify_http_api_tags",
            self.sources["secure-api"],
        )
        self.assertIn(
            "jm8_verify_http_api_tags",
            self.sources["deploy-observability"],
        )

    def test_secure_api_rejects_cross_stage_production_selection(self):
        source = self.sources["secure-api"]
        self.assertIn('[ "${STAGE:-}" = "prod" ]', source)
        self.assertIn('[ "$AWS_PROFILE" = "jm8-prod" ]', source)
        self.assertIn('[ "$API_NAME" != "journalm8-prod-api" ]', source)
        production_guard = source.index("jm8_validate_contract_or_exit")
        production_resolution = source.index(
            'API_ID="$(jm8_resolve_http_api_id "$API_NAME")"'
        )
        self.assertLess(production_guard, production_resolution)

    def test_helper_does_not_suppress_tag_lookup_failures(self):
        helper = self.sources["jm8_resource_tags.sh"]
        for forbidden in ("|| true", "eval ", "set -x", "2>/dev/null"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, helper)
        for lookup in (
            "aws apigatewayv2 get-tags",
            "aws cognito-idp list-tags-for-resource",
            "aws lambda list-tags",
        ):
            self.assertIn(lookup, helper)

    def test_frontend_production_enablement_uses_cloudformation_tags(self):
        for name in ("create-frontend-hosting", "deploy-frontend"):
            source = (BIN_DIR / name).read_text(encoding="utf-8")
            self.assertIn("staging|prod", source)
            self.assertNotIn("jm8_resource_tags.sh", source)
        create_source = (BIN_DIR / "create-frontend-hosting").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '--tags "App=$APP_NAME" "Stage=$STAGE" "ManagedBy=aws-cli"',
            create_source,
        )

    def test_tag_helpers_contain_no_secret_material_or_secret_output(self):
        combined = self.sources["jm8_resource_tags.sh"] + self.sources[
            "jm8_resource_tag_contract.py"
        ]
        for forbidden in (
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "SecretString",
        ):
            self.assertNotIn(forbidden, combined)


class ProductionTagPolicyTests(unittest.TestCase):
    def test_policy_allows_only_safe_tag_reconciliation_surfaces(self):
        policies = generate_policies(REGION)
        foundation = policies["journalm8-prod-deployer-foundation"]["Statement"]
        compute = policies["journalm8-prod-deployer-compute"]["Statement"]

        cognito_tag = next(
            statement
            for statement in foundation
            if statement["Sid"] == "TagVerifiedProductionUserPool"
        )
        self.assertEqual(cognito_tag["Action"], ["cognito-idp:TagResource"])
        self.assertEqual(
            cognito_tag["Condition"]["StringEquals"]["aws:ResourceTag/Stage"],
            "prod",
        )

        api_manage = next(
            statement
            for statement in compute
            if statement["Sid"] == "ManageTaggedProductionHttpApi"
        )
        self.assertIn("apigateway:PATCH", api_manage["Action"])
        self.assertEqual(
            api_manage["Condition"]["StringEquals"]["aws:ResourceTag/Stage"],
            "prod",
        )
        statement_ids = {statement["Sid"] for statement in compute}
        self.assertNotIn("TagExactProductionHttpApi", statement_ids)
        self.assertNotIn(
            "InspectExactProductionHttpApiForTagBootstrap",
            statement_ids,
        )

    def test_policy_adds_tag_reads_without_wildcard_actions(self):
        serialized = json.dumps(generate_policies(REGION), sort_keys=True)
        for action in (
            "cognito-idp:DescribeUserPool",
            "cognito-idp:ListTagsForResource",
            "lambda:ListTags",
        ):
            self.assertIn(action, serialized)
        self.assertNotIn('"Action": "*"', serialized)
        self.assertNotIn('"Action": ["*"]', serialized)
        self.assertNotIn('cognito-idp:*', serialized)
        self.assertNotIn('lambda:*', serialized)


if __name__ == "__main__":
    unittest.main()
