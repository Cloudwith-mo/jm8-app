from __future__ import annotations

import copy
import json
from pathlib import Path
import stat
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
FUNCTION_DIR = BACKEND_ROOT / "function"
sys.path.insert(0, str(BIN_DIR))
sys.path.insert(0, str(FUNCTION_DIR))

from jm8_semantic_embedding_activation import (  # noqa: E402
    SemanticEmbeddingActivationError,
    validate_activation_readiness,
)
from semantic_retrieval_evaluation import (  # noqa: E402
    SEMANTIC_RETRIEVAL_EVALUATION_VERSION,
    evaluate_semantic_retrieval,
)


APP = "journalm8"
STAGE = "prod"
REGION = "us-east-1"
ACCOUNT = "114743615542"
PARTITION = "aws"
TABLE_NAME = "journalm8-prod-entry-chunks"
TABLE_ARN = f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{TABLE_NAME}"
STREAM_ARN = f"{TABLE_ARN}/stream/2026-09-08T14:20:40.733"
INDEX_ARN = f"{TABLE_ARN}/index/SemanticEmbeddingIndex"
WORKER_NAME = "journalm8-prod-semantic-embedding-worker"
WORKER_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{WORKER_NAME}"
ROLE_NAME = f"{WORKER_NAME}-role"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
POLICY_NAME = f"{WORKER_NAME}-access"
DLQ_NAME = "journalm8-prod-semantic-embedding-dlq"
DLQ_ARN = f"arn:aws:sqs:{REGION}:{ACCOUNT}:{DLQ_NAME}"
MODEL_ARN = (
    f"arn:aws:bedrock:{REGION}::foundation-model/"
    "amazon.titan-embed-text-v2:0"
)


def passing_report():
    result = lambda entry, tenant="tenant-a": {
        "entryLabel": entry,
        "tenantLabel": tenant,
        "current": True,
    }
    return evaluate_semantic_retrieval({
        "evaluationVersion": SEMANTIC_RETRIEVAL_EVALUATION_VERSION,
        "cases": [
            {"caseId": "case-a", "tenantLabel": "tenant-a",
             "relevantEntryLabels": ["entry-a"], "results": [result("entry-a")]},
            {"caseId": "case-b", "tenantLabel": "tenant-a",
             "relevantEntryLabels": ["entry-b"], "results": [result("entry-b")]},
            {"caseId": "case-c", "tenantLabel": "tenant-a",
             "relevantEntryLabels": ["entry-c"], "results": [result("entry-c")]},
            {"caseId": "empty", "tenantLabel": "tenant-empty",
             "relevantEntryLabels": [], "results": []},
        ],
    })


