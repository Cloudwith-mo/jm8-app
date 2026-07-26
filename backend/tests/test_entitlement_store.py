import json
import os
import unittest
from datetime import (
    datetime,
    timedelta,
    timezone,
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


from entitlement_policy import (  # noqa: E402
    ENTITLEMENT_ENTITY_TYPE,
    ENTITLEMENT_SK,
    ENTITLEMENT_VERSION,
    SOURCE_STRIPE,
    SOURCE_SYSTEM,
    STATUS_ACTIVE,
    STATUS_FREE,
)
from entitlement_store import (  # noqa: E402
    EntitlementConflictError,
    EntitlementStoreError,
    create_entitlement_record,
    entitlement_key,
    get_entitlement_record,
    normalize_entitlement_user_id,
    replace_entitlement_record,
)
from usage_policy import (  # noqa: E402
    PLAN_FREE,
    PLAN_PRO,
)


FIXED_NOW = datetime(
    2026,
    7,
    26,
    18,
    0,
    tzinfo=timezone.utc,
)

NEXT_UPDATE = (
    FIXED_NOW
    + timedelta(hours=1)
)


def client_error(
    code: str,
    *,
    operation: str,
) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": (
                    "Synthetic provider "
                    "private detail."
                ),
            }
        },
        operation,
    )


def stored_pro_item(
    *,
    user_id="test-user",
):
    return {
        "PK": f"USER#{user_id}",
        "SK": ENTITLEMENT_SK,
        "entityType": (
            ENTITLEMENT_ENTITY_TYPE
        ),
        "entitlementVersion": (
            ENTITLEMENT_VERSION
        ),
        "plan": PLAN_PRO,
        "status": STATUS_ACTIVE,
        "source": SOURCE_STRIPE,
        "accessStartsAt": None,
        "accessEndsAt": None,
        "cancelAtPeriodEnd": False,
        "updatedAt": (
            FIXED_NOW.isoformat()
        ),
    }


class FakeTable:
    def __init__(
        self,
        *,
        get_result=None,
        get_error=None,
        put_error=None,
    ):
        self.get_result = (
            get_result
            if get_result is not None
            else {}
        )

        self.get_error = get_error
        self.put_error = put_error

        self.get_calls = []
        self.put_calls = []

    def get_item(
        self,
        **kwargs,
    ):
        self.get_calls.append(
            kwargs
        )

        if self.get_error:
            raise self.get_error

        return self.get_result

    def put_item(
        self,
        **kwargs,
    ):
        self.put_calls.append(
            kwargs
        )

        if self.put_error:
            raise self.put_error

        return {}


