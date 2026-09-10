from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
FUNCTION_DIR = BACKEND_ROOT / "function"
BIN_DIR = BACKEND_ROOT / "bin"
sys.path.insert(0, str(FUNCTION_DIR))

from semantic_retrieval_evaluation import (  # noqa: E402
    MAX_EVALUATION_CASES,
    MAX_RESULTS_PER_CASE,
    SEMANTIC_RETRIEVAL_EVALUATION_VERSION,
    SemanticRetrievalEvaluationError,
    evaluate_semantic_retrieval,
    validate_passing_evaluation_report,
)


def result(entry: str, tenant: str = "tenant-a", current: bool = True):
    return {
        "entryLabel": entry,
        "tenantLabel": tenant,
        "current": current,
    }


def passing_document():
    return {
        "evaluationVersion": SEMANTIC_RETRIEVAL_EVALUATION_VERSION,
        "cases": [
            {
                "caseId": "recurring-goal",
                "tenantLabel": "tenant-a",
                "relevantEntryLabels": ["goal-jan"],
                "results": [result("goal-jan")],
            },
            {
                "caseId": "repeated-challenge",
                "tenantLabel": "tenant-a",
                "relevantEntryLabels": ["challenge-mar"],
                "results": [result("challenge-mar")],
            },
            {
                "caseId": "mindset-change",
                "tenantLabel": "tenant-a",
                "relevantEntryLabels": ["mindset-may"],
                "results": [result("mindset-may")],
            },
            {
                "caseId": "empty-history",
                "tenantLabel": "tenant-empty",
                "relevantEntryLabels": [],
                "results": [],
            },
        ],
    }


