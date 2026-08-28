import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "bin" / "deploy-observability"
).read_text(encoding="utf-8")


class ObservabilityAlarmTests(unittest.TestCase):
    def _function(self, name: str) -> str:
        start = SCRIPT.index(f"{name}() {{")
        end = SCRIPT.index("\n}\n", start)
        return SCRIPT[start : end + 3]

    def test_ocr_worker_error_alarm_is_stage_scoped_and_actionable(self):
        self.assertIn(
            'WORKER_ERRORS_ALARM="${APP_NAME}-${STAGE}-ocr-worker-errors"',
            SCRIPT,
        )
        worker_call = SCRIPT[SCRIPT.index('  "$WORKER_ERRORS_ALARM"') :]
        worker_call = worker_call[: worker_call.index("\n\n")]
        for expected in (
            '"$WORKER_ERRORS_ALARM"',
            '"AWS/Lambda"',
            '"Errors"',
            '"FunctionName"',
            '"$WORKER_FUNCTION_NAME"',
        ):
            self.assertIn(expected, worker_call)
        helper = self._function("put_count_alarm")
        self.assertIn("--statistic Sum", helper)
        self.assertIn("--unit Count", helper)
        self.assertIn("--treat-missing-data notBreaching", helper)
        self.assertIn('--alarm-actions "$TOPIC_ARN"', helper)

    def test_api_gateway_alarms_use_resolved_api_and_default_stage(self):
        self.assertIn(
            'API_GATEWAY_5XX_ALARM="${APP_NAME}-${STAGE}-api-gateway-5xx"',
            SCRIPT,
        )
        self.assertIn(
            'API_GATEWAY_HIGH_LATENCY_ALARM="${APP_NAME}-${STAGE}-api-gateway-high-latency"',
            SCRIPT,
        )
        self.assertIn(
            'API_ID="$(jm8_resolve_http_api_id "$API_GATEWAY_NAME")"',
            SCRIPT,
        )

        helper = self._function("put_api_gateway_alarm")
        for expected in (
            '--namespace "AWS/ApiGateway"',
            '"Name=ApiId,Value=${API_ID}"',
            '"Name=Stage,Value=\\$default"',
            '--period 300',
            '--evaluation-periods 1',
            '--datapoints-to-alarm 1',
            '--comparison-operator GreaterThanOrEqualToThreshold',
            '--treat-missing-data notBreaching',
            '--alarm-actions "$TOPIC_ARN"',
        ):
            self.assertIn(expected, helper)

        gateway_calls = SCRIPT[SCRIPT.index('  "$API_GATEWAY_5XX_ALARM"') :]
        gateway_calls = gateway_calls[: gateway_calls.index("\n\nput_count_alarm")]
        for expected in (
            '"$API_GATEWAY_5XX_ALARM"',
            '"5xx"',
            '"Sum"',
            '"Count"',
            '"1"',
            '"$API_GATEWAY_HIGH_LATENCY_ALARM"',
            '"Latency"',
            '"Maximum"',
            '"Milliseconds"',
            '"25000"',
        ):
            self.assertIn(expected, gateway_calls)

    def test_dashboard_contains_alarm_status_and_api_gateway_health(self):
        self.assertIn('  "$API_ID" \\', SCRIPT)
        self.assertIn("> .build/operations-dashboard.json", SCRIPT)
        self.assertIn("api_id = sys.argv[20]", SCRIPT)
        for alarm_suffix in (
            "ocr-worker-errors",
            "api-gateway-5xx",
            "api-gateway-high-latency",
        ):
            self.assertIn(f'f"{{app_name}}-{{stage}}-{alarm_suffix}"', SCRIPT)

        start = SCRIPT.index('"title": "API Gateway health"')
        end = SCRIPT.index("\n    },\n])", start)
        widget = SCRIPT[start:end]
        for metric in ('"Count"', '"5xx"', '"Latency"', '"IntegrationLatency"'):
            self.assertIn(metric, widget)
        self.assertEqual(widget.count('"ApiId"'), 6)
        self.assertEqual(widget.count("api_id"), 6)
        self.assertEqual(widget.count('"Stage"'), 6)
        self.assertEqual(widget.count('"$default"'), 6)
        self.assertEqual(widget.count('{"stat": "p95"'), 2)
        self.assertEqual(widget.count('"stat": "Maximum"'), 2)
        self.assertEqual(widget.count('{"stat": "Sum"'), 2)

    def test_production_api_id_is_never_hard_coded(self):
        self.assertNotIn("j56qvbzzpe", SCRIPT)


if __name__ == "__main__":
    unittest.main()
