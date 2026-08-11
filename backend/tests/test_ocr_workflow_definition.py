import json
import unittest
from pathlib import Path


class OcrWorkflowDefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        definition_path = (
            Path(__file__).parents[1] / "infra" / "ocr-workflow.asl.json"
        )
        cls.definition = json.loads(definition_path.read_text())

    def test_retryable_worker_errors_use_bounded_backoff(self):
        retries = self.definition["States"]["RunOcrWorker"]["Retry"]
        retry = next(
            item
            for item in retries
            if "OcrRetryableError" in item["ErrorEquals"]
        )

        self.assertEqual(retry["MaxAttempts"], 3)
        self.assertEqual(retry["BackoffRate"], 2)
        self.assertEqual(retry["JitterStrategy"], "FULL")

    def test_all_terminal_failures_are_recorded(self):
        worker = self.definition["States"]["RunOcrWorker"]
        self.assertEqual(worker["Catch"][0]["ErrorEquals"], ["States.ALL"])
        self.assertEqual(worker["Catch"][0]["Next"], "RecordWorkflowFailure")


if __name__ == "__main__":
    unittest.main()
