import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "infra" / "cloudfront" / "frontend-hosting.yaml"
CREATE_SCRIPT = REPO_ROOT / "bin" / "create-frontend-hosting"
DIAGNOSTICS_HELPER = REPO_ROOT / "bin" / "jm8_frontend_hosting_diagnostics.py"
DEPLOY_SCRIPT = REPO_ROOT / "bin" / "deploy-frontend"
AMPLIFY_FILE = REPO_ROOT.parent / "amplify.yml"
LEGACY_TEST_FILE = REPO_ROOT / "tests" / "test_amplify_configuration.py"


class FrontendHostingWiringTests(unittest.TestCase):
    def setUp(self):
        self.template = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.create_script = CREATE_SCRIPT.read_text(encoding="utf-8")
        self.diagnostics_helper = DIAGNOSTICS_HELPER.read_text(encoding="utf-8")
        self.deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    def test_diagnostics_are_timestamped_and_fail_closed(self):
        for phase in (
            "Environment contract validation started",
            "Environment contract validation completed",
            "Local template preflight started",
            "Local template preflight completed",
            "CloudFormation deploy started",
            "CloudFormation deploy completed",
            "Resulting stack status",
            "Output validation started",
            "Output validation completed",
            "S3 verification started",
            "S3 verification completed",
            "CloudFront/OAC verification started",
            "CloudFront/OAC verification completed",
        ):
            self.assertIn(phase, self.create_script)
        self.assertIn("log_info", self.create_script)
        self.assertIn("log_warn", self.create_script)
        self.assertIn("log_error", self.create_script)
        self.assertNotIn("set -x", self.create_script)
        self.assertNotIn("|| true", self.create_script)

    def test_diagnostic_template_and_stack_parsing_is_deterministic(self):
        self.assertIn("sha256_hex", self.create_script)
        self.assertIn("get-template", self.create_script)
        self.assertIn("template-check", self.create_script)
        self.assertIn("OriginAccessControlId", self.diagnostics_helper)
        self.assertIn("DEPLOYED_TEMPLATE_OUTPUT_MISSING", self.diagnostics_helper)
        self.assertIn("DEPLOYED_TEMPLATE_OUTPUT_INVALID", self.diagnostics_helper)
        self.assertIn("malformed OAC ID", self.diagnostics_helper)
        self.assertIn("output_keys", self.diagnostics_helper)

    def test_diagnostic_failure_correlation_and_report_are_sanitized(self):
        self.assertIn("list-stack-resources", self.create_script)
        self.assertIn("FrontendOriginAccessControl", self.create_script)
        self.assertIn("PhysicalResourceId", self.diagnostics_helper)
        self.assertIn("do not substitute a physical ID", self.create_script)
        self.assertIn("describe-stack-events", self.create_script)
        self.assertIn("events", self.create_script)
        self.assertIn(".build/diagnostics", self.create_script)
        self.assertIn("write-report", self.create_script)
        self.assertIn("rm -rf \"$TEMP_DIR\"", self.create_script)
        self.assertNotIn("echo \"$STAGING_BASIC_AUTH_PASSWORD\"", self.create_script)
        self.assertNotIn("log_info \"$AUTH_DIGEST\"", self.create_script)
        self.assertNotIn("printenv", self.create_script)
        self.assertNotIn("env |", self.create_script)

    def test_no_s3_website_hosting(self):
        self.assertNotIn("WebsiteConfiguration", self.template)
        self.assertNotIn("S3Website", self.template)
        self.assertNotIn("WebsiteEndpoint", self.template)

    def test_bucket_and_raw_bucket_are_separate(self):
        self.assertIn("FrontendBucketName", self.template)
        self.assertIn("FRONTEND_BUCKET", self.create_script)
        self.assertIn("FRONTEND_BUCKET", self.deploy_script)
        self.assertIn("RAW_BUCKET", self.create_script)
        self.assertIn("RAW_BUCKET", self.deploy_script)

    def test_full_block_public_access_and_bucket_hardening(self):
        self.assertIn("BlockPublicAcls: true", self.template)
        self.assertIn("IgnorePublicAcls: true", self.template)
        self.assertIn("BlockPublicPolicy: true", self.template)
        self.assertIn("RestrictPublicBuckets: true", self.template)
        self.assertIn("BucketOwnerEnforced", self.template)
        self.assertIn("AES256", self.template)
        self.assertIn("Status: Enabled", self.template)
        self.assertIn("NoncurrentVersionExpirationInDays: 30", self.template)
        self.assertIn("DaysAfterInitiation: 7", self.template)
        self.assertIn("DataClassification", self.template)

    def test_oac_and_private_policy_requirements(self):
        self.assertIn("OriginAccessControlOriginType: s3", self.template)
        self.assertIn("SigningBehavior: always", self.template)
        self.assertIn("SigningProtocol: sigv4", self.template)
        self.assertIn("Service: cloudfront.amazonaws.com", self.template)
        self.assertIn("AWS:SourceArn", self.template)
        self.assertIn("aws:SecureTransport", self.template)
        self.assertNotIn("Principal: '*'", self.template)
        self.assertNotIn("PublicAccessBlockConfiguration", self.template.replace("PublicAccessBlockConfiguration", ""))

    def test_oac_output_is_defined_and_validated_by_create_script(self):
        self.assertIn("OriginAccessControlId:", self.template)
        self.assertIn("Description: CloudFront Origin Access Control identifier.", self.template)
        self.assertIn("Value: !GetAtt FrontendOriginAccessControl.Id", self.template)
        self.assertIn('require_output "OriginAccessControlId"', self.create_script)
        self.assertIn("OriginAccessControlId", self.create_script)

    def test_distribution_security_and_spa_behavior_with_deferred_custom_domain_tls(self):
        self.assertIn("redirect-to-https", self.template)
        # Explicit minimum TLS enforcement is deferred until a custom domain
        # and ACM certificate are configured; the temporary cloudfront.net
        # hostname uses CloudFrontDefaultCertificate and its AWS-managed policy.
        self.assertIn("CloudFrontDefaultCertificate: true", self.template)
        self.assertNotIn("MinimumProtocolVersion", self.template)
        self.assertIn("CachePolicyId: 658327ea-f89d-4fab-a63d-7e88639e58f6", self.template)
        self.assertIn("403", self.template)
        self.assertIn("404", self.template)
        self.assertIn("/index.html", self.template)
        self.assertIn("ErrorCachingMinTTL: 0", self.template)
        self.assertIn("DefaultRootObject: index.html", self.template)

    def test_cloudfront_function_basic_auth_is_staging_only(self):
        self.assertIn("IsStaging: !Equals [!Ref Stage, staging]", self.template)
        self.assertIn("Condition: IsStaging", self.template)
        self.assertIn("viewer-request", self.template)
        self.assertIn("FunctionAssociations", self.template)
        self.assertIn("FunctionAssociations: !If", self.template)
        self.assertIn("!Ref AWS::NoValue", self.template)
        self.assertIn("FunctionConfig:", self.template)
        self.assertIn("Comment: JM8 staging basic authentication", self.template)
        self.assertIn("Runtime: cloudfront-js-2.0", self.template)
        self.assertIn("AutoPublish: true", self.template)
        self.assertIn("401", self.template)
        self.assertIn('Basic realm="JM8 Staging"', self.template)
        self.assertIn("cache-control", self.template)
        self.assertIn("require('crypto')", self.template)
        self.assertIn("createHash('sha256')", self.template)
        self.assertNotIn("TextEncoder", self.template)
        self.assertNotIn("crypto.subtle", self.template)
        self.assertIn("AuthorizationDigest", self.template)

    def test_only_digest_is_passed_to_cloudformation(self):
        self.assertIn("AuthorizationDigest", self.create_script)
        self.assertIn("shasum -a 256", self.create_script)
        self.assertIn("sha256sum", self.create_script)
        self.assertIn("neither shasum nor sha256sum is available", self.create_script)
        self.assertIn("Basic ${AUTH_HEADER}", self.create_script)
        self.assertIn("unset AUTH_HEADER", self.create_script)
        self.assertNotIn("STAGING_BASIC_AUTH_USERNAME=", self.create_script)
        self.assertNotIn("STAGING_BASIC_AUTH_PASSWORD=", self.create_script)
        self.assertNotIn("Authorization: Basic", self.create_script)
        self.assertNotIn("curl -u", self.deploy_script)
        self.assertNotIn("-u \"${STAGING_BASIC_AUTH_USERNAME}:${STAGING_BASIC_AUTH_PASSWORD}\"", self.deploy_script)
        self.assertNotIn("-H \"Authorization: Basic", self.deploy_script)
        self.assertIn("curl --config -", self.deploy_script)
        self.assertIn("auth_header=\"Authorization: Basic", self.deploy_script)
        self.assertIn('if [[ "$STAGE" == "staging" ]]', self.create_script)
        self.assertIn('AUTH_DIGEST=""', self.create_script)
        self.assertIn('PARAMETER_OVERRIDES+=("AuthorizationDigest=$AUTH_DIGEST")', self.create_script)

    def test_create_post_deploy_state_and_output_validation(self):
        self.assertIn("CREATE_COMPLETE|UPDATE_COMPLETE", self.create_script)
        self.assertIn("require_output", self.create_script)
        self.assertIn('""|None|null', self.create_script)
        self.assertIn('BUCKET_NAME" != "$FRONTEND_BUCKET', self.create_script)
        self.assertIn('DISTRIBUTION_DOMAIN" != *.cloudfront.net', self.create_script)
        self.assertNotIn("stack-create-complete", self.create_script)
        self.assertNotIn("stack-update-complete", self.create_script)
        self.assertIn("stack-contract-verify", self.create_script)
        self.assertIn('--tags "App=$APP_NAME" "Stage=$STAGE" "ManagedBy=aws-cli"', self.create_script)

    def test_create_verifies_actual_s3_settings(self):
        self.assertIn("BlockPublicAcls", self.create_script)
        self.assertIn("IgnorePublicAcls", self.create_script)
        self.assertIn("BlockPublicPolicy", self.create_script)
        self.assertIn("RestrictPublicBuckets", self.create_script)
        self.assertIn('!= "True"', self.create_script)
        self.assertIn('!= "AES256"', self.create_script)
        self.assertIn('!= "BucketOwnerEnforced"', self.create_script)
        self.assertIn('!= "Enabled"', self.create_script)
        self.assertIn("get-bucket-tagging", self.create_script)
        self.assertIn("get-bucket-policy", self.create_script)
        self.assertIn("bucket-policy-verify", self.create_script)

    def test_website_check_accepts_only_missing_configuration(self):
        self.assertIn("WEBSITE_ERROR_FILE", self.create_script)
        self.assertIn("NoSuchWebsiteConfiguration", self.create_script)
        self.assertIn("cat \"$WEBSITE_ERROR_FILE\" >&2", self.create_script)
        self.assertNotIn("get-bucket-website.*|| true", self.create_script)

    def test_create_inspects_exact_distribution_oac(self):
        self.assertIn("FrontendBucketRegionalDomainName", self.create_script)
        self.assertIn("ORIGIN_MATCH_COUNT", self.create_script)
        self.assertIn("ORIGIN_OAC_ID", self.create_script)
        self.assertIn("get-origin-access-control", self.create_script)
        self.assertIn("OAC_ORIGIN_TYPE", self.create_script)
        self.assertIn("OAC_SIGNING_BEHAVIOR", self.create_script)
        self.assertIn("OAC_SIGNING_PROTOCOL", self.create_script)
        self.assertNotIn("list-origin-access-controls", self.create_script)
        self.assertNotIn("Items[0]", self.create_script)

    def test_create_verifies_cloudfront_postconditions(self):
        self.assertIn("Distribution.Status", self.create_script)
        self.assertIn("Deployed", self.create_script)
        self.assertIn("Distribution.DistributionConfig.Enabled", self.create_script)
        self.assertIn("DefaultRootObject", self.create_script)
        self.assertIn("ViewerProtocolPolicy", self.create_script)
        self.assertIn("CachePolicyId", self.create_script)
        self.assertIn("viewer-request", self.create_script)
        self.assertIn("CustomErrorResponses", self.template)
        self.assertIn("ERROR_CODE", self.create_script)
        self.assertIn("/index.html", self.create_script)
        self.assertIn("list-tags-for-resource", self.create_script)
        self.assertIn('"$STAGE"', self.create_script)

    def test_create_script_has_no_upload_behavior(self):
        self.assertNotIn("aws s3 sync", self.create_script)
        self.assertNotIn("npm run build:staging", self.create_script)
        self.assertNotIn("create-invalidation", self.create_script)
        self.assertNotIn("curl", self.create_script)

    def test_deploy_script_has_no_infrastructure_creation(self):
        self.assertNotIn("aws cloudformation create-stack", self.deploy_script)
        self.assertNotIn("aws cloudformation deploy", self.deploy_script)
        self.assertNotIn("stack-create-complete", self.deploy_script)

    def test_build_order_and_sync_behavior(self):
        self.assertLess(self.deploy_script.index("npm ci"), self.deploy_script.index("npm run test"))
        self.assertLess(self.deploy_script.index("npm run test"), self.deploy_script.index("npm run build:staging"))
        self.assertIn("npm run build:staging", self.deploy_script)
        self.assertIn("npm run build:production", self.deploy_script)
        self.assertIn(".env.production.local", self.deploy_script)
        self.assertIn("node scripts/validate_production_build.mjs dist", self.deploy_script)
        self.assertIn("aws s3 sync dist/ \"s3://${FRONTEND_BUCKET}\"", self.deploy_script)
        self.assertIn("--delete", self.deploy_script)
        self.assertIn("--cache-control \"no-cache\"", self.deploy_script)
        self.assertIn("no-cache,no-store,must-revalidate", self.deploy_script)
        self.assertIn("public,max-age=31536000,immutable", self.deploy_script)
        self.assertIn("aws s3 cp dist/assets/ \"s3://${FRONTEND_BUCKET}/assets/\"", self.deploy_script)
        self.assertNotIn("--exclude \"index.html\" --cache-control \"public,max-age=31536000,immutable\"", self.deploy_script)

    def test_invalidation_and_health_checks_exist(self):
        self.assertIn("create-invalidation", self.deploy_script)
        self.assertIn("aws cloudfront wait invalidation-completed", self.deploy_script)
        self.assertNotIn("invalidation-deployed", self.deploy_script)
        self.assertIn('--distribution-id "$CLOUDFRONT_DISTRIBUTION_ID"', self.deploy_script)
        self.assertIn('--id "$INVALIDATION_ID"', self.deploy_script)
        self.assertIn("aws cloudfront get-invalidation", self.deploy_script)
        self.assertIn('"$INVALIDATION_STATUS" != "Completed"', self.deploy_script)
        self.assertIn("curl", self.deploy_script)
        self.assertIn("mktemp -d", self.deploy_script)
        self.assertIn("trap cleanup EXIT", self.deploy_script)
        self.assertIn("401", self.deploy_script)
        self.assertIn("200", self.deploy_script)
        self.assertIn("/archive", self.deploy_script)

    def test_deploy_script_root_resolves_to_repository(self):
        self.assertIn('REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"', self.deploy_script)
        self.assertIn('cd "$REPO_ROOT/frontend"', self.deploy_script)

    def test_source_map_deletion_is_not_suppressed(self):
        self.assertIn("find dist -type f -name '*.map' -delete", self.deploy_script)
        self.assertNotIn("find dist -name '*.map' -delete || true", self.deploy_script)

    def test_immutable_cache_scope_is_assets_only(self):
        self.assertIn("aws s3 sync dist/ \"s3://${FRONTEND_BUCKET}\"", self.deploy_script)
        self.assertIn("--cache-control \"no-cache\"", self.deploy_script)
        self.assertIn("aws s3 cp dist/assets/ \"s3://${FRONTEND_BUCKET}/assets/\"", self.deploy_script)
        self.assertIn("--cache-control \"public,max-age=31536000,immutable\"", self.deploy_script)

    def test_shared_guard_and_fail_closed_behavior(self):
        for script_text in (self.create_script, self.deploy_script):
            self.assertIn('source "$SCRIPT_DIR/jm8_deployment_guard.sh"', script_text)
            self.assertIn("jm8_validate_contract_or_exit", script_text)
            self.assertIn("set -euo pipefail", script_text)
            self.assertIn("exit 1", script_text)
        self.assertNotIn("STRIPE_SECRET_KEY", self.create_script)
        self.assertNotIn("STRIPE_SECRET_KEY", self.deploy_script)
        self.assertNotIn("|| true", self.create_script)
        self.assertNotIn("|| true", self.deploy_script)

    def test_scripts_support_only_staging_and_production(self):
        for source in (self.create_script, self.deploy_script):
            self.assertIn("staging|prod", source)
            self.assertNotIn("staging-only", source)
        self.assertIn('[[ "$AWS_PROFILE" != "jm8-prod" ]]', self.create_script)
        self.assertIn('[[ "$AWS_PROFILE" != "jm8-prod" ]]', self.deploy_script)
        self.assertIn("journalm8-prod-frontend-114743615542", self.create_script)
        self.assertIn("journalm8-prod-frontend-114743615542", self.deploy_script)

    def test_production_does_not_use_staging_basic_auth_health_checks(self):
        self.assertIn('if [[ "$STAGE" == "staging" ]]', self.deploy_script)
        self.assertIn(
            "production frontend health checks must return 200 without Basic Auth",
            self.deploy_script,
        )
        self.assertIn("expected_viewer_request_count = 1 if expected_stage == \"staging\" else 0", self.diagnostics_helper)

    def test_amplify_files_are_absent(self):
        self.assertFalse(AMPLIFY_FILE.exists())
        self.assertFalse(LEGACY_TEST_FILE.exists())

    def test_cloudfront_helper_reports_named_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_path = Path(tmpdir) / "distribution.json"
            dist_path.write_text(json.dumps({
                "Distribution": {
                    "Status": "InProgress",
                    "DomainName": "example.cloudfront.net",
                    "DistributionConfig": {
                        "Enabled": False,
                        "DefaultRootObject": "index.html",
                        "Origins": {"Items": [{"DomainName": "bucket.s3.amazonaws.com", "OriginAccessControlId": "bad-oac"}]},
                        "DefaultCacheBehavior": {
                            "ViewerProtocolPolicy": "allow-all",
                            "CachePolicyId": "wrong",
                            "FunctionAssociations": {"Items": [{"EventType": "viewer-request"}]},
                        },
                        "CustomErrorResponses": {"Items": [{"ErrorCode": 403, "ResponseCode": 404, "ResponsePagePath": "/index.html"}]},
                    },
                }
            }), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(DIAGNOSTICS_HELPER),
                    "cloudfront-verify",
                    str(dist_path),
                    "bucket.s3.amazonaws.com",
                    "GOOD_OAC_123",
                    "d2m5h45dzf3zij.cloudfront.net",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["error_code"], "CLOUDFRONT_DISTRIBUTION_STATUS_INVALID")
            self.assertIn("validation_name", payload["checks"][0])
            self.assertIn("expected_value", payload["checks"][0])
            self.assertIn("safe_actual_value", payload["checks"][0])
            self.assertIn("stable_error_code", payload["checks"][0])

    def test_cloudfront_helper_accepts_string_response_codes_for_error_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_path = Path(tmpdir) / "distribution.json"
            dist_path.write_text(json.dumps({
                "Distribution": {
                    "Status": "Deployed",
                    "DomainName": "d2m5h45dzf3zij.cloudfront.net",
                    "DistributionConfig": {
                        "Enabled": True,
                        "DefaultRootObject": "index.html",
                        "Origins": {"Items": [{
                            "DomainName": "bucket.s3.amazonaws.com",
                            "OriginAccessControlId": "GOOD_OAC_123",
                            "S3OriginConfig": {"OriginAccessIdentity": ""},
                        }]},
                        "DefaultCacheBehavior": {
                            "ViewerProtocolPolicy": "redirect-to-https",
                            "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
                            "FunctionAssociations": {"Items": [{"EventType": "viewer-request"}]},
                        },
                        "CustomErrorResponses": {"Items": [
                            {"ErrorCode": 403, "ResponsePagePath": "/index.html", "ResponseCode": "200", "ErrorCachingMinTTL": 0},
                            {"ErrorCode": 404, "ResponsePagePath": "/index.html", "ResponseCode": "200", "ErrorCachingMinTTL": 0},
                        ]},
                    },
                }
            }), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(DIAGNOSTICS_HELPER),
                    "cloudfront-verify",
                    str(dist_path),
                    "bucket.s3.amazonaws.com",
                    "GOOD_OAC_123",
                    "d2m5h45dzf3zij.cloudfront.net",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["passed"])
            self.assertIsNone(payload["error_code"])

    def test_cloudfront_helper_rejects_basic_auth_for_production(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_path = Path(tmpdir) / "distribution.json"
            dist_path.write_text(json.dumps({
                "Distribution": {
                    "Id": "PRODDIST123",
                    "Status": "Deployed",
                    "DomainName": "prod.cloudfront.net",
                    "DistributionConfig": {
                        "Enabled": True,
                        "DefaultRootObject": "index.html",
                        "Origins": {"Items": [{
                            "DomainName": "prod-bucket.s3.amazonaws.com",
                            "OriginAccessControlId": "PROD_OAC_123",
                            "S3OriginConfig": {"OriginAccessIdentity": ""},
                        }]},
                        "DefaultCacheBehavior": {
                            "ViewerProtocolPolicy": "redirect-to-https",
                            "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
                            "FunctionAssociations": {"Items": [{"EventType": "viewer-request"}]},
                        },
                        "CustomErrorResponses": {"Items": [
                            {"ErrorCode": 403, "ResponsePagePath": "/index.html", "ResponseCode": "200"},
                            {"ErrorCode": 404, "ResponsePagePath": "/index.html", "ResponseCode": "200"},
                        ]},
                    },
                }
            }), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(DIAGNOSTICS_HELPER),
                    "cloudfront-verify",
                    str(dist_path),
                    "prod-bucket.s3.amazonaws.com",
                    "PROD_OAC_123",
                    "prod.cloudfront.net",
                    "prod",
                    "PRODDIST123",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                json.loads(result.stdout)["error_code"],
                "CLOUDFRONT_FUNCTION_ASSOCIATION_INVALID",
            )
            payload = json.loads(dist_path.read_text(encoding="utf-8"))
            payload["Distribution"]["DistributionConfig"][
                "DefaultCacheBehavior"
            ]["FunctionAssociations"] = {"Quantity": 0}
            dist_path.write_text(json.dumps(payload), encoding="utf-8")
            accepted = subprocess.run(
                [
                    sys.executable,
                    str(DIAGNOSTICS_HELPER),
                    "cloudfront-verify",
                    str(dist_path),
                    "prod-bucket.s3.amazonaws.com",
                    "PROD_OAC_123",
                    "prod.cloudfront.net",
                    "prod",
                    "PRODDIST123",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout)

    def test_stack_tags_and_parameters_are_verified_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stack_path = Path(tmpdir) / "stack.json"
            stack_path.write_text(json.dumps({
                "Stacks": [{
                    "StackName": "journalm8-prod-frontend-hosting",
                    "StackStatus": "CREATE_COMPLETE",
                    "Parameters": [
                        {"ParameterKey": "AppName", "ParameterValue": "journalm8"},
                        {"ParameterKey": "Stage", "ParameterValue": "prod"},
                        {
                            "ParameterKey": "FrontendBucketName",
                            "ParameterValue": "journalm8-prod-frontend-114743615542",
                        },
                    ],
                    "Tags": [
                        {"Key": "App", "Value": "journalm8"},
                        {"Key": "Stage", "Value": "prod"},
                        {"Key": "ManagedBy", "Value": "aws-cli"},
                    ],
                    "Outputs": [],
                }]
            }), encoding="utf-8")
            command = [
                sys.executable,
                str(DIAGNOSTICS_HELPER),
                "stack-contract-verify",
                str(stack_path),
                "journalm8-prod-frontend-hosting",
                "prod",
                "journalm8-prod-frontend-114743615542",
            ]
            accepted = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout)

            payload = json.loads(stack_path.read_text(encoding="utf-8"))
            payload["Stacks"][0]["Tags"][1]["Value"] = "staging"
            stack_path.write_text(json.dumps(payload), encoding="utf-8")
            rejected = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(
                json.loads(rejected.stdout)["error_code"],
                "STACK_TAG_MISMATCH",
            )

    def test_exact_bucket_policy_contract_is_verified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = Path(tmpdir) / "policy.json"
            bucket = "journalm8-prod-frontend-114743615542"
            distribution_arn = (
                "arn:aws:cloudfront::114743615542:distribution/PRODDIST123"
            )
            policy_path.write_text(json.dumps({
                "Policy": json.dumps({
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "cloudfront.amazonaws.com"},
                            "Action": "s3:GetObject",
                            "Resource": f"arn:aws:s3:::{bucket}/*",
                            "Condition": {
                                "StringEquals": {"AWS:SourceArn": distribution_arn}
                            },
                        },
                        {
                            "Effect": "Deny",
                            "Principal": "*",
                            "Action": "s3:*",
                            "Resource": [
                                f"arn:aws:s3:::{bucket}",
                                f"arn:aws:s3:::{bucket}/*",
                            ],
                            "Condition": {
                                "Bool": {"aws:SecureTransport": "false"}
                            },
                        },
                    ]
                })
            }), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(DIAGNOSTICS_HELPER),
                    "bucket-policy-verify",
                    str(policy_path),
                    bucket,
                    distribution_arn,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_create_script_reports_and_sanitizes_diagnostics(self):
        self.assertIn('mkdir -p "$DIAGNOSTICS_DIR"', self.create_script)
        self.assertIn('Diagnostic report path: $DIAGNOSTIC_PATH', self.create_script)
        self.assertIn('trap \'handle_exit "$?"\' EXIT', self.create_script)
        self.assertIn('REPORT_EXIT_CODE', self.create_script)
        self.assertIn('write-report', self.create_script)
        self.assertIn('--stable-error-code', self.create_script)
        self.assertIn('--sanitized-error-message', self.create_script)
        self.assertIn('rm -rf "$TEMP_DIR"', self.create_script)
        self.assertIn('final_status', self.diagnostics_helper)
        self.assertIn('stable_error_code', self.diagnostics_helper)
        self.assertNotIn('assert ', self.create_script)
        self.assertNotIn('assert ', self.diagnostics_helper)
        self.assertNotIn('Traceback', self.diagnostics_helper)
        self.assertNotIn('Authorization: Basic', self.create_script)
        self.assertNotIn('aws_secret_access_key', self.create_script)

    def test_helper_redacts_credentials_and_preserves_exit_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            error_path = Path(tmpdir) / "aws-error.txt"
            error_path.write_text("Authorization: Basic abc123\naws_secret_access_key=supersecret\n", encoding="utf-8")
            sanitized = subprocess.run(
                [sys.executable, str(DIAGNOSTICS_HELPER), "sanitize-error", str(error_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(sanitized.returncode, 0)
            self.assertNotIn("abc123", sanitized.stdout)
            self.assertNotIn("supersecret", sanitized.stdout)
            self.assertIn("Authorization=<redacted>", sanitized.stdout)
            self.assertIn("aws_secret=<redacted>", sanitized.stdout)

    def test_script_tracks_required_phases_and_cleanup(self):
        for phase in (
            "environment_contract",
            "local_template_preflight",
            "predeployment_inspection",
            "cloudformation_deploy",
            "deployed_template_verification",
            "output_validation",
            "s3_verification",
            "cloudfront_distribution_verification",
            "oac_verification",
            "completed",
        ):
            self.assertIn(phase, self.create_script)
        self.assertIn('mkdir -p "$DIAGNOSTICS_DIR"', self.create_script)
        self.assertIn('trap \'handle_exit "$?"\' EXIT', self.create_script)
        self.assertIn('rm -rf "$TEMP_DIR"', self.create_script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
