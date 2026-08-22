from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))

from jm8_production_deployer_policies import generate_policies  # noqa: E402
from jm8_resource_tag_contract import (  # noqa: E402
    TagContractError,
    _context,
    resolve_api,
    resolve_user_pool,
    validate_api,
    validate_lambda,
    validate_user_pool,
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
            {"ApiId": "prod123456", "Name": "journalm8-prod-api"},
            "prod123456",
            "journalm8-prod-api",
        )
        with self.assertRaisesRegex(TagContractError, "does not match"):
            validate_api(
                {"ApiId": "prod123456", "Name": "journalm8-staging-api"},
                "prod123456",
                "journalm8-prod-api",
            )

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

    def test_production_create_commands_supply_canonical_tags(self):
        create_auth = self.sources["create-auth"]
        create_api = self.sources["create-api"]
        self.assertIn("--user-pool-tags", create_auth)
        self.assertIn("aws cognito-idp create-user-pool", create_auth)
        self.assertIn("aws apigatewayv2 create-api", create_api)
        for source in (create_auth, create_api):
            self.assertIn('"App=${APP_NAME}"', source)
            self.assertIn('"Stage=${STAGE}"', source)
            self.assertIn('"ManagedBy=aws-cli"', source)

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

    def test_cognito_reuse_reconciles_before_child_resources(self):
        source = self.sources["create-auth"]
        self.assertLess(
            source.index("jm8_reconcile_cognito_user_pool_tags"),
            source.index("aws cognito-idp list-user-pool-clients"),
        )
        client_block = source[
            source.index("aws cognito-idp create-user-pool-client"):
            source.index("else\n  echo \"App client already exists")
        ]
        domain_block = source[
            source.index("aws cognito-idp create-user-pool-domain"):
            source.index("fi\n\nCOGNITO_ISSUER")
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
        for name in (
            "deploy",
            "deploy-ocr-workflow",
            "deploy-historical-reanalysis-workflow",
        ):
            with self.subTest(script=name):
                source = self.sources[name]
                self.assertIn("aws lambda create-function", source)
                self.assertIn("jm8_reconcile_lambda_tags", source)

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

    def test_no_frontend_production_enablement_was_added(self):
        for name in ("create-frontend-hosting", "deploy-frontend"):
            source = (BIN_DIR / name).read_text(encoding="utf-8")
            self.assertIn("staging", source)
            self.assertNotIn("jm8_resource_tags.sh", source)

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
