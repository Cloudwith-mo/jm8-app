from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "function"))
sys.path.insert(0, str(BACKEND_ROOT / "bin"))


from jm8_environment_contract import (  # noqa: E402
    EnvironmentContractError,
    validate_entry_chunks_table_description,
    validate_entry_chunks_vector_index_description,
)
from semantic_embedding_contract import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    EMBEDDING_DISTANCE_FUNCTION,
    EMBEDDING_INDEX_NAME,
    EMBEDDING_PARTITION_ATTRIBUTE,
    EMBEDDING_VECTOR_ATTRIBUTE,
)
from semantic_vector_index_contract import (  # noqa: E402
    SemanticVectorIndexContractError,
    validate_vector_index_state,
    vector_index_create_definition,
)


TABLE_NAME = "journalm8-prod-entry-chunks"
TABLE_ARN = (
    "arn:aws:dynamodb:us-east-1:114743615542:table/"
    "journalm8-prod-entry-chunks"
)


def table_document(*, with_index: bool, status: str = "ACTIVE"):
    table = {
        "TableName": TABLE_NAME,
        "TableArn": TABLE_ARN,
        "TableStatus": status,
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
        "DeletionProtectionEnabled": True,
    }
    if with_index:
        table["AttributeDefinitions"].append({
            "AttributeName": "embeddingPartition",
            "AttributeType": "S",
        })
        table["VectorIndexes"] = [{
            **vector_index_create_definition(),
            "IndexArn": f"{TABLE_ARN}/index/SemanticEmbeddingIndex",
            "IndexStatus": "ACTIVE",
            "Backfilling": False,
            "ItemCount": 1,
            "IndexSizeBytes": 4200,
        }]
    return {"Table": table}


class SemanticVectorIndexContractTests(unittest.TestCase):
    def test_create_definition_is_bound_to_embedding_contract(self):
        self.assertEqual(vector_index_create_definition(), {
            "IndexName": EMBEDDING_INDEX_NAME,
            "VectorAttribute": {"AttributeName": EMBEDDING_VECTOR_ATTRIBUTE},
            "SearchSchema": [{
                "AttributeName": EMBEDDING_PARTITION_ATTRIBUTE,
                "SearchSchemaElementType": "HASH",
            }],
            "Projection": {"ProjectionType": "KEYS_ONLY"},
            "Dimensions": EMBEDDING_DIMENSIONS,
            "DistanceFunction": EMBEDDING_DISTANCE_FUNCTION,
        })

    def test_absent_creating_and_active_states_are_classified(self):
        absent = table_document(with_index=False)
        self.assertEqual(
            validate_vector_index_state(absent, table_arn=TABLE_ARN),
            {"action": "CREATE", "indexArn": None},
        )
        self.assertEqual(
            validate_entry_chunks_vector_index_description(
                absent,
                app_name="journalm8",
                stage="prod",
                account_id="114743615542",
                region="us-east-1",
                table_name=TABLE_NAME,
            ),
            "CREATE",
        )

        creating = table_document(with_index=True, status="UPDATING")
        creating["Table"]["VectorIndexes"][0].update({
            "IndexStatus": "CREATING",
            "Backfilling": True,
        })
        self.assertEqual(
            validate_vector_index_state(creating, table_arn=TABLE_ARN)["action"],
            "WAIT",
        )
        self.assertEqual(
            validate_entry_chunks_vector_index_description(
                creating,
                app_name="journalm8",
                stage="prod",
                account_id="114743615542",
                region="us-east-1",
                table_name=TABLE_NAME,
            ),
            "WAIT",
        )

        active = table_document(with_index=True)
        self.assertEqual(
            validate_vector_index_state(active, table_arn=TABLE_ARN)["action"],
            "READY",
        )
        self.assertEqual(
            validate_entry_chunks_vector_index_description(
                active,
                app_name="journalm8",
                stage="prod",
                account_id="114743615542",
                region="us-east-1",
                table_name=TABLE_NAME,
            ),
            "READY",
        )
        validate_entry_chunks_table_description(
            active,
            app_name="journalm8",
            stage="prod",
            account_id="114743615542",
            region="us-east-1",
            table_name=TABLE_NAME,
            require_deletion_protection=True,
        )

    def test_incompatible_or_extra_indexes_fail_closed(self):
        mutations = (
            ("IndexName", "OtherIndex"),
            ("VectorAttribute", "otherVector"),
            ("Dimensions", 512),
            ("DistanceFunction", "EUCLIDEAN"),
            ("SearchSchema", []),
            ("Projection", {"ProjectionType": "ALL"}),
            ("IndexStatus", "DELETING"),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                document = table_document(with_index=True)
                document["Table"]["VectorIndexes"][0][key] = value
                with self.assertRaises(SemanticVectorIndexContractError):
                    validate_vector_index_state(document, table_arn=TABLE_ARN)
                with self.assertRaises(EnvironmentContractError):
                    validate_entry_chunks_vector_index_description(
                        document,
                        app_name="journalm8",
                        stage="prod",
                        account_id="114743615542",
                        region="us-east-1",
                        table_name=TABLE_NAME,
                    )

        extra = table_document(with_index=True)
        extra["Table"]["VectorIndexes"].append(
            copy.deepcopy(extra["Table"]["VectorIndexes"][0])
        )
        with self.assertRaises(SemanticVectorIndexContractError):
            validate_vector_index_state(extra, table_arn=TABLE_ARN)

    def test_provisioning_requires_on_demand_billing_and_exact_table(self):
        for field, value in (
            ("TableArn", TABLE_ARN.replace("114743615542", "999999999999")),
            ("TableStatus", "DELETING"),
            ("BillingModeSummary", {"BillingMode": "PROVISIONED"}),
        ):
            with self.subTest(field=field):
                document = table_document(with_index=False)
                document["Table"][field] = value
                with self.assertRaises(EnvironmentContractError):
                    validate_entry_chunks_vector_index_description(
                        document,
                        app_name="journalm8",
                        stage="prod",
                        account_id="114743615542",
                        region="us-east-1",
                        table_name=TABLE_NAME,
                    )


if __name__ == "__main__":
    unittest.main()
