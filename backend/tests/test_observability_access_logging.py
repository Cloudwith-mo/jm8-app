import unittest
from pathlib import Path


class ObservabilityAccessLoggingTests(unittest.TestCase):
    def setUp(self):
        backend_dir = Path(__file__).resolve().parents[1]
        self.create_api_script = (backend_dir / "bin" / "create-api").read_text()
        self.deploy_observability_script = (
            backend_dir / "bin" / "deploy-observability"
        ).read_text()

    def _access_log_block(self, script_text: str) -> str:
        marker = '"Format": json.dumps({'
        start = script_text.find(marker)
        if start == -1:
            self.fail("Access log format block was not found in script.")
        end = script_text.find("}))", start)
        if end == -1:
            self.fail("Access log format block is incomplete.")
        return script_text[start : end + 3]

    def test_create_api_and_deploy_observability_configure_access_logs(self):
        for script_name, script_text in (
            ("create-api", self.create_api_script),
            ("deploy-observability", self.deploy_observability_script),
        ):
            with self.subTest(script=script_name):
                self.assertIn("--stage-name '$default'", script_text)
                self.assertIn("--access-log-settings", script_text)
                self.assertIn("--default-route-settings DetailedMetricsEnabled=true", script_text)
                self.assertIn("DetailedMetricsEnabled=true", script_text)
                self.assertIn("/aws/apigateway/", script_text)
                self.assertIn("--retention-in-days 30", script_text)

                block = self._access_log_block(script_text)
                for required in (
                    '"requestId": "$context.requestId"',
                    '"routeKey": "$context.routeKey"',
                    '"httpMethod": "$context.httpMethod"',
                    '"status": "$context.status"',
                    '"responseLatency": "$context.responseLatency"',
                    '"integrationLatency": "$context.integrationLatency"',
                    '"integrationError": "$context.integrationErrorMessage"',
                    '"sourceIp": "$context.identity.sourceIp"',
                ):
                    self.assertIn(required, block)

    def test_access_log_configuration_excludes_sensitive_fields(self):
        for script_name, script_text in (
            ("create-api", self.create_api_script),
            ("deploy-observability", self.deploy_observability_script),
        ):
            with self.subTest(script=script_name):
                block = self._access_log_block(script_text).lower()
                forbidden = (
                    "authorization",
                    "jwt",
                    "request body",
                    "response body",
                    "stripe-signature",
                    "journal",
                    "webhook payload",
                    "webhook body",
                    "journal text",
                    "token",
                )
                for value in forbidden:
                    self.assertNotIn(value, block)

    def test_deploy_observability_tracks_analyze_entry_and_billing_failures(self):
        script = self.deploy_observability_script

        self.assertIn('ANALYZE_ENTRY_FUNCTION_NAME="${APP_NAME}-${STAGE}-analyze-entry"', script)
        self.assertIn('ANALYZE_ENTRY_LOG_GROUP="/aws/lambda/${ANALYZE_ENTRY_FUNCTION_NAME}"', script)
        self.assertIn("put-retention-policy", script)
        self.assertIn("--retention-in-days 30", script)

        self.assertIn('{ $.event = "billing_webhook_failed" }', script)
        self.assertIn("BillingWebhookFailures", script)
        self.assertIn("BILLING_WEBHOOK_FAILURES_ALARM", script)
        self.assertIn('--alarm-actions "$TOPIC_ARN"', script)
        self.assertIn("$TOPIC_ARN", script)
        self.assertIn("billing-webhook-failures", script)
        self.assertIn("widgets", script)
        self.assertIn("BillingWebhookFailures", script)

    def test_scripts_are_idempotent_and_use_expected_operations(self):
        for script_name, script_text in (
            ("create-api", self.create_api_script),
            ("deploy-observability", self.deploy_observability_script),
        ):
            with self.subTest(script=script_name):
                self.assertIn("describe-log-groups", script_text)
                self.assertIn("create-log-group", script_text)
                self.assertIn("put-retention-policy", script_text)
                self.assertIn("put-resource-policy", script_text)
                self.assertIn("update-stage", script_text)

        self.assertIn("put-metric-filter", self.deploy_observability_script)
        self.assertIn("put-metric-alarm", self.deploy_observability_script)
        self.assertIn("billing_webhook_failed", self.deploy_observability_script)
        self.assertIn("BillingWebhookFailures", self.deploy_observability_script)
        self.assertIn("billing-webhook-failures", self.deploy_observability_script)


if __name__ == "__main__":
    unittest.main()
