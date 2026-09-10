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
BEDROCK = object()
PREVIOUS_TABLE_NAME = os.environ.get("ENTRY_CHUNKS_TABLE_NAME")
os.environ["ENTRY_CHUNKS_TABLE_NAME"] = "journalm8-test-entry-chunks"
with patch.object(boto3, "resource") as resource, patch.object(
    boto3, "client"
) as client:
    resource.return_value.Table.return_value = TABLE
    client.return_value = BEDROCK
    sys.modules.pop("semantic_embedding_worker", None)
    worker = importlib.import_module("semantic_embedding_worker")
    RESOURCE_CALLS = list(resource.call_args_list)
    TABLE_CALLS = list(resource.return_value.Table.call_args_list)
    CLIENT_CALLS = list(client.call_args_list)
if PREVIOUS_TABLE_NAME is None:
    os.environ.pop("ENTRY_CHUNKS_TABLE_NAME", None)
else:
    os.environ["ENTRY_CHUNKS_TABLE_NAME"] = PREVIOUS_TABLE_NAME


def telemetry(*, failures: int) -> dict[str, object]:
    return {
        "recordCount": 1,
        "failureCount": failures,
        "failureCounts": {
            "MemoryReadFailures": failures,
            "ProviderFailures": 0,
            "PersistenceFailures": 0,
            "ContractFailures": 0,
            "UnexpectedFailures": 0,
        },
    }


class SemanticEmbeddingWorkerTests(unittest.TestCase):
    def test_cold_start_creates_only_exact_runtime_dependencies(self):
        self.assertEqual([call.args for call in RESOURCE_CALLS], [("dynamodb",)])
        self.assertEqual(
            [call.args for call in TABLE_CALLS],
            [("journalm8-test-entry-chunks",)],
        )
        self.assertEqual([call.args for call in CLIENT_CALLS], [("bedrock-runtime",)])
        self.assertIs(worker.entry_chunks_table, TABLE)
        self.assertIs(worker.bedrock_client, BEDROCK)

    def test_handler_delegates_and_returns_partial_batch_response(self):
        event = {"Records": [{
            "eventID": "private-event",
            "dynamodb": {"SequenceNumber": "private-sequence"},
        }]}
        expected = {
            "batchItemFailures": [{"itemIdentifier": "private-sequence"}]
        }
        output = io.StringIO()
        with patch.object(
            worker,
            "process_embedding_stream_event_with_telemetry",
            return_value=(expected, telemetry(failures=1)),
        ) as process, patch.object(
            worker.time,
            "time",
            return_value=123.456,
        ), patch.dict(
            os.environ,
            {"AWS_LAMBDA_FUNCTION_NAME": "journalm8-test-worker"},
        ), patch("sys.stdout", output):
            result = worker.lambda_handler(event, object())

        process.assert_called_once_with(TABLE, BEDROCK, event)
        self.assertIs(result, expected)
        logged = json.loads(output.getvalue())
        self.assertEqual(logged["RecordCount"], 1)
        self.assertEqual(logged["RecordFailures"], 1)
        self.assertEqual(logged["MemoryReadFailures"], 1)
        self.assertEqual(logged["FunctionName"], "journalm8-test-worker")
        self.assertEqual(logged["_aws"]["Timestamp"], 123456)

    def test_operational_log_is_aggregate_and_privacy_safe(self):
        secrets = (
            "journal text must not be logged",
            "private-user",
            "private-entry",
            "private-digest",
            "private-sequence",
            "private-event",
        )
        event = {"Records": [{
            "eventID": secrets[5],
            "dynamodb": {
                "SequenceNumber": secrets[4],
                "NewImage": {
                    "userId": {"S": secrets[1]},
                    "entryId": {"S": secrets[2]},
                    "text": {"S": secrets[0]},
                    "contentDigest": {"S": secrets[3]},
                },
            },
        }]}
        output = io.StringIO()
        with patch.object(
            worker,
            "process_embedding_stream_event_with_telemetry",
            return_value=(
                {"batchItemFailures": []},
                telemetry(failures=0),
            ),
        ), patch("sys.stdout", output):
            worker.lambda_handler(event, None)

        logged = output.getvalue()
        payload = json.loads(logged)
        self.assertEqual(
            set(payload) - {"_aws", "event", "FunctionName"},
            {
                "RecordCount",
                "RecordFailures",
                "MemoryReadFailures",
                "ProviderFailures",
                "PersistenceFailures",
                "ContractFailures",
                "UnexpectedFailures",
            },
        )
        for secret in secrets:
            self.assertNotIn(secret, logged)

    def test_processor_exception_is_not_logged_or_rewritten(self):
        failure = RuntimeError("private provider failure")
        output = io.StringIO()
        with patch.object(
            worker,
            "process_embedding_stream_event_with_telemetry",
            side_effect=failure,
        ), patch("sys.stdout", output), self.assertRaises(RuntimeError) as raised:
            worker.lambda_handler({"Records": []}, None)
        self.assertIs(raised.exception, failure)
        self.assertEqual(output.getvalue(), "")

    def test_missing_or_blank_table_name_fails_before_aws_dependency(self):
        script = (
            "import sys; "
            f"sys.path.insert(0, {str(FUNCTION_DIR)!r}); "
            "import semantic_embedding_worker"
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
                self.assertIn(
                    "SemanticEmbeddingWorkerConfigurationError",
                    combined,
                )
                self.assertNotIn("journal text", combined.lower())


if __name__ == "__main__":
    unittest.main()