class EntitlementStoreTests(
    unittest.TestCase
):
    def test_invalid_user_is_rejected(
        self,
    ):
        with self.assertRaises(
            EntitlementStoreError
        ) as raised:
            normalize_entitlement_user_id(
                " "
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementUser",
        )

    def test_entitlement_key_is_user_scoped(
        self,
    ):
        self.assertEqual(
            entitlement_key(
                "user-a"
            ),
            {
                "PK": "USER#user-a",
                "SK": ENTITLEMENT_SK,
            },
        )

    def test_different_users_have_different_keys(
        self,
    ):
        self.assertNotEqual(
            entitlement_key(
                "user-a"
            )["PK"],
            entitlement_key(
                "user-b"
            )["PK"],
        )

    def test_missing_entitlement_returns_none(
        self,
    ):
        fake = FakeTable(
            get_result={}
        )

        result = get_entitlement_record(
            "test-user",
            table_resource=fake,
        )

        self.assertIsNone(
            result
        )

    def test_read_is_strongly_consistent(
        self,
    ):
        fake = FakeTable(
            get_result={
                "Item": stored_pro_item()
            }
        )

        get_entitlement_record(
            "test-user",
            table_resource=fake,
        )

        self.assertEqual(
            len(fake.get_calls),
            1,
        )

        self.assertTrue(
            fake.get_calls[0][
                "ConsistentRead"
            ]
        )

        self.assertEqual(
            fake.get_calls[0]["Key"],
            {
                "PK": "USER#test-user",
                "SK": ENTITLEMENT_SK,
            },
        )

    def test_read_returns_allow_listed_record(
        self,
    ):
        item = stored_pro_item()

        item.update({
            "stripeCustomerId": (
                "private-customer"
            ),
            "subscriptionId": (
                "private-subscription"
            ),
            "paymentMethodId": (
                "private-payment-method"
            ),
            "privateMetadata": {
                "secret": True,
            },
        })

        fake = FakeTable(
            get_result={
                "Item": item
            }
        )

        result = get_entitlement_record(
            "test-user",
            table_resource=fake,
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

        for private_field in (
            "stripeCustomerId",
            "subscriptionId",
            "paymentMethodId",
            "privateMetadata",
            "private-customer",
            "private-subscription",
        ):
            self.assertNotIn(
                private_field,
                serialized,
            )

    def test_wrong_storage_owner_is_rejected(
        self,
    ):
        fake = FakeTable(
            get_result={
                "Item": stored_pro_item(
                    user_id="other-user"
                )
            }
        )

        with self.assertRaises(
            EntitlementStoreError
        ) as raised:
            get_entitlement_record(
                "test-user",
                table_resource=fake,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementRecord",
        )

    def test_invalid_entity_type_is_rejected(
        self,
    ):
        item = stored_pro_item()
        item["entityType"] = (
            "OTHER_ENTITY"
        )

        fake = FakeTable(
            get_result={
                "Item": item
            }
        )

        with self.assertRaises(
            EntitlementStoreError
        ) as raised:
            get_entitlement_record(
                "test-user",
                table_resource=fake,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementRecord",
        )

    def test_invalid_updated_timestamp_rejected(
        self,
    ):
        item = stored_pro_item()
        item["updatedAt"] = (
            "not-a-date"
        )

        fake = FakeTable(
            get_result={
                "Item": item
            }
        )

        with self.assertRaises(
            EntitlementStoreError
        ) as raised:
            get_entitlement_record(
                "test-user",
                table_resource=fake,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementRecord",
        )

    def test_retryable_read_error_is_sanitized(
        self,
    ):
        fake = FakeTable(
            get_error=client_error(
                "ThrottlingException",
                operation="GetItem",
            )
        )

        with self.assertRaises(
            EntitlementStoreError
        ) as raised:
            get_entitlement_record(
                "test-user",
                table_resource=fake,
            )

        self.assertEqual(
            raised.exception.code,
            "ThrottlingException",
        )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertNotIn(
            "Synthetic provider",
            raised.exception.message,
        )

    def test_nonretryable_read_error_sanitized(
        self,
    ):
        fake = FakeTable(
            get_error=client_error(
                "ValidationException",
                operation="GetItem",
            )
        )

        with self.assertRaises(
            EntitlementStoreError
        ) as raised:
            get_entitlement_record(
                "test-user",
                table_resource=fake,
            )

        self.assertFalse(
            raised.exception.retryable
        )

        self.assertNotIn(
            "Synthetic provider",
            raised.exception.message,
        )

    def test_create_uses_conditional_write(
        self,
    ):
        fake = FakeTable()

        created = (
            create_entitlement_record(
                "test-user",
                plan=PLAN_FREE,
                status=STATUS_FREE,
                source=SOURCE_SYSTEM,
                now=FIXED_NOW,
                table_resource=fake,
            )
        )

        self.assertEqual(
            len(fake.put_calls),
            1,
        )

        call = fake.put_calls[0]

        self.assertEqual(
            call[
                "ConditionExpression"
            ],
            "attribute_not_exists(#sk)",
        )

        self.assertEqual(
            call["Item"]["PK"],
            "USER#test-user",
        )

        self.assertEqual(
            call["Item"]["SK"],
            ENTITLEMENT_SK,
        )

        self.assertEqual(
            created["plan"],
            PLAN_FREE,
        )

    def test_create_output_excludes_storage_keys(
        self,
    ):
        fake = FakeTable()

        created = (
            create_entitlement_record(
                "test-user",
                plan=PLAN_PRO,
                status=STATUS_ACTIVE,
                source=SOURCE_STRIPE,
                now=FIXED_NOW,
                table_resource=fake,
            )
        )

        self.assertNotIn(
            "PK",
            created,
        )

        self.assertNotIn(
            "SK",
            created,
        )

    def test_duplicate_create_returns_conflict(
        self,
    ):
        fake = FakeTable(
            put_error=client_error(
                (
                    "ConditionalCheckFailed"
                    "Exception"
                ),
                operation="PutItem",
            )
        )

        with self.assertRaises(
            EntitlementConflictError
        ):
            create_entitlement_record(
                "test-user",
                plan=PLAN_FREE,
                status=STATUS_FREE,
                source=SOURCE_SYSTEM,
                now=FIXED_NOW,
                table_resource=fake,
            )

    def test_replace_uses_optimistic_lock(
        self,
    ):
        fake = FakeTable()

        replaced = (
            replace_entitlement_record(
                "test-user",
                expected_updated_at=(
                    FIXED_NOW.isoformat()
                ),
                plan=PLAN_PRO,
                status=STATUS_ACTIVE,
                source=SOURCE_STRIPE,
                now=NEXT_UPDATE,
                table_resource=fake,
            )
        )

        call = fake.put_calls[0]

        self.assertEqual(
            call[
                "ConditionExpression"
            ],
            (
                "#updatedAt = "
                ":expectedUpdatedAt"
            ),
        )

        self.assertEqual(
            call[
                "ExpressionAttributeValues"
            ][":expectedUpdatedAt"],
            FIXED_NOW.isoformat(),
        )

        self.assertEqual(
            replaced["updatedAt"],
            NEXT_UPDATE.isoformat(),
        )

    def test_stale_replace_returns_conflict(
        self,
    ):
        fake = FakeTable(
            put_error=client_error(
                (
                    "ConditionalCheckFailed"
                    "Exception"
                ),
                operation="PutItem",
            )
        )

        with self.assertRaises(
            EntitlementConflictError
        ):
            replace_entitlement_record(
                "test-user",
                expected_updated_at=(
                    FIXED_NOW.isoformat()
                ),
                plan=PLAN_PRO,
                status=STATUS_ACTIVE,
                source=SOURCE_STRIPE,
                now=NEXT_UPDATE,
                table_resource=fake,
            )

    def test_replace_requires_valid_timestamp(
        self,
    ):
        fake = FakeTable()

        with self.assertRaises(
            EntitlementStoreError
        ) as raised:
            replace_entitlement_record(
                "test-user",
                expected_updated_at=(
                    "not-a-date"
                ),
                plan=PLAN_PRO,
                status=STATUS_ACTIVE,
                source=SOURCE_STRIPE,
                now=NEXT_UPDATE,
                table_resource=fake,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementRecord",
        )

        self.assertEqual(
            len(fake.put_calls),
            0,
        )

    def test_invalid_policy_update_is_sanitized(
        self,
    ):
        fake = FakeTable()

        with self.assertRaises(
            EntitlementStoreError
        ) as raised:
            create_entitlement_record(
                "test-user",
                plan=PLAN_FREE,
                status=STATUS_ACTIVE,
                source=SOURCE_SYSTEM,
                now=FIXED_NOW,
                table_resource=fake,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementRecord",
        )

        self.assertEqual(
            len(fake.put_calls),
            0,
        )


if __name__ == "__main__":
    unittest.main()
