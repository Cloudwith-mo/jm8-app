import json
import os
import unittest
from datetime import (
    datetime,
    timezone,
)

from boto3.dynamodb.types import (
    TypeDeserializer,
)
from botocore.exceptions import (
    ClientError,
)


os.environ.setdefault(
    "AWS_ACCESS_KEY_ID",
    "testing",
)

os.environ.setdefault(
    "AWS_SECRET_ACCESS_KEY",
    "testing",
)

os.environ.setdefault(
    "AWS_DEFAULT_REGION",
    "us-east-1",
)

os.environ.setdefault(
    "AWS_EC2_METADATA_DISABLED",
    "true",
)

os.environ.setdefault(
    "TABLE_NAME",
    "journalm8-test-main",
)

os.environ.setdefault(
    "RAW_BUCKET",
    "journalm8-test-raw",
)


from billing_customer_store import (  # noqa: E402
    BILLING_CUSTOMER_ENTITY_TYPE,
    BILLING_CUSTOMER_LOOKUP_ENTITY_TYPE,
    BILLING_CUSTOMER_MAPPING_VERSION,
    BILLING_CUSTOMER_SK,
    BillingCustomerConflictError,
    BillingCustomerStoreError,
    billing_customer_key,
    build_customer_mapping_items,
    build_customer_mapping_transaction_token,
    create_stripe_customer_mapping,
    get_billing_user_for_customer,
    get_stripe_customer_mapping,
    normalize_customer_mapping,
    normalize_customer_store_user_id,
    normalize_stored_customer_id,
    normalize_stored_user_reference,
    stripe_customer_lookup_key,
)


FIXED_NOW = datetime(
    2026,
    7,
    27,
    18,
    0,
    tzinfo=timezone.utc,
)

TEST_USER = "test-user"

OTHER_USER = "other-user"

TEST_CUSTOMER = (
    "cus_customerstore123"
)

OTHER_CUSTOMER = (
    "cus_othercustomer123"
)

TEST_REFERENCE = (
    "jm8usr_"
    "1234567890abcdef"
    "1234567890abcdef"
)

OTHER_REFERENCE = (
    "jm8usr_"
    "abcdef1234567890"
    "abcdef1234567890"
)


def client_error(
    code,
    *,
    operation,
    cancellation_codes=None,
):
    response = {
        "Error": {
            "Code": code,
            "Message": (
                "Synthetic private "
                "DynamoDB detail."
            ),
        }
    }

    if cancellation_codes is not None:
        response[
            "CancellationReasons"
        ] = [
            {
                "Code": item,
                "Message": (
                    "Synthetic private "
                    "transaction detail."
                ),
            }
            for item in cancellation_codes
        ]

    return ClientError(
        response,
        operation,
    )


def stored_items(
    *,
    user_id=TEST_USER,
    customer_id=TEST_CUSTOMER,
    livemode=False,
    user_reference=TEST_REFERENCE,
):
    return build_customer_mapping_items(
        user_id=user_id,
        customer_id=customer_id,
        livemode=livemode,
        user_reference=user_reference,
        now=FIXED_NOW,
    )


