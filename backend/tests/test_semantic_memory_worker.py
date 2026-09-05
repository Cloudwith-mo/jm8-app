from __future__ import annotations

import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import boto3


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))


TABLE = object()
PREVIOUS_ENTRY_CHUNKS_TABLE_NAME = os.environ.get("ENTRY_CHUNKS_TABLE_NAME")
os.environ["ENTRY_CHUNKS_TABLE_NAME"] = "journalm8-test-entry-chunks"
with patch.object(boto3, "resource") as resource:
    resource.return_value.Table.return_value = TABLE
    sys.modules.pop("semantic_memory_worker", None)
    worker = importlib.import_module("semantic_memory_worker")
    COLD_START_RESOURCE_CALLS = list(resource.call_args_list)
    COLD_START_TABLE_CALLS = list(resource.return_value.Table.call_args_list)
if PREVIOUS_ENTRY_CHUNKS_TABLE_NAME is None:
    os.environ.pop("ENTRY_CHUNKS_TABLE_NAME", None)
else:
    os.environ["ENTRY_CHUNKS_TABLE_NAME"] = PREVIOUS_ENTRY_CHUNKS_TABLE_NAME


class SemanticMemoryWorkerTests(unittest.TestCase):
    def test_cold_start_creates_only_the_dedicated_table_dependency(self):
        self.assertEqual(len(COLD_START_RESOURCE_CALLS), 1)
        self.assertEqual(COLD_START_RESOURCE_CALLS[0].args, ("dynamodb",))
        self.assertEqual(len(COLD_START_TABLE_CALLS), 1)
        self.assertEqual(
            COLD_START_TABLE_CALLS[0].args,
            ("journalm8-test-entry-chunks",),
        )
        self.assertIs(worker.entry_chunks_table, TABLE)

    def test_handler_delegates_and_returns_exact_partial_batch_response(self):
        event = {"Records": [{
            "eventID": "private-event-id",
            "dynamodb": {"SequenceNumber": "private-sequence"},
        }]}
        expected = {
            "batchItemFailures": [{"itemIdentifier": "private-sequence"}]
        }
        output = io.StringIO()
        with patch.object(
            worker,
            "process_entry_stream_event",
            return_value=expected,
        ) as process, patch("sys.stdout", output):
            result = worker.lambda_handler(event, object())

        process.assert_called_once_with(TABLE, event)
        self.assertIs(result, expected)
        self.assertEqual(json.loads(output.getvalue()), {
            "event": "SemanticMemoryLifecycleBatch",
            "processedCount": 1,
            "failedCount": 1,
        })

    def test_operational_log_contains_only_safe_aggregate_fields(self):
        secrets = (
            "journal text must not be logged",
            "user-private",
            "entry-private",
            "digest-private",
            "sequence-private",
            "event-private",
        )
        event = {"Records": [{
            "eventID": secrets[5],
            "dynamodb": {
                "SequenceNumber": secrets[4],
                "NewImage": {
                    "userId": {"S": secrets[1]},
                    "entryId": {"S": secrets[2]},
                    "rawText": {"S": secrets[0]},
                    "contentDigest": {"S": secrets[3]},
                },
            },
        }]}
        output = io.StringIO()
        with patch.object(
            worker,
            "process_entry_stream_event",
            return_value={"batchItemFailures": []},
        ), patch("sys.stdout", output):
            worker.lambda_handler(event, None)

        logged = output.getvalue()
        self.assertEqual(
            set(json.loads(logged)),
            {"event", "processedCount", "failedCount"},
        )
        for secret in secrets:
            self.assertNotIn(secret, logged)

    def test_processor_exception_is_not_logged_or_rewritten(self):
        output = io.StringIO()
        failure = RuntimeError("private raw exception")
        with patch.object(
            worker,
            "process_entry_stream_event",
            side_effect=failure,
        ), patch("sys.stdout", output), self.assertRaises(RuntimeError) as raised:
            worker.lambda_handler({"Records": []}, None)
        self.assertIs(raised.exception, failure)
        self.assertEqual(output.getvalue(), "")

    def test_missing_or_blank_configuration_fails_before_aws_dependency(self):
        script = (
            "import sys; "
            f"sys.path.insert(0, {str(FUNCTION_DIR)!r}); "
            "import semantic_memory_worker"
        )
        for value in (None, "", "   "):
            environment = dict(os.environ)
            if value is None:
                environment.pop("ENTRY_CHUNKS_TABLE_NAME", None)
            else:
                environment["ENTRY_CHUNKS_TABLE_NAME"] = value
            result = subprocess.run(
                [sys.executable, "-c", script],
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
            with self.subTest(value=value):
                self.assertNotEqual(result.returncode, 0)
                combined = result.stdout + result.stderr
                self.assertIn("SemanticMemoryWorkerConfigurationError", combined)
                self.assertNotIn("journal", combined.lower())

    def test_source_has_no_main_table_or_other_service_dependency(self):
        source = (FUNCTION_DIR / "semantic_memory_worker.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "TABLE_NAME",
            "RAW_BUCKET",
            "EXPORT_BUCKET",
            "bedrock",
            "cognito",
            "stepfunctions",
            "invoke(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source.replace(
                    "ENTRY_CHUNKS_TABLE_NAME", "ENTRY_CHUNKS"
                ))


if __name__ == "__main__":
    unittest.main()