def documents(mapping_state="Disabled"):
    table = {
        "Table": {
            "TableName": TABLE_NAME,
            "TableArn": TABLE_ARN,
            "TableStatus": "ACTIVE",
            "LatestStreamArn": STREAM_ARN,
            "StreamSpecification": {
                "StreamEnabled": True,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
            "VectorIndexes": [{
                "IndexName": "SemanticEmbeddingIndex",
                "IndexArn": INDEX_ARN,
                "IndexStatus": "ACTIVE",
                "Backfilling": False,
                "VectorAttribute": {"AttributeName": "embedding"},
                "SearchSchema": [{
                    "AttributeName": "embeddingPartition",
                    "SearchSchemaElementType": "HASH",
                }],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
                "Dimensions": 1024,
                "DistanceFunction": "COSINE",
            }],
        }
    }
    worker = {
        "FunctionName": WORKER_NAME,
        "FunctionArn": WORKER_ARN,
        "Runtime": "python3.12",
        "Architectures": ["arm64"],
        "Handler": "semantic_embedding_worker.lambda_handler",
        "Role": ROLE_ARN,
        "Timeout": 120,
        "MemorySize": 512,
        "State": "Active",
        "LastUpdateStatus": "Successful",
        "Environment": {"Variables": {"ENTRY_CHUNKS_TABLE_NAME": TABLE_NAME}},
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ReadExactEntryChunksStream", "Effect": "Allow",
             "Action": ["dynamodb:DescribeStream", "dynamodb:GetRecords",
                        "dynamodb:GetShardIterator"], "Resource": STREAM_ARN},
            {"Sid": "ListEntryChunksStreams", "Effect": "Allow",
             "Action": ["dynamodb:ListStreams"], "Resource": "*"},
            {"Sid": "ReadAndPersistExactEntryChunks", "Effect": "Allow",
             "Action": ["dynamodb:GetItem", "dynamodb:Query",
                        "dynamodb:TransactWriteItems"],
             "Resource": TABLE_ARN},
            {"Sid": "InvokeExactEmbeddingModel", "Effect": "Allow",
             "Action": ["bedrock:InvokeModel"], "Resource": MODEL_ARN},
            {"Sid": "SendExactSemanticEmbeddingDlq", "Effect": "Allow",
             "Action": ["sqs:SendMessage"], "Resource": DLQ_ARN},
        ],
    }
    mappings = {"EventSourceMappings": [{
        "UUID": "e254527e-293c-4ea8-a88a-d23cc2d5e30c",
        "EventSourceArn": STREAM_ARN,
        "FunctionArn": WORKER_ARN,
        "State": mapping_state,
        "StartingPosition": "LATEST",
        "BatchSize": 10,
        "MaximumBatchingWindowInSeconds": 1,
        "ParallelizationFactor": 1,
        "BisectBatchOnFunctionError": True,
        "MaximumRetryAttempts": 5,
        "MaximumRecordAgeInSeconds": 3600,
        "FunctionResponseTypes": ["ReportBatchItemFailures"],
        "DestinationConfig": {"OnFailure": {"Destination": DLQ_ARN}},
    }]}
    queue = {"Attributes": {
        "QueueArn": DLQ_ARN,
        "KmsMasterKeyId": "alias/aws/sqs",
        "MessageRetentionPeriod": "1209600",
        "ApproximateNumberOfMessages": "0",
        "ApproximateNumberOfMessagesNotVisible": "0",
        "ApproximateNumberOfMessagesDelayed": "0",
    }}
    alarms = {"MetricAlarms": []}
    alarm_specs = (
        (f"{WORKER_NAME}-errors", "AWS/Lambda", "Errors", "Sum", 1.0,
         "FunctionName", WORKER_NAME),
        (f"{WORKER_NAME}-throttles", "AWS/Lambda", "Throttles", "Sum", 1.0,
         "FunctionName", WORKER_NAME),
        (f"{WORKER_NAME}-iterator-age", "AWS/Lambda", "IteratorAge", "Maximum",
         300000.0, "FunctionName", WORKER_NAME),
        (f"{WORKER_NAME}-record-failures", "JournalM8/SemanticEmbedding",
         "RecordFailures", "Sum", 1.0, "FunctionName", WORKER_NAME),
        (f"{DLQ_NAME}-visible-messages", "AWS/SQS",
         "ApproximateNumberOfMessagesVisible", "Maximum", 1.0,
         "QueueName", DLQ_NAME),
    )
    for name, namespace, metric, statistic, threshold, dimension, value in alarm_specs:
        alarms["MetricAlarms"].append({
            "AlarmName": name,
            "Namespace": namespace,
            "MetricName": metric,
            "Statistic": statistic,
            "Period": 300,
            "EvaluationPeriods": 1,
            "Threshold": threshold,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "TreatMissingData": "notBreaching",
            "StateValue": "OK",
            "Dimensions": [{"Name": dimension, "Value": value}],
        })
    return {
        "caller_identity": {"Account": ACCOUNT,
                            "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/prod/session"},
        "table_document": table,
        "worker_configuration": worker,
        "worker_concurrency": {"ReservedConcurrentExecutions": 1},
        "worker_policy_response": {"RoleName": ROLE_NAME,
                                   "PolicyName": POLICY_NAME,
                                   "PolicyDocument": policy},
        "mappings_document": mappings,
        "queue_attributes": queue,
        "alarms_document": alarms,
    }


def validate(values, *, target="Enabled", mode="canary", report=None):
    return validate_activation_readiness(
        **values,
        evaluation_report=report,
        target_state=target,
        activation_mode=mode,
        app_name=APP,
        stage=STAGE,
        region=REGION,
        account_id=ACCOUNT,
    )


