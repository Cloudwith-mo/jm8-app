import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AccountExportDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deploy = (ROOT / "bin" / "deploy-account-export").read_text()
        cls.api_deploy = (ROOT / "bin" / "deploy").read_text()
        cls.create_api = (ROOT / "bin" / "create-api").read_text()
        cls.secure_api = (ROOT / "bin" / "secure-api").read_text()
        cls.workflow = (ROOT / "workflows" / "account-export.asl.json").read_text()
        cls.observability = (ROOT / "bin" / "deploy-observability").read_text()
        cls.worker = (ROOT / "function" / "account_export_worker.py").read_text()
        cls.data_contract = (ROOT.parent / "docs" / "JM8_USER_DATA_CONTRACT.md").read_text()

    def test_bucket_hardening_lifecycle_and_distinctness(self):
        for required in (
            "BlockPublicAcls=true", "IgnorePublicAcls=true", "BlockPublicPolicy=true",
            "RestrictPublicBuckets=true", "BucketOwnerEnforced", "DenyInsecureTransport",
            '"Days":1', '"DaysAfterInitiation":1', "delete-bucket-website",
            'EXPORT_BUCKET" = "$RAW_BUCKET', "Status=Suspended",
        ):
            self.assertIn(required, self.deploy)

    def test_worker_limits_configuration_and_scoped_iam(self):
        for required in (
            "python3.12", "arm64", "--timeout 900", "--memory-size 1024",
            "EPHEMERAL_STORAGE_MB=6144", 'Size="$EPHEMERAL_STORAGE_MB"',
            "put-function-concurrency", "accountExportTtlEpoch",
            'f"arn:aws:s3:::{raw}/users/*/uploads/*"',
            'f"arn:aws:s3:::{exports}/exports/*"',
        ):
            self.assertIn(required, self.deploy)
        for forbidden in ("bedrock:", "textract:", "secretsmanager:", "cognito-idp:DeleteUser"):
            self.assertNotIn(forbidden, self.deploy.lower())

    def test_capacity_invariants_are_implemented_and_documented(self):
        self.assertIn("MAX_SOURCE_BYTES = 2 * 1024**3", self.worker)
        self.assertIn("MAX_ARCHIVE_BYTES = 3 * 1024**3", self.worker)
        self.assertIn(
            "MAX_SOURCE_BYTES + MAX_ARCHIVE_BYTES >= CONFIGURED_EPHEMERAL_STORAGE_BYTES",
            self.worker,
        )
        self.assertIn(
            "MAX_ARCHIVE_BYTES >= S3_SINGLE_PUT_OBJECT_LIMIT_BYTES",
            self.worker,
        )
        self.assertIn(
            "MAX_SOURCE_BYTES + MAX_ARCHIVE_BYTES < ephemeral storage",
            self.data_contract,
        )
        self.assertIn(
            "MAX_ARCHIVE_BYTES < the 5 GB single-request PutObject limit",
            self.data_contract,
        )

    def test_workflow_has_retry_catch_and_failure_recorder(self):
        for required in ("RunExport", "Retry", "Catch", "RecordFailure", "ExportFailed"):
            self.assertIn(required, self.workflow)

    def test_all_routes_are_created_and_secured(self):
        for route in (
            "POST /account/exports", "GET /account/exports", "GET /account/exports/{exportId}",
        ):
            self.assertIn(route, self.create_api)
            self.assertIn(route, self.secure_api)

    def test_api_policy_is_exact_and_environment_is_wired(self):
        self.assertIn("ACCOUNT_EXPORT_WORKFLOW_ARN", self.api_deploy)
        self.assertIn('"states:StartExecution"', self.api_deploy)
        self.assertIn('f"arn:aws:s3:::{bucket}/exports/*"', self.api_deploy)
        self.assertNotIn('f"arn:aws:s3:::{bucket}/*"', self.api_deploy)

    def test_observability_has_export_alarms_metrics_and_retention(self):
        for required in (
            "account-export-worker-errors", "account-export-workflow-failed",
            "account-export-workflow-timed-out", "ExecutionsFailed", "ExecutionsTimedOut",
            "AccountExportRequested", "AccountExportCompleted", "AccountExportFailed",
            "PackageSizeBytes", "--retention-in-days 30", "--treat-missing-data notBreaching",
        ):
            self.assertIn(required, self.observability)


if __name__ == "__main__":
    unittest.main()
