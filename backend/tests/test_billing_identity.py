import unittest

from billing_identity import (
    BILLING_USER_REFERENCE_PREFIX,
    CHECKOUT_IDEMPOTENCY_PREFIX,
    CUSTOMER_IDEMPOTENCY_PREFIX,
    BillingIdentityError,
    build_billing_user_reference,
    build_checkout_idempotency_key,
    build_customer_idempotency_key,
    normalize_billing_request_token,
    normalize_billing_user_id,
)


class BillingIdentityTests(
    unittest.TestCase
):
    def test_user_id_is_normalized(
        self,
    ):
        self.assertEqual(
            normalize_billing_user_id(
                "  user-a  "
            ),
            "user-a",
        )

    def test_missing_user_is_rejected(
        self,
    ):
        with self.assertRaises(
            BillingIdentityError
        ) as raised:
            normalize_billing_user_id(
                " "
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidBillingUser",
        )

    def test_request_token_is_normalized(
        self,
    ):
        self.assertEqual(
            normalize_billing_request_token(
                "  request:123  "
            ),
            "request:123",
        )

    def test_invalid_request_token_rejected(
        self,
    ):
        with self.assertRaises(
            BillingIdentityError
        ) as raised:
            normalize_billing_request_token(
                "invalid token"
            )

        self.assertEqual(
            raised.exception.code,
            (
                "InvalidBilling"
                "RequestToken"
            ),
        )

    def test_user_reference_is_stable(
        self,
    ):
        first = (
            build_billing_user_reference(
                "user-a"
            )
        )

        second = (
            build_billing_user_reference(
                "user-a"
            )
        )

        self.assertEqual(
            first,
            second,
        )

        self.assertTrue(
            first.startswith(
                BILLING_USER_REFERENCE_PREFIX
            )
        )

    def test_different_users_have_different_references(
        self,
    ):
        self.assertNotEqual(
            build_billing_user_reference(
                "user-a"
            ),
            build_billing_user_reference(
                "user-b"
            ),
        )

    def test_reference_does_not_expose_user_id(
        self,
    ):
        raw_user = (
            "private-cognito-user"
        )

        reference = (
            build_billing_user_reference(
                raw_user
            )
        )

        self.assertNotIn(
            raw_user,
            reference,
        )

    def test_customer_key_is_stable_and_private(
        self,
    ):
        raw_user = (
            "private-cognito-user"
        )

        first = (
            build_customer_idempotency_key(
                raw_user
            )
        )

        second = (
            build_customer_idempotency_key(
                raw_user
            )
        )

        self.assertEqual(
            first,
            second,
        )

        self.assertTrue(
            first.startswith(
                CUSTOMER_IDEMPOTENCY_PREFIX
            )
        )

        self.assertNotIn(
            raw_user,
            first,
        )

    def test_checkout_key_reuses_same_request(
        self,
    ):
        first = (
            build_checkout_idempotency_key(
                "user-a",
                "request-123",
            )
        )

        second = (
            build_checkout_idempotency_key(
                "user-a",
                "request-123",
            )
        )

        self.assertEqual(
            first,
            second,
        )

        self.assertTrue(
            first.startswith(
                CHECKOUT_IDEMPOTENCY_PREFIX
            )
        )

    def test_checkout_key_changes_for_new_request(
        self,
    ):
        self.assertNotEqual(
            build_checkout_idempotency_key(
                "user-a",
                "request-123",
            ),
            build_checkout_idempotency_key(
                "user-a",
                "request-456",
            ),
        )


if __name__ == "__main__":
    unittest.main()