class FakeTable:
    def __init__(
        self,
        *,
        items=None,
        error=None,
    ):
        self.items = dict(
            items or {}
        )

        self.error = error
        self.calls = []

    def get_item(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        if self.error:
            raise self.error

        key = kwargs["Key"]

        item = self.items.get(
            (
                key["PK"],
                key["SK"],
            )
        )

        if item is None:
            return {}

        return {
            "Item": dict(item)
        }


class FakeClient:
    def __init__(
        self,
        *,
        error=None,
    ):
        self.error = error
        self.calls = []

    def transact_write_items(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        if self.error:
            raise self.error

        return {}


DESERIALIZER = TypeDeserializer()


def deserialize_item(
    item,
):
    return {
        key: DESERIALIZER.deserialize(
            value
        )
        for key, value in item.items()
    }


def fake_table_with_items(
    mapping_item,
    lookup_item,
):
    return FakeTable(
        items={
            (
                mapping_item["PK"],
                mapping_item["SK"],
            ):
                mapping_item,

            (
                lookup_item["PK"],
                lookup_item["SK"],
            ):
                lookup_item,
        }
    )


class BillingCustomerStoreTests(
    unittest.TestCase
):
    def test_invalid_user_rejected(
        self,
    ):
        with self.assertRaises(
            BillingCustomerStoreError
        ):
            normalize_customer_store_user_id(
                " "
            )

    def test_invalid_customer_rejected(
        self,
    ):
        with self.assertRaises(
            BillingCustomerStoreError
        ):
            normalize_stored_customer_id(
                "invalid"
            )

    def test_invalid_reference_rejected(
        self,
    ):
        with self.assertRaises(
            BillingCustomerStoreError
        ):
            normalize_stored_user_reference(
                "raw-user-id"
            )

    def test_user_key_is_scoped(
        self,
    ):
        self.assertEqual(
            billing_customer_key(
                TEST_USER
            ),
            {
                "PK":
                    "USER#test-user",

                "SK":
                    BILLING_CUSTOMER_SK,
            },
        )

    def test_test_lookup_key_is_mode_scoped(
        self,
    ):
        key = (
            stripe_customer_lookup_key(
                TEST_CUSTOMER,
                livemode=False,
            )
        )

        self.assertIn(
            "#TEST#",
            key["PK"],
        )

    def test_live_lookup_key_is_mode_scoped(
        self,
    ):
        key = (
            stripe_customer_lookup_key(
                TEST_CUSTOMER,
                livemode=True,
            )
        )

        self.assertIn(
            "#LIVE#",
            key["PK"],
        )

    def test_different_modes_have_different_lookup_keys(
        self,
    ):
        self.assertNotEqual(
            stripe_customer_lookup_key(
                TEST_CUSTOMER,
                livemode=False,
            ),
            stripe_customer_lookup_key(
                TEST_CUSTOMER,
                livemode=True,
            ),
        )

    def test_missing_mapping_returns_none(
        self,
    ):
        self.assertIsNone(
            get_stripe_customer_mapping(
                TEST_USER,
                table_resource=FakeTable(),
            )
        )

    def test_user_mapping_read_is_strongly_consistent(
        self,
    ):
        mapping_item, _ = (
            stored_items()
        )

        fake = FakeTable(
            items={
                (
                    mapping_item["PK"],
                    mapping_item["SK"],
                ):
                    mapping_item,
            }
        )

        result = (
            get_stripe_customer_mapping(
                TEST_USER,
                table_resource=fake,
            )
        )

        self.assertIsNotNone(
            result
        )

        self.assertTrue(
            fake.calls[0][
                "ConsistentRead"
            ]
        )

    def test_reverse_lookup_read_is_strongly_consistent(
        self,
    ):
        _, lookup_item = (
            stored_items()
        )

        fake = FakeTable(
            items={
                (
                    lookup_item["PK"],
                    lookup_item["SK"],
                ):
                    lookup_item,
            }
        )

        result = (
            get_billing_user_for_customer(
                TEST_CUSTOMER,
                livemode=False,
                table_resource=fake,
            )
        )

        self.assertIsNotNone(
            result
        )

        self.assertTrue(
            fake.calls[0][
                "ConsistentRead"
            ]
        )

    def test_stored_mapping_is_allow_listed(
        self,
    ):
        mapping_item, _ = (
            stored_items()
        )

        mapping_item.update({
            "paymentMethodId":
                "private-payment-method",

            "privateMetadata": {
                "secret": True,
            },
        })

        result = (
            normalize_customer_mapping(
                mapping_item,
                expected_key=(
                    billing_customer_key(
                        TEST_USER
                    )
                ),
            )
        )

        serialized = json.dumps(
            result
        )

        self.assertNotIn(
            "PK",
            result,
        )

        self.assertNotIn(
            "SK",
            result,
        )

        self.assertNotIn(
            "paymentMethodId",
            serialized,
        )

        self.assertNotIn(
            "privateMetadata",
            serialized,
        )

    def test_invalid_mapping_owner_rejected(
        self,
    ):
        mapping_item, _ = (
            stored_items()
        )

        with self.assertRaises(
            BillingCustomerStoreError
        ):
            normalize_customer_mapping(
                mapping_item,
                expected_key=(
                    billing_customer_key(
                        OTHER_USER
                    )
                ),
            )

    def test_retryable_read_error_is_sanitized(
        self,
    ):
        private_detail = (
            "Synthetic private "
            "DynamoDB detail."
        )

        fake = FakeTable(
            error=client_error(
                "ThrottlingException",
                operation="GetItem",
            )
        )

        with self.assertRaises(
            BillingCustomerStoreError
        ) as raised:
            get_stripe_customer_mapping(
                TEST_USER,
                table_resource=fake,
            )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertNotIn(
            private_detail,
            raised.exception.message,
        )

    def test_create_writes_atomic_pair(
        self,
    ):
        client = FakeClient()

        create_stripe_customer_mapping(
            user_id=TEST_USER,
            customer_id=TEST_CUSTOMER,
            livemode=False,
            user_reference=(
                TEST_REFERENCE
            ),
            now=FIXED_NOW,
            client_resource=client,
        )

        call = client.calls[0]

        self.assertEqual(
            len(
                call["TransactItems"]
            ),
            2,
        )

        first = deserialize_item(
            call["TransactItems"][0][
                "Put"
            ]["Item"]
        )

        second = deserialize_item(
            call["TransactItems"][1][
                "Put"
            ]["Item"]
        )

        self.assertEqual(
            first["entityType"],
            BILLING_CUSTOMER_ENTITY_TYPE,
        )

        self.assertEqual(
            second["entityType"],
            (
                BILLING_CUSTOMER_LOOKUP_ENTITY_TYPE
            ),
        )

    def test_create_uses_conditional_writes(
        self,
    ):
        client = FakeClient()

        create_stripe_customer_mapping(
            user_id=TEST_USER,
            customer_id=TEST_CUSTOMER,
            livemode=False,
            user_reference=(
                TEST_REFERENCE
            ),
            now=FIXED_NOW,
            client_resource=client,
        )

        for action in client.calls[0][
            "TransactItems"
        ]:
            put = action["Put"]

            self.assertIn(
                "attribute_not_exists",
                put[
                    "ConditionExpression"
                ],
            )

    def test_transaction_token_is_stable(
        self,
    ):
        first = (
            build_customer_mapping_transaction_token(
                user_id=TEST_USER,
                customer_id=TEST_CUSTOMER,
                livemode=False,
                user_reference=(
                    TEST_REFERENCE
                ),
            )
        )

        second = (
            build_customer_mapping_transaction_token(
                user_id=TEST_USER,
                customer_id=TEST_CUSTOMER,
                livemode=False,
                user_reference=(
                    TEST_REFERENCE
                ),
            )
        )

        self.assertEqual(
            first,
            second,
        )

        self.assertLessEqual(
            len(first),
            36,
        )

        self.assertNotIn(
            TEST_USER,
            first,
        )

        self.assertNotIn(
            TEST_CUSTOMER,
            first,
        )

    def test_create_marks_new_mapping(
        self,
    ):
        result = (
            create_stripe_customer_mapping(
                user_id=TEST_USER,
                customer_id=(
                    TEST_CUSTOMER
                ),
                livemode=False,
                user_reference=(
                    TEST_REFERENCE
                ),
                now=FIXED_NOW,
                client_resource=(
                    FakeClient()
                ),
            )
        )

        self.assertTrue(
            result[
                "_createdInRequest"
            ]
        )

        self.assertEqual(
            result["mappingVersion"],
            (
                BILLING_CUSTOMER_MAPPING_VERSION
            ),
        )

    def test_duplicate_same_mapping_is_reused(
        self,
    ):
        mapping_item, lookup_item = (
            stored_items()
        )

        table_resource = (
            fake_table_with_items(
                mapping_item,
                lookup_item,
            )
        )

        client = FakeClient(
            error=client_error(
                "TransactionCanceledException",
                operation=(
                    "TransactWriteItems"
                ),
                cancellation_codes=[
                    "ConditionalCheckFailed",
                    "ConditionalCheckFailed",
                ],
            )
        )

        result = (
            create_stripe_customer_mapping(
                user_id=TEST_USER,
                customer_id=(
                    TEST_CUSTOMER
                ),
                livemode=False,
                user_reference=(
                    TEST_REFERENCE
                ),
                now=FIXED_NOW,
                table_resource=(
                    table_resource
                ),
                client_resource=client,
            )
        )

        self.assertFalse(
            result[
                "_createdInRequest"
            ]
        )

        self.assertEqual(
            result["stripeCustomerId"],
            TEST_CUSTOMER,
        )

    def test_conflicting_user_mapping_rejected(
        self,
    ):
        existing_mapping, _ = (
            stored_items(
                customer_id=(
                    OTHER_CUSTOMER
                )
            )
        )

        requested_lookup = (
            stored_items()[1]
        )

        table_resource = (
            fake_table_with_items(
                existing_mapping,
                requested_lookup,
            )
        )

        client = FakeClient(
            error=client_error(
                "TransactionCanceledException",
                operation=(
                    "TransactWriteItems"
                ),
                cancellation_codes=[
                    "ConditionalCheckFailed",
                ],
            )
        )

        with self.assertRaises(
            BillingCustomerConflictError
        ):
            create_stripe_customer_mapping(
                user_id=TEST_USER,
                customer_id=(
                    TEST_CUSTOMER
                ),
                livemode=False,
                user_reference=(
                    TEST_REFERENCE
                ),
                now=FIXED_NOW,
                table_resource=(
                    table_resource
                ),
                client_resource=client,
            )

    def test_conflicting_lookup_mapping_rejected(
        self,
    ):
        requested_mapping, _ = (
            stored_items()
        )

        _, conflicting_lookup = (
            stored_items(
                user_id=OTHER_USER,
                customer_id=(
                    TEST_CUSTOMER
                ),
                user_reference=(
                    OTHER_REFERENCE
                ),
            )
        )

        table_resource = (
            fake_table_with_items(
                requested_mapping,
                conflicting_lookup,
            )
        )

        client = FakeClient(
            error=client_error(
                "TransactionCanceledException",
                operation=(
                    "TransactWriteItems"
                ),
                cancellation_codes=[
                    "ConditionalCheckFailed",
                ],
            )
        )

        with self.assertRaises(
            BillingCustomerConflictError
        ):
            create_stripe_customer_mapping(
                user_id=TEST_USER,
                customer_id=(
                    TEST_CUSTOMER
                ),
                livemode=False,
                user_reference=(
                    TEST_REFERENCE
                ),
                now=FIXED_NOW,
                table_resource=(
                    table_resource
                ),
                client_resource=client,
            )

    def test_retryable_transaction_error_is_sanitized(
        self,
    ):
        private_detail = (
            "Synthetic private "
            "DynamoDB detail."
        )

        client = FakeClient(
            error=client_error(
                "ThrottlingException",
                operation=(
                    "TransactWriteItems"
                ),
            )
        )

        with self.assertRaises(
            BillingCustomerStoreError
        ) as raised:
            create_stripe_customer_mapping(
                user_id=TEST_USER,
                customer_id=(
                    TEST_CUSTOMER
                ),
                livemode=False,
                user_reference=(
                    TEST_REFERENCE
                ),
                now=FIXED_NOW,
                client_resource=client,
            )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertNotIn(
            private_detail,
            raised.exception.message,
        )

    def test_reverse_lookup_returns_private_user(
        self,
    ):
        _, lookup_item = (
            stored_items()
        )

        fake = FakeTable(
            items={
                (
                    lookup_item["PK"],
                    lookup_item["SK"],
                ):
                    lookup_item,
            }
        )

        result = (
            get_billing_user_for_customer(
                TEST_CUSTOMER,
                livemode=False,
                table_resource=fake,
            )
        )

        self.assertEqual(
            result["userId"],
            TEST_USER,
        )

        self.assertEqual(
            result["userReference"],
            TEST_REFERENCE,
        )


if __name__ == "__main__":
    unittest.main()
