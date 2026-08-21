# JM8 Stripe webhook patch

## Documentation

- [JM8 cloud architecture](docs/JM8_CLOUD_ARCHITECTURE.md)
- [JM8 staging operations runbook](docs/JM8_STAGING_OPERATIONS_RUNBOOK.md)
- [JM8 production promotion checklist](docs/JM8_PRODUCTION_PROMOTION_CHECKLIST.md)

This patch adds the public `POST /billing/webhook` route and processes these
Stripe events:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

The handler verifies Stripe's HMAC signature against the unmodified request
body, resolves the Cognito user through JM8's existing Stripe-customer reverse
mapping, verifies `jm8_user_ref` ownership metadata, and creates or replaces
the existing entitlement record with source `STRIPE`.

## Apply

From the JM8 repository root, back up or commit the current working tree first.
Then extract this archive over the repository root:

```bash
cd ~/jm8-app
unzip -o ~/Downloads/jm8-stripe-webhook-patch.zip
git diff --check
git diff --stat
```

The archive only contains these path-aware files:

- `backend/function/billing_webhook.py` (new)
- `backend/function/app.py` (updated)
- `backend/bin/create-api` (updated)
- `backend/tests/test_billing_webhook.py` (new)
- `backend/tests/test_billing_checkout_route.py` (updated)

## Test before deployment

```bash
cd ~/jm8-app/backend
source .venv/bin/activate

python -m py_compile \
  function/billing_webhook.py \
  function/app.py

python -m pytest -q \
  tests/test_billing_webhook.py \
  tests/test_billing_checkout_route.py \
  tests/test_billing_policy.py \
  tests/test_billing_customer_store.py \
  tests/test_entitlement_store.py \
  tests/test_api_route_postconditions.py

bash -n bin/create-api
```

If the route-postcondition test uses an explicit route list, add
`POST /billing/webhook` to the expected create-api routes and ensure it is not
listed among secure-api routes.

## Configure Stripe before deploying

Staging Stripe bootstrap is intentionally two-phase:

1. Source the staging base environment.
2. Set the staging Stripe test key and staging HTTPS return URLs:
  `STRIPE_CHECKOUT_SUCCESS_URL`, `STRIPE_CHECKOUT_CANCEL_URL`, and
  `STRIPE_PORTAL_RETURN_URL`.
3. Run `./bin/setup-stripe-catalog`.
4. Run `./bin/provision-stripe-secret` without a webhook secret for staging
  bootstrap.
5. Source `infra/environments/generated/staging.stripe.env`.
6. Run `./bin/deploy`.
7. Run `./bin/create-api` and `./bin/secure-api`.
8. Create the Stripe webhook endpoint for:
  `${API_ENDPOINT}/billing/webhook`.
9. Rerun `./bin/provision-stripe-secret` with the real webhook signing secret.
10. Verify webhook readiness and redeploy only if actually required.

The staging bootstrap secret contains only `STRIPE_SECRET_KEY` until the real
Stripe endpoint exists. Production requires both secret fields before deploy.
Neither secret belongs in `.env`, generated environment files, source control,
terminal output, or the Lambda environment. The ARN-backed runtime loader
retrieves both fields from Secrets Manager.

## Deploy and secure routes

```bash
cd ~/jm8-app/backend
source .venv/bin/activate
source .env
source "infra/environments/generated/${STAGE}.cognito.env"

./bin/deploy
./bin/create-api
./bin/secure-api
```

For an existing local dev configuration, manually move it once from
`infra/cognito.env` to `infra/environments/generated/dev.cognito.env` before
sourcing the stage-specific file. The provisioning script does not read or
migrate the legacy file automatically.

Verify the webhook remains public while Checkout remains JWT-protected:

```bash
aws apigatewayv2 get-routes \
  --api-id u06tdrfsua \
  --no-paginate \
  --max-results 500 \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query "Items[?contains(RouteKey, 'billing')].[RouteKey,AuthorizationType,Target]" \
  --output table
```

Expected authorization types:

- `POST /billing/checkout` -> `JWT`
- `POST /billing/webhook` -> `NONE`

## Replay the existing payment

In Stripe Workbench, open the successful test event and resend it to the new
endpoint. Start with `checkout.session.completed`; Stripe's subscription event
replays can then reconcile the billing period and cancellation state.

Finally verify:

```bash
curl -sS \
  "$API_ENDPOINT/account/entitlement" \
  -H "Authorization: Bearer $ID_TOKEN" \
  -w '\nHTTP_STATUS:%{http_code}\n'
```

Expected: `plan.id` is `PRO`, `subscription.status` is `ACTIVE`,
`subscription.source` is `STRIPE`, and `access.isPro` is `true`.
