import re
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]

LEGACY_NAMESPACES = (
    "JM8/OCR",
    "JM8/HistoricalReanalysis",
    "JM8/Billing",
    "JM8/AI",
)

NAMESPACE_ASSIGNMENTS = {
    "METRIC_NAMESPACE": "JM8/${STAGE}/OCR",
    "REANALYSIS_METRIC_NAMESPACE": "JM8/${STAGE}/HistoricalReanalysis",
    "BILLING_METRIC_NAMESPACE": "JM8/${STAGE}/Billing",
    "AI_METRIC_NAMESPACE": "JM8/${STAGE}/AI",
}


def _resolve_namespace(template: str, stage: str) -> str:
    return template.replace("${STAGE}", stage)


class ObservabilityNamespaceIsolationTests(unittest.TestCase):
    def setUp(self):
        self.deploy_observability_script = (
            BACKEND_ROOT / "bin" / "deploy-observability"
        ).read_text()
        self.deploy_analysis_observability_script = (
            BACKEND_ROOT / "bin" / "deploy-analysis-observability"
        ).read_text()

    def test_scripts_define_stage_specific_namespace_variables(self):
        for variable, template in NAMESPACE_ASSIGNMENTS.items():
            assignment = f'{variable}="{template}"'
            script = (
                self.deploy_analysis_observability_script
                if variable == "AI_METRIC_NAMESPACE"
                else self.deploy_observability_script
            )
            self.assertIn(
                assignment,
                script,
                f"{variable} must be assigned as {assignment!r}",
            )

    def test_dev_and_staging_resolve_to_different_namespaces(self):
        for variable, template in NAMESPACE_ASSIGNMENTS.items():
            dev_namespace = _resolve_namespace(template, "dev")
            staging_namespace = _resolve_namespace(template, "staging")

            self.assertNotEqual(
                dev_namespace,
                staging_namespace,
                f"{variable} must resolve differently across stages",
            )

            for legacy_namespace in LEGACY_NAMESPACES:
                self.assertNotEqual(dev_namespace, legacy_namespace)
                self.assertNotEqual(staging_namespace, legacy_namespace)

    def test_no_script_uses_legacy_shared_namespace_literal(self):
        namespace_pattern = re.compile(
            r'"(JM8/(?:OCR|HistoricalReanalysis|Billing|AI))"'
        )

        for script_name, script_text in (
            ("deploy-observability", self.deploy_observability_script),
            (
                "deploy-analysis-observability",
                self.deploy_analysis_observability_script,
            ),
        ):
            with self.subTest(script=script_name):
                matches = namespace_pattern.findall(script_text)
                self.assertEqual(
                    matches,
                    [],
                    f"{script_name} still references legacy shared namespace(s): {matches}",
                )

    def test_deploy_observability_metric_filters_use_namespace_variables(self):
        script = self.deploy_observability_script

        self.assertIn('metricNamespace=${METRIC_NAMESPACE}', script)
        self.assertIn('"$REANALYSIS_METRIC_NAMESPACE"', script)
        self.assertIn(
            'metricNamespace=${namespace}',
            script,
        )
        self.assertIn('"$BILLING_METRIC_NAMESPACE"', script)

    def test_deploy_observability_alarms_use_namespace_variables(self):
        script = self.deploy_observability_script

        self.assertIn(
            'put_undimensioned_count_alarm \\\n'
            '  "$REANALYSIS_START_FAILURES_ALARM"',
            script,
        )
        self.assertIn('"$REANALYSIS_METRIC_NAMESPACE"', script)
        self.assertIn(
            'put_undimensioned_count_alarm \\\n'
            '  "$BILLING_WEBHOOK_FAILURES_ALARM"',
            script,
        )
        self.assertIn('"$BILLING_METRIC_NAMESPACE"', script)

    def test_deploy_observability_dashboard_passes_namespace_variables_as_arguments(self):
        script = self.deploy_observability_script

        self.assertIn('"$METRIC_NAMESPACE" \\', script)
        self.assertIn('"$REANALYSIS_METRIC_NAMESPACE" \\', script)
        self.assertIn('"$BILLING_METRIC_NAMESPACE" \\', script)

        self.assertIn("metric_namespace = sys.argv[12]", script)
        self.assertIn("reanalysis_metric_namespace = sys.argv[18]", script)
        self.assertIn("billing_metric_namespace = sys.argv[19]", script)

        self.assertIn("billing_metric_namespace,\n                    \"BillingWebhookFailures\"", script)
        self.assertNotIn('"JM8/Billing"', script)

    def test_deploy_analysis_observability_uses_namespace_variable_everywhere(self):
        script = self.deploy_analysis_observability_script

        self.assertIn('metricNamespace=${AI_METRIC_NAMESPACE}', script)
        self.assertIn('--namespace "$AI_METRIC_NAMESPACE"', script)
        self.assertIn('"$AI_METRIC_NAMESPACE" \\', script)
        self.assertIn("namespace = sys.argv[3]", script)

    def test_no_or_true_introduced_in_observability_scripts(self):
        for script_name, script_text in (
            ("deploy-observability", self.deploy_observability_script),
            (
                "deploy-analysis-observability",
                self.deploy_analysis_observability_script,
            ),
        ):
            with self.subTest(script=script_name):
                self.assertNotIn("|| true", script_text)


if __name__ == "__main__":
    unittest.main()
