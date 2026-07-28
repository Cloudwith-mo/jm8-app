import json
import unittest

from botocore.exceptions import (
    ClientError,
)

from stripe_checkout_gateway import (
    StripeCheckoutGateway,
    StripeGatewayError,
)
from stripe_secret_loader import (
    STRIPE_SECRET_ARN_ENV,
    StripeSecretLoadError,
    clear_stripe_secret_cache,
    load_stripe_runtime_environment,
    normalize_stripe_secret_arn,
    retrieve_stripe_secret_key,
)


TEST_SECRET = (
    "sk_test_"
    "secretloader123456"
)

TEST_SECRET_ARN = (
    "arn:aws:secretsmanager:"
    "us-east-1:"
    "111122223333:"
    "secret:journalm8/dev/"
    "stripe-AbCdEf"
)

TEST_PRICE = (
    "price_"
    "secretloader123456"
)


def checkout_environment(
    *,
    include_secret=False,
):
    environment = {
        "STRIPE_PRO_MONTHLY_PRICE_ID":
            TEST_PRICE,

        "STRIPE_CHECKOUT_SUCCESS_URL":
            (
                "https://app.example.com/"
                "?checkout=success"
            ),

        "STRIPE_CHECKOUT_CANCEL_URL":
            (
                "https://app.example.com/"
                "?checkout=cancelled"
            ),

        "STRIPE_PORTAL_RETURN_URL":
            "https://app.example.com/",
    }

    if include_secret:
        environment[
            "STRIPE_SECRET_KEY"
        ] = TEST_SECRET

    return environment


def client_error(
    code,
):
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": (
                    "Synthetic private "
                    "secret detail."
                ),
            }
        },
        "GetSecretValue",
    )


