import unittest

from billing_identity import build_billing_user_reference
from billing_portal import BillingPortalError, create_billing_portal


USER_ID = "jwt-user"
CUSTOMER_ID = "cus_123456789"
PORTAL_URL = "https://billing.stripe.com/p/session/test_123"


def mapping(*, livemode=False):
    return {
        "stripeCustomerId": CUSTOMER_ID,
        "livemode": livemode,
        "userReference": build_billing_user_reference(USER_ID),
    }


class FakeGateway:
    livemode = False

    def __init__(self):
        self.retrieved = None
        self.created = None

    def retrieve_customer(self, customer_id):
        self.retrieved = customer_id
        return {
            "object": "customer",
            "id": customer_id,
            "livemode": self.livemode,
        }

    def create_billing_portal_session(self, *, customer_id):
        self.created = customer_id
        return {
            "object": "billing_portal.session",
            "customer": customer_id,
            "livemode": self.livemode,
            "url": PORTAL_URL,
        }


class BillingPortalTests(unittest.TestCase):
    def test_creates_portal_for_verified_mapping(self):
        gateway = FakeGateway()

        result = create_billing_portal(
            user_id=USER_ID,
            gateway=gateway,
            mapping_reader=lambda _: mapping(),
        )

        self.assertEqual(
            result,
            {"billingPortalUrl": PORTAL_URL},
        )
        self.assertEqual(gateway.retrieved, CUSTOMER_ID)
        self.assertEqual(gateway.created, CUSTOMER_ID)

    def test_missing_mapping_returns_404(self):
        with self.assertRaises(BillingPortalError) as caught:
            create_billing_portal(
                user_id=USER_ID,
                gateway=FakeGateway(),
                mapping_reader=lambda _: None,
            )

        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(
            caught.exception.code,
            "BillingCustomerNotFound",
        )

    def test_mode_mismatch_returns_409(self):
        with self.assertRaises(BillingPortalError) as caught:
            create_billing_portal(
                user_id=USER_ID,
                gateway=FakeGateway(),
                mapping_reader=lambda _: mapping(livemode=True),
            )

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.code,
            "BillingCustomerModeMismatch",
        )

    def test_ownership_mismatch_returns_409(self):
        bad_mapping = mapping()
        bad_mapping["userReference"] = build_billing_user_reference(
            "another-user"
        )

        with self.assertRaises(BillingPortalError) as caught:
            create_billing_portal(
                user_id=USER_ID,
                gateway=FakeGateway(),
                mapping_reader=lambda _: bad_mapping,
            )

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.code,
            "BillingCustomerOwnershipMismatch",
        )


if __name__ == "__main__":
    unittest.main()
