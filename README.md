# JM8 Stripe webhook patch

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

Create a Stripe webhook endpoint for:

```text
https://u06tdrfsua.execute-api.us-east-1.amazonaws.com/billing/webhook
```

Subscribe it to the four events listed above. Copy its signing secret (starts
with `whsec_`) into the same AWS Secrets Manager JSON object referenced by
`STRIPE_SECRET_ARN`, using this key:

```json
{
  "STRIPE_SECRET_KEY": "existing value",
  "STRIPE_WEBHOOK_SECRET": "whsec_..."
}
```

Do not place either secret in `.env`, source control, terminal output, or the
Lambda environment. The existing secret loader should retrieve both at
runtime.

## Deploy and secure routes

```bash
cd ~/jm8-app/backend
source .venv/bin/activate
source .env
source infra/cognito.env

./bin/deploy
./bin/create-api
./bin/secure-api
```

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