class FakeSecretsClient:
    def __init__(
        self,
        *,
        result=None,
        error=None,
    ):
        self.result = (
            result
            if result is not None
            else {
                "SecretString":
                    json.dumps({
                        "STRIPE_SECRET_KEY":
                            TEST_SECRET
                    })
            }
        )

        self.error = error
        self.calls = []

    def get_secret_value(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        if self.error:
            raise self.error

        return self.result


class StripeSecretLoaderTests(
    unittest.TestCase
):
    def setUp(
        self,
    ):
        clear_stripe_secret_cache()

    def tearDown(
        self,
    ):
        clear_stripe_secret_cache()

    def test_direct_secret_bypasses_aws(
        self,
    ):
        client = FakeSecretsClient()

        result = (
            load_stripe_runtime_environment(
                checkout_environment(
                    include_secret=True
                ),
                client_resource=client,
            )
        )

        self.assertEqual(
            result[
                "STRIPE_SECRET_KEY"
            ],
            TEST_SECRET,
        )

        self.assertEqual(
            client.calls,
            [],
        )

    def test_missing_arn_rejected(
        self,
    ):
        with self.assertRaises(
            StripeSecretLoadError
        ) as raised:
            load_stripe_runtime_environment(
                checkout_environment()
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidStripeSecretArn",
        )

    def test_invalid_arn_rejected(
        self,
    ):
        with self.assertRaises(
            StripeSecretLoadError
        ):
            normalize_stripe_secret_arn(
                "not-an-arn"
            )

    def test_secret_is_retrieved_by_full_arn(
        self,
    ):
        client = FakeSecretsClient()

        result = retrieve_stripe_secret_key(
            TEST_SECRET_ARN,
            client_resource=client,
        )

        self.assertEqual(
            result,
            TEST_SECRET,
        )

        self.assertEqual(
            client.calls,
            [{
                "SecretId":
                    TEST_SECRET_ARN
            }],
        )

    def test_secret_is_cached(
        self,
    ):
        client = FakeSecretsClient()

        first = retrieve_stripe_secret_key(
            TEST_SECRET_ARN,
            client_resource=client,
        )

        second = retrieve_stripe_secret_key(
            TEST_SECRET_ARN,
            client_resource=client,
        )

        self.assertEqual(
            first,
            second,
        )

        self.assertEqual(
            len(client.calls),
            1,
        )

    def test_environment_is_augmented(
        self,
    ):
        client = FakeSecretsClient()

        environment = (
            checkout_environment()
        )

        environment[
            STRIPE_SECRET_ARN_ENV
        ] = TEST_SECRET_ARN

        result = (
            load_stripe_runtime_environment(
                environment,
                client_resource=client,
            )
        )

        self.assertEqual(
            result[
                "STRIPE_SECRET_KEY"
            ],
            TEST_SECRET,
        )

        self.assertNotIn(
            "STRIPE_SECRET_KEY",
            environment,
        )

    def test_invalid_json_rejected(
        self,
    ):
        client = FakeSecretsClient(
            result={
                "SecretString":
                    "not-json"
            }
        )

        with self.assertRaises(
            StripeSecretLoadError
        ) as raised:
            retrieve_stripe_secret_key(
                TEST_SECRET_ARN,
                client_resource=client,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidStripeSecretValue",
        )

    def test_non_object_secret_rejected(
        self,
    ):
        client = FakeSecretsClient(
            result={
                "SecretString":
                    json.dumps([
                        TEST_SECRET
                    ])
            }
        )

        with self.assertRaises(
            StripeSecretLoadError
        ):
            retrieve_stripe_secret_key(
                TEST_SECRET_ARN,
                client_resource=client,
            )

    def test_missing_secret_field_rejected(
        self,
    ):
        client = FakeSecretsClient(
            result={
                "SecretString":
                    json.dumps({
                        "other":
                            "value"
                    })
            }
        )

        with self.assertRaises(
            StripeSecretLoadError
        ):
            retrieve_stripe_secret_key(
                TEST_SECRET_ARN,
                client_resource=client,
            )

    def test_invalid_secret_key_rejected(
        self,
    ):
        client = FakeSecretsClient(
            result={
                "SecretString":
                    json.dumps({
                        "STRIPE_SECRET_KEY":
                            "invalid"
                    })
            }
        )

        with self.assertRaises(
            StripeSecretLoadError
        ):
            retrieve_stripe_secret_key(
                TEST_SECRET_ARN,
                client_resource=client,
            )

    def test_binary_secret_rejected(
        self,
    ):
        client = FakeSecretsClient(
            result={
                "SecretBinary":
                    b"private"
            }
        )

        with self.assertRaises(
            StripeSecretLoadError
        ):
            retrieve_stripe_secret_key(
                TEST_SECRET_ARN,
                client_resource=client,
            )

    def test_retryable_aws_error_is_sanitized(
        self,
    ):
        client = FakeSecretsClient(
            error=client_error(
                "ThrottlingException"
            )
        )

        with self.assertRaises(
            StripeSecretLoadError
        ) as raised:
            retrieve_stripe_secret_key(
                TEST_SECRET_ARN,
                client_resource=client,
            )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertNotIn(
            "Synthetic private",
            raised.exception.message,
        )

        self.assertNotIn(
            TEST_SECRET,
            raised.exception.message,
        )

    def test_nonretryable_aws_error_is_sanitized(
        self,
    ):
        client = FakeSecretsClient(
            error=client_error(
                "AccessDeniedException"
            )
        )

        with self.assertRaises(
            StripeSecretLoadError
        ) as raised:
            retrieve_stripe_secret_key(
                TEST_SECRET_ARN,
                client_resource=client,
            )

        self.assertFalse(
            raised.exception.retryable
        )

        self.assertNotIn(
            "Synthetic private",
            raised.exception.message,
        )

    def test_gateway_uses_secret_loader(
        self,
    ):
        calls = []

        def secret_loader(
            environ,
        ):
            calls.append(
                environ
            )

            result = dict(
                environ or {}
            )

            result[
                "STRIPE_SECRET_KEY"
            ] = TEST_SECRET

            return result

        gateway = StripeCheckoutGateway(
            environ=(
                checkout_environment()
            ),
            secret_loader=secret_loader,
        )

        self.assertEqual(
            len(calls),
            1,
        )

        self.assertFalse(
            gateway.livemode
        )

    def test_retryable_loader_error_becomes_503(
        self,
    ):
        def failed_loader(
            environ,
        ):
            raise StripeSecretLoadError(
                "ThrottlingException",
                (
                    "Private secret "
                    "provider detail."
                ),
                retryable=True,
            )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            StripeCheckoutGateway(
                environ=(
                    checkout_environment()
                ),
                secret_loader=(
                    failed_loader
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            503,
        )

        self.assertTrue(
            raised.exception.retryable
        )

    def test_nonretryable_loader_error_becomes_500(
        self,
    ):
        def failed_loader(
            environ,
        ):
            raise StripeSecretLoadError(
                "InvalidStripeSecretValue",
                (
                    "Private secret "
                    "provider detail."
                ),
                retryable=False,
            )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            StripeCheckoutGateway(
                environ=(
                    checkout_environment()
                ),
                secret_loader=(
                    failed_loader
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            500,
        )

        self.assertFalse(
            raised.exception.retryable
        )


if __name__ == "__main__":
    unittest.main()
