import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "infra" / "cloudfront" / "frontend-hosting.yaml"
CREATE_SCRIPT = REPO_ROOT / "bin" / "create-frontend-hosting"
DEPLOY_SCRIPT = REPO_ROOT / "bin" / "deploy-frontend"
AMPLIFY_FILE = REPO_ROOT.parent / "amplify.yml"
LEGACY_TEST_FILE = REPO_ROOT / "tests" / "test_amplify_configuration.py"


class FrontendHostingWiringTests(unittest.TestCase):
    def setUp(self):
        self.template = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.create_script = CREATE_SCRIPT.read_text(encoding="utf-8")
        self.deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

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

    def test_distribution_security_and_spa_behavior(self):
        self.assertIn("redirect-to-https", self.template)
        self.assertIn("TLSv1.2_2021", self.template)
        self.assertIn("CachePolicyId: 658327ea-f89d-4fab-a63d-7e88639e58f6", self.template)
        self.assertIn("403", self.template)
        self.assertIn("404", self.template)
        self.assertIn("/index.html", self.template)
        self.assertIn("ErrorCachingMinTTL: 0", self.template)
        self.assertIn("DefaultRootObject: index.html", self.template)

    def test_cloudfront_function_basic_auth_is_required(self):
        self.assertIn("viewer-request", self.template)
        self.assertIn("FunctionAssociations", self.template)
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
        self.assertIn("sha256sum", self.create_script)
        self.assertIn("Basic ${AUTH_HEADER}", self.create_script)
        self.assertNotIn("STAGING_BASIC_AUTH_USERNAME=", self.create_script)
        self.assertNotIn("STAGING_BASIC_AUTH_PASSWORD=", self.create_script)
        self.assertNotIn("Authorization: Basic", self.create_script)
        self.assertNotIn("curl -u", self.deploy_script)
        self.assertNotIn("-u \"${STAGING_BASIC_AUTH_USERNAME}:${STAGING_BASIC_AUTH_PASSWORD}\"", self.deploy_script)
        self.assertNotIn("-H \"Authorization: Basic", self.deploy_script)
        self.assertIn("curl --config -", self.deploy_script)
        self.assertIn("auth_header=\"Authorization: Basic", self.deploy_script)

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
        self.assertIn("aws s3 sync dist/ \"s3://${FRONTEND_BUCKET}\"", self.deploy_script)
        self.assertIn("--delete", self.deploy_script)
        self.assertIn("--cache-control \"no-cache\"", self.deploy_script)
        self.assertIn("no-cache,no-store,must-revalidate", self.deploy_script)
        self.assertIn("public,max-age=31536000,immutable", self.deploy_script)
        self.assertIn("aws s3 cp dist/assets/ \"s3://${FRONTEND_BUCKET}/assets/\"", self.deploy_script)
        self.assertNotIn("--exclude \"index.html\" --cache-control \"public,max-age=31536000,immutable\"", self.deploy_script)

    def test_invalidation_and_health_checks_exist(self):
        self.assertIn("create-invalidation", self.deploy_script)
        self.assertIn("invalidation-deployed", self.deploy_script)
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

    def test_amplify_files_are_absent(self):
        self.assertFalse(AMPLIFY_FILE.exists())
        self.assertFalse(LEGACY_TEST_FILE.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