class SemanticEmbeddingActivationTests(unittest.TestCase):
    def test_canary_enable_and_idempotent_enabled_state(self):
        self.assertEqual(validate(documents()), "ENABLE")
        self.assertEqual(validate(documents("Enabled")), "NOOP")

    def test_disable_is_safe_and_idempotent_without_report(self):
        self.assertEqual(
            validate(documents("Enabled"), target="Disabled", mode="disable"),
            "DISABLE",
        )
        self.assertEqual(
            validate(documents(), target="Disabled", mode="disable"),
            "NOOP",
        )

    def test_disable_remains_available_during_an_embedding_incident(self):
        values = documents("Enabled")
        values["table_document"]["Table"]["VectorIndexes"] = []
        values["worker_configuration"] = {"State": "Failed"}
        values["worker_concurrency"] = {}
        values["worker_policy_response"] = {}
        values["queue_attributes"]["Attributes"][
            "ApproximateNumberOfMessages"
        ] = "3"
        values["alarms_document"]["MetricAlarms"][3][
            "StateValue"
        ] = "ALARM"

        self.assertEqual(
            validate(values, target="Disabled", mode="disable"),
            "DISABLE",
        )

    def test_disable_still_requires_exact_target_identity(self):
        mutations = []

        value = documents("Enabled")
        value["caller_identity"]["Account"] = "000000000000"
        mutations.append(value)

        value = documents("Enabled")
        value["table_document"]["Table"]["TableArn"] = "invalid"
        mutations.append(value)

        value = documents("Enabled")
        value["mappings_document"]["EventSourceMappings"][0][
            "FunctionArn"
        ] = "invalid"
        mutations.append(value)

        for index, value in enumerate(mutations):
            with self.subTest(boundary=index):
                with self.assertRaises(SemanticEmbeddingActivationError):
                    validate(value, target="Disabled", mode="disable")

    def test_steady_requires_enabled_mapping_and_passing_report(self):
        report = passing_report()
        self.assertEqual(
            validate(documents("Enabled"), mode="steady", report=report),
            "NOOP",
        )
        with self.assertRaises(SemanticEmbeddingActivationError):
            validate(documents(), mode="steady", report=report)
        with self.assertRaises(SemanticEmbeddingActivationError):
            validate(documents("Enabled"), mode="steady")

        failed = passing_report()
        failed["status"] = "FAIL"
        with self.assertRaises(SemanticEmbeddingActivationError):
            validate(documents("Enabled"), mode="steady", report=failed)

    def test_modes_cannot_bypass_their_intended_evidence(self):
        with self.assertRaises(SemanticEmbeddingActivationError):
            validate(documents(), mode="canary", report=passing_report())
        with self.assertRaises(SemanticEmbeddingActivationError):
            validate(documents(), target="Disabled", mode="disable",
                     report=passing_report())
        with self.assertRaises(SemanticEmbeddingActivationError):
            validate(documents(), target="Enabled", mode="disable")

    def test_every_operational_boundary_fails_closed(self):
        mutations = []

        value = documents()
        value["caller_identity"]["Account"] = "000000000000"
        mutations.append(value)

        value = documents()
        value["table_document"]["Table"]["VectorIndexes"][0]["Dimensions"] = 768
        mutations.append(value)

        value = documents()
        value["worker_configuration"]["Runtime"] = "python3.14"
        mutations.append(value)

        value = documents()
        value["worker_concurrency"]["ReservedConcurrentExecutions"] = 2
        mutations.append(value)

        value = documents()
        value["worker_policy_response"]["PolicyDocument"]["Statement"][3]["Resource"] = "*"
        mutations.append(value)

        value = documents()
        value["mappings_document"]["EventSourceMappings"][0]["BatchSize"] = 100
        mutations.append(value)

        value = documents()
        value["queue_attributes"]["Attributes"]["ApproximateNumberOfMessages"] = "1"
        mutations.append(value)

        value = documents()
        value["alarms_document"]["MetricAlarms"][0]["StateValue"] = "ALARM"
        mutations.append(value)

        for index, value in enumerate(mutations):
            with self.subTest(boundary=index):
                with self.assertRaises(SemanticEmbeddingActivationError):
                    validate(value)

    def test_policy_url_encoding_is_supported_but_wildcards_are_rejected(self):
        values = documents()
        policy = values["worker_policy_response"]["PolicyDocument"]
        from urllib.parse import quote
        values["worker_policy_response"]["PolicyDocument"] = quote(
            json.dumps(policy)
        )
        self.assertEqual(validate(values), "ENABLE")

        values = documents()
        values["worker_policy_response"]["PolicyDocument"]["Statement"][2]["Resource"] = "*"
        with self.assertRaises(SemanticEmbeddingActivationError):
            validate(values)

    def test_controller_is_executable_narrow_and_has_failure_rollback(self):
        path = BIN_DIR / "set-semantic-embedding-ingestion"
        source = path.read_text(encoding="utf-8")
        self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        self.assertIn('export JM8_OPERATION="deploy-semantic-embedding"', source)
        self.assertIn("SEMANTIC_EMBEDDING_CANARY_CONFIRMATION", source)
        self.assertIn("SEMANTIC_RETRIEVAL_EVALUATION_REPORT", source)
        self.assertIn("SEMANTIC_EMBEDDING_ACTIVATION_EXECUTE", source)
        self.assertIn('ACTIVATION_EXECUTE="${SEMANTIC_EMBEDDING_ACTIVATION_EXECUTE:-false}"', source)
        self.assertIn("No mapping state was changed.", source)
        self.assertIn(
            "evaluation report is not accepted in canary mode",
            source,
        )
        self.assertIn("ACTIVATED_BY_THIS_RUN", source)
        self.assertIn("attempting to disable the mapping", source)
        self.assertIn('if [ "$TARGET_STATE" = Disabled ]', source)
        self.assertIn("return", source)
        self.assertEqual(source.count("aws lambda update-event-source-mapping"), 3)
        for forbidden in (
            "aws lambda update-function-code",
            "aws lambda update-function-configuration",
            "aws lambda invoke",
            "aws iam put-role-policy",
            "aws dynamodb update-table",
            "aws dynamodb put-item",
            "aws dynamodb update-item",
            "aws dynamodb delete-item",
            "aws bedrock-runtime",
            "aws cloudwatch put-metric-alarm",
        ):
            self.assertNotIn(forbidden, source)

    def test_validation_does_not_expose_resource_details(self):
        values = documents()
        values["worker_policy_response"]["PolicyDocument"] = {
            "private": "journal-content"
        }
        with self.assertRaises(SemanticEmbeddingActivationError) as raised:
            validate(values)
        self.assertNotIn("journal-content", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