class SemanticRetrievalEvaluationTests(unittest.TestCase):
    def test_perfect_evaluation_passes_without_private_identifiers(self):
        document = passing_document()
        report = evaluate_semantic_retrieval(document)

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["caseCount"], 4)
        self.assertEqual(report["positiveCaseCount"], 3)
        self.assertEqual(report["negativeCaseCount"], 1)
        self.assertEqual(report["metrics"], {
            "macroRecallAtK": 1.0,
            "macroPrecisionAtK": 1.0,
            "meanReciprocalRank": 1.0,
        })
        self.assertEqual(set(report["violations"].values()), {0})
        validate_passing_evaluation_report(report)

        serialized = json.dumps(report)
        for private_value in (
            "tenant-a",
            "tenant-empty",
            "goal-jan",
            "challenge-mar",
            "mindset-may",
        ):
            self.assertNotIn(private_value, serialized)

    def test_ranked_quality_metrics_are_macro_averaged(self):
        document = passing_document()
        document["cases"][0]["relevantEntryLabels"] = ["goal-jan", "goal-feb"]
        document["cases"][0]["results"] = [
            result("other"),
            result("goal-jan"),
            result("goal-feb"),
        ]

        report = evaluate_semantic_retrieval(document)

        self.assertEqual(report["metrics"], {
            "macroRecallAtK": 1.0,
            "macroPrecisionAtK": 0.888889,
            "meanReciprocalRank": 0.833333,
        })
        self.assertEqual(report["status"], "PASS")

    def test_quality_below_threshold_fails(self):
        document = passing_document()
        document["cases"][0]["results"] = [result("irrelevant")]
        document["cases"][1]["results"] = [result("also-irrelevant")]

        report = evaluate_semantic_retrieval(document)

        self.assertEqual(report["status"], "FAIL")
        self.assertIn("RECALL_BELOW_THRESHOLD", report["failureCodes"])
        self.assertIn("PRECISION_BELOW_THRESHOLD", report["failureCodes"])
        self.assertIn("RANKING_BELOW_THRESHOLD", report["failureCodes"])
        self.assertIn("POSITIVE_CASE_MISSED", report["failureCodes"])
        self.assertEqual(
            report["failingCaseIds"],
            ["recurring-goal", "repeated-challenge"],
        )
        with self.assertRaises(SemanticRetrievalEvaluationError):
            validate_passing_evaluation_report(report)

    def test_tenant_stale_duplicate_and_negative_violations_fail(self):
        document = passing_document()
        document["cases"][0]["results"] = [
            result("goal-jan", tenant="tenant-b"),
            result("goal-jan", current=False),
        ]
        document["cases"][3]["results"] = [
            result("unexpected", tenant="tenant-empty")
        ]

        report = evaluate_semantic_retrieval(document)

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["violations"], {
            "tenantLeakCount": 1,
            "staleResultCount": 1,
            "duplicateResultCount": 1,
            "negativeFalsePositiveCount": 1,
            "missedPositiveCaseCount": 0,
        })
        self.assertEqual(
            report["failingCaseIds"],
            ["empty-history", "recurring-goal"],
        )

    def test_requires_positive_and_negative_coverage(self):
        document = passing_document()
        document["cases"] = document["cases"][:2]
        report = evaluate_semantic_retrieval(document)
        self.assertIn("INSUFFICIENT_POSITIVE_CASES", report["failureCodes"])
        self.assertIn("INSUFFICIENT_NEGATIVE_CASES", report["failureCodes"])

    def test_rejects_extra_fields_that_could_carry_private_data(self):
        private_fields = (
            ("question", "What happened?"),
            ("text", "Private journal text"),
            ("userId", "cognito-subject"),
            ("entryId", "real-entry-id"),
            ("embedding", [1.0]),
            ("contentDigest", "a" * 64),
        )
        for name, value in private_fields:
            with self.subTest(name=name):
                document = passing_document()
                document["cases"][0][name] = value
                with self.assertRaises(SemanticRetrievalEvaluationError):
                    evaluate_semantic_retrieval(document)

    def test_rejects_malformed_aliases_duplicates_and_limits(self):
        mutations = []
        malformed = passing_document()
        malformed["cases"][0]["caseId"] = "USER#private"
        mutations.append(malformed)

        duplicate_case = passing_document()
        duplicate_case["cases"][1]["caseId"] = "recurring-goal"
        mutations.append(duplicate_case)

        duplicate_relevant = passing_document()
        duplicate_relevant["cases"][0]["relevantEntryLabels"] = ["x", "x"]
        mutations.append(duplicate_relevant)

        non_boolean = passing_document()
        non_boolean["cases"][0]["results"][0]["current"] = 1
        mutations.append(non_boolean)

        too_many_results = passing_document()
        too_many_results["cases"][0]["results"] = [
            result(f"entry-{index}")
            for index in range(MAX_RESULTS_PER_CASE + 1)
        ]
        mutations.append(too_many_results)

        too_many_cases = passing_document()
        template = too_many_cases["cases"][0]
        too_many_cases["cases"] = [
            {**template, "caseId": f"case-{index}"}
            for index in range(MAX_EVALUATION_CASES + 1)
        ]
        mutations.append(too_many_cases)

        for document in mutations:
            with self.subTest(case=document["cases"][0]["caseId"]):
                with self.assertRaises(SemanticRetrievalEvaluationError):
                    evaluate_semantic_retrieval(document)

    def test_passing_report_validator_rejects_tampering(self):
        report = evaluate_semantic_retrieval(passing_document())
        mutations = (
            ("evaluationVersion", "other"),
            ("status", "FAIL"),
            ("caseCount", 5),
            ("failureCodes", ["OTHER"]),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                changed = json.loads(json.dumps(report))
                changed[field] = value
                with self.assertRaises(SemanticRetrievalEvaluationError):
                    validate_passing_evaluation_report(changed)

        changed = json.loads(json.dumps(report))
        changed["metrics"]["macroRecallAtK"] = 0.0
        with self.assertRaises(SemanticRetrievalEvaluationError):
            validate_passing_evaluation_report(changed)

        changed = json.loads(json.dumps(report))
        changed["violations"]["tenantLeakCount"] = 1
        with self.assertRaises(SemanticRetrievalEvaluationError):
            validate_passing_evaluation_report(changed)

    def test_cli_writes_pass_report_and_uses_distinct_gate_exit(self):
        executable = BIN_DIR / "evaluate-semantic-retrieval"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "report.json"
            input_path.write_text(json.dumps(passing_document()))
            passed = subprocess.run(
                [sys.executable, str(executable), str(input_path),
                 "--output", str(output_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(passed.returncode, 0)
            self.assertEqual(json.loads(passed.stdout)["status"], "PASS")
            self.assertEqual(json.loads(output_path.read_text()), json.loads(passed.stdout))

            failed_document = passing_document()
            failed_document["cases"][0]["results"] = []
            input_path.write_text(json.dumps(failed_document))
            failed = subprocess.run(
                [sys.executable, str(executable), str(input_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stdout)["status"], "FAIL")

    def test_cli_sanitizes_invalid_input_errors(self):
        executable = BIN_DIR / "evaluate-semantic-retrieval"
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.json"
            input_path.write_text('{"private":"journal secret"}')
            result = subprocess.run(
                [sys.executable, str(executable), str(input_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "Semantic retrieval evaluation input is invalid.\n",
        )
        self.assertNotIn("journal secret", result.stderr)


if __name__ == "__main__":
    unittest.main()
