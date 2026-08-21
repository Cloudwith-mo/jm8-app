# JM8 Staging Operations Runbook

This runbook is for the implemented `staging` environment only. It assumes the operator is at a reviewed commit and has an approved staging change window. Commands that mutate AWS or Stripe are deliberately grouped under deployment steps; verification commands are read-only unless explicitly labeled.

The staging acceptance supplied with this documentation is complete at commit `d988997`: frontend/OAC, Cognito/JWT, API routes, hardened DynamoDB/S3, Stripe test billing, Bedrock analysis, one-record historical reanalysis, workflow logging/tracing, dashboards, alarms, retention, SNS delivery, alarm transition, and recovery were verified. That evidence does not waive the checks below for a later deployment.

## 1. Operator safety rules

### Prerequisites

- Bash, Python 3, AWS CLI v2, `jq`, Node/npm, and the backend virtual environment are installed.
- AWS credentials for the staging profile are valid and require no secret values in command arguments or terminal history.
- The expected commit and change set have been reviewed.
- Staging callback, logout, checkout, cancel, Portal, API, and frontend origins are plain HTTPS URLs.
- A non-personal placeholder such as `${ALERT_EMAIL}` is replaced through the operator's secure process only when an SNS subscription is intended.
- Stripe is in test mode. Production credentials must never be used in staging.

### Mandatory rules

1. Work from `backend/` because scripts use repository-relative paths.
2. Use one shell for the entire sequence. Source files; do not execute them as child processes.
3. Verify `STAGE`, profile, region, and STS account before every mutation group.
4. Stop on any account, stage, role, ARN, origin, or postcondition mismatch. Never “fix” a mismatch by weakening a policy.
5. Do not paste credentials, browser tokens, Basic Auth values, Stripe signing material, or journal text into commands, tickets, screenshots, logs, or chat.
6. Do not enable shell tracing. Keep `set -euo pipefail` semantics when wrapping commands.
7. Do not delete or recreate a table, bucket, state machine, role, Lambda, secret, distribution, or user pool as a recovery shortcut.
8. Treat generated files as local handoff artifacts. They are ignored by Git and are not backups.
9. Use synthetic, non-sensitive journal text and images for smoke tests.
10. Save only sanitized diagnostics. `backend/bin/create-frontend-hosting` writes a sanitized report under its diagnostics directory.

## 2. Stage-isolated environment loading

### Base shell

From the repository root:

```bash
cd backend
source .venv/bin/activate

set -a
source .env
set +a

test "${STAGE}" = "staging"
test "${APP_NAME}" = "journalm8"
test "${DEPLOY_CONFIRMATION}" = "staging"
```

The ignored `backend/.env` is the base contract. It should provide stage identity, AWS settings, exact resource names, model/version settings, safe URLs, and safe price/configuration values. Do not store any Stripe API credential, webhook signing material, browser token, or Basic Auth value there.

Load generated files only for the already validated stage, and always after the base file so a different stage cannot leak into the shell:

```bash
COGNITO_ENV="infra/environments/generated/${STAGE}.cognito.env"
STRIPE_ENV="infra/environments/generated/${STAGE}.stripe.env"

if [[ -f "$COGNITO_ENV" ]]; then
  source "$COGNITO_ENV"
fi

if [[ -f "$STRIPE_ENV" ]]; then
  source "$STRIPE_ENV"
fi
```

Do not source an example file. Do not source a generated file whose filename is not exactly `${STAGE}`.

### Required-variable check without printing values

This checks presence only:

```bash
required_names=(
  APP_NAME STAGE AWS_PROFILE AWS_REGION EXPECTED_AWS_ACCOUNT_ID
  DEPLOY_CONFIRMATION API_NAME TABLE_NAME RAW_BUCKET
  FRONTEND_BUCKET ALLOWED_ORIGINS
  BEDROCK_ANALYSIS_MODEL_ID ANALYSIS_SCHEMA_VERSION
  ANALYSIS_PROMPT_VERSION
)

for name in "${required_names[@]}"; do
  [[ -n "${!name:-}" ]] || {
    printf 'Missing required variable: %s\n' "$name" >&2
    exit 1
  }
done
```

### Identity and contract preflight

These commands are read-only:

```bash
CALLER_ARN="$(aws sts get-caller-identity \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query Arn \
  --output text)"

export ACCOUNT_ID="$(aws sts get-caller-identity \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query Account \
  --output text)"

test "$ACCOUNT_ID" = "$EXPECTED_AWS_ACCOUNT_ID"

AWS_PARTITION="${CALLER_ARN#arn:}"
AWS_PARTITION="${AWS_PARTITION%%:*}"
test -n "$AWS_PARTITION"

python3 bin/jm8_environment_contract.py validate
```

The validator intentionally rejects production today. If STS fails, returns the wrong account, or the validator fails, stop; do not run a deployment script.

### Secret injection

The standalone Stripe and staging-hosting scripts require sensitive values in the current process. Obtain them through the approved secret manager or interactive operator process, export them only immediately before the owning script, and unset them immediately after. Never put them in `.env`, a generated file, a shell transcript, or a command-line literal.

```bash
# Values are injected by the approved secure process here.
# Do not paste example or real values into this runbook.

test -n "${STRIPE_SECRET_KEY:-}"
# Run only the intended Stripe command, then:
unset STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET
```

The API deployment must run with the plaintext Stripe variables unset. It accepts only `STRIPE_SECRET_ARN` and safe catalog settings.

## 3. Pre-deployment verification

### Source and branch

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git diff --check
```

Expected: the intended branch/commit, no unreviewed changes, and no whitespace errors. Stop if the worktree contains unrelated changes that the deployment package would include.

### Local checks

```bash
bash -n bin/create-resources
bash -n bin/create-auth
bash -n bin/deploy-ocr-workflow
bash -n bin/deploy-historical-reanalysis-workflow
bash -n bin/setup-stripe-catalog
bash -n bin/provision-stripe-secret
bash -n bin/deploy
bash -n bin/create-api
bash -n bin/secure-api
bash -n bin/create-frontend-hosting
bash -n bin/deploy-frontend
bash -n bin/deploy-observability
bash -n bin/deploy-analysis-observability
bash -n bin/deploy-bedrock-budget

PYTHONPATH=function ./.venv/bin/python -m unittest discover \
  -s tests -p 'test_*.py'

cd ../frontend
npm ci
npm test
npm run lint
npm run build:staging
cd ../backend
```

Do not proceed on a syntax, test, lint, environment-contract, or build failure.

### Existing-resource inspection

For a routine update, confirm that expected durable resources exist. These are read-only:

```bash
aws dynamodb describe-table \
  --table-name "$TABLE_NAME" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Table.{Status:TableStatus,DeletionProtection:DeletionProtectionEnabled}' \
  --output table

aws s3api head-bucket \
  --bucket "$RAW_BUCKET" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

aws iam get-role \
  --role-name "${APP_NAME}-${STAGE}-lambda-basic-role" \
  --profile "$AWS_PROFILE" \
  --query 'Role.Arn' \
  --output text
```

If this is the first staging bootstrap, absence is expected only where the owning create script explicitly handles it. An access error is not proof of absence.

## 4. Canonical deployment order

The order below follows actual dependencies rather than filename order.

### Step 1: establish frontend hosting and origin

`create-frontend-hosting` is staging-only. It needs the exact frontend bucket name and securely injected Basic Auth inputs.

```bash
./bin/create-frontend-hosting
```

Resolve its outputs instead of copying formatted console or Markdown text:

```bash
STACK_NAME="${APP_NAME}-${STAGE}-frontend-hosting"

export CLOUDFRONT_DISTRIBUTION_ID="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionId`].OutputValue | [0]' \
  --output text)"

export FRONTEND_ORIGIN="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`FrontendOrigin`].OutputValue | [0]' \
  --output text)"

test -n "$CLOUDFRONT_DISTRIBUTION_ID"
test "$CLOUDFRONT_DISTRIBUTION_ID" != "None"
test -n "$FRONTEND_ORIGIN"
test "$FRONTEND_ORIGIN" != "None"
```

Set `ALLOWED_ORIGINS`, `CALLBACK_URL`, and `LOGOUT_URL` to the exact plain HTTPS origin/paths required by the application. Do not wrap a URL in Markdown link syntax, quotes that become part of the value, or trailing punctuation.

### Step 2: create or resolve Cognito

```bash
./bin/create-auth
source "infra/environments/generated/${STAGE}.cognito.env"
```

The script creates missing resources but does not update an existing app client's callback/logout configuration. Always verify those values after the command; if an existing client differs, stop and use an approved, reviewed reconciliation procedure.

### Step 3: create and harden data resources and the API role

```bash
./bin/create-resources
```

This must run after `ALLOWED_ORIGINS` contains the exact staging frontend origin. It owns the table, raw bucket hardening, `${APP_NAME}-${STAGE}-lambda-basic-role`, its basic execution attachment, and `${APP_NAME}-${STAGE}-app-access-policy`.

### Step 4: deploy background workflows

```bash
./bin/deploy-ocr-workflow
./bin/deploy-historical-reanalysis-workflow
```

Both state machines must exist before `backend/bin/deploy`, which resolves their ARNs and grants the API role exact start permission. The historical workflow also requires a valid Bedrock inference profile and destination models.

### Step 5: reconcile Stripe test catalog and secret

For first staging bootstrap:

1. Inject the staging test API credential and safe staging return URLs into the current shell.
2. Run the catalog setup.
3. Source its generated safe catalog output before secret provisioning.
4. Provision the Secrets Manager secret. The webhook field may be absent only during staging bootstrap.
5. Source the generated file again for `STRIPE_SECRET_ARN`.
6. Confirm all safe values remain in the current shell, then unset plaintext values.

```bash
./bin/setup-stripe-catalog
source "infra/environments/generated/${STAGE}.stripe.env"

./bin/provision-stripe-secret
source "infra/environments/generated/${STAGE}.stripe.env"

for name in \
  STRIPE_SECRET_ARN \
  STRIPE_PRO_MONTHLY_PRICE_ID \
  STRIPE_CHECKOUT_SUCCESS_URL \
  STRIPE_CHECKOUT_CANCEL_URL \
  STRIPE_PORTAL_RETURN_URL
do
  [[ -n "${!name:-}" ]] || {
    printf 'Missing safe Stripe deployment value: %s\n' "$name" >&2
    exit 1
  }
done

unset STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET
```

Current repository behavior matters: both Stripe scripts write `infra/environments/generated/${STAGE}.stripe.env`, and secret provisioning rewrites it with the ARN. Sourcing the catalog output before provisioning keeps the safe catalog values in this shell. A fresh shell must reload those safe values from its base contract or an approved merged stage file before `deploy`; missing values must stop deployment.

### Step 6: deploy the API Lambda

```bash
test -z "${STRIPE_SECRET_KEY:-}"
test -z "${STRIPE_WEBHOOK_SECRET:-}"
./bin/deploy
```

The script packages the backend, verifies the API role, resolves and verifies the exact Bedrock policy, resolves both state machines and the table, checks the Stripe secret, reconciles scoped inline policies, builds a mode-0600 environment document, and creates or updates the API Lambda.

### Step 7: create routes and enforce authorization

```bash
./bin/create-api
./bin/secure-api
```

Capture the exact API endpoint from `get-api`, not from formatted output:

```bash
API_ID="$(aws apigatewayv2 get-apis \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query "Items[?Name=='${API_NAME}'].ApiId | [0]" \
  --output text)"

export API_ENDPOINT="$(aws apigatewayv2 get-api \
  --api-id "$API_ID" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query ApiEndpoint \
  --output text)"
```

`secure-api` does not invoke the shared account/stage guard. Run it only in this already verified shell, immediately after guarded `create-api`, and perform the route verification in section 6.

### Step 8: complete the Stripe webhook bootstrap

In Stripe test mode, create or verify the endpoint at the literal path `${API_ENDPOINT}/billing/webhook` and subscribe only to the events handled by `billing_webhook.py`. Obtain its signing material through the approved secure channel, inject it for `provision-stripe-secret`, rerun that script, then unset plaintext values.

The secret ARN does not change on update, so a Lambda redeploy is not normally required. Warm Lambda environments cache the secret for up to five minutes. Wait for cache expiry or use an approved cold-start procedure before concluding that a secret update failed.

### Step 9: deploy frontend assets

Set the Vite values from the exact API, Cognito, and CloudFront outputs:

```bash
export VITE_APP_STAGE="$STAGE"
export VITE_API_ENDPOINT="$API_ENDPOINT"
export VITE_COGNITO_DOMAIN="$COGNITO_DOMAIN"
export VITE_COGNITO_CLIENT_ID="$COGNITO_APP_CLIENT_ID"
export VITE_COGNITO_REDIRECT_URI="$COGNITO_CALLBACK_URL"
export VITE_COGNITO_LOGOUT_URI="$COGNITO_LOGOUT_URL"

./bin/deploy-frontend
```

The deploy script tests and builds, removes source maps, syncs the bucket, applies cache metadata, invalidates CloudFront, waits for completion, and validates home/archive responses.

### Step 10: deploy observability and cost controls

```bash
./bin/deploy-observability
./bin/deploy-analysis-observability
./bin/deploy-bedrock-budget
```

If `ALERT_EMAIL` is set, SNS creates an email subscription only when no matching subscription exists. The recipient must confirm it before alerts can be delivered.

## 5. Script ownership

| Script | Owns or reconciles | Does not own |
| --- | --- | --- |
| `backend/bin/build` | `.build/function` Python package tree | AWS resources |
| `backend/bin/package` | `dist/function.zip` | AWS resources |
| `backend/bin/create-frontend-hosting` | Staging CloudFormation frontend stack, private frontend bucket, CloudFront distribution/function/OAC, postconditions | Asset upload; production hosting |
| `backend/bin/create-auth` | Stage user pool, public app client, Hosted UI domain, generated Cognito file | Existing-client callback reconciliation; API authorizer |
| `backend/bin/create-resources` | Main table, raw bucket and hardening, API execution role and base app policy | API Lambda, workflows, API Gateway |
| `backend/bin/deploy-ocr-workflow` | OCR worker/failure Lambdas, worker/state roles, workflow definition, workflow log group, logging/tracing | API start permission |
| `backend/bin/deploy-historical-reanalysis-workflow` | Historical worker/coordinator Lambdas and roles, state role/machine, exact worker Bedrock policy, workflow log group, logging/tracing | API start permission; API Bedrock policy |
| `backend/bin/setup-stripe-catalog` | Stage-mode JM8 Pro product/price reconciliation and safe generated catalog values | Secrets Manager; webhook endpoint |
| `backend/bin/provision-stripe-secret` | `${APP_NAME}/${STAGE}/stripe`, secret value update, generated ARN | Stripe catalog or endpoint |
| `backend/bin/configure-bedrock-analyzer` | Standalone reconciliation of the API role's canonical Bedrock policy and safe model/version values in local `.env` | Lambda code deployment |
| `backend/bin/deploy` | API package/Lambda, canonical API Bedrock policy, scoped workflow/table/secret policies, Lambda environment | State-machine creation; API Gateway routes |
| `backend/bin/create-api` | HTTP API, Lambda integration, routes, `$default` stage, CORS, access logs/retention, invoke permission | JWT authorization policy |
| `backend/bin/secure-api` | Cognito JWT authorizer and exact secured/public route settings | Shared environment guard; user pool/client |
| `backend/bin/deploy-frontend` | Staging build/test, S3 asset sync/cache metadata, CloudFront invalidation and response checks | Hosting infrastructure; production frontend |
| `backend/bin/deploy-observability` | Operations dashboard, alert topic/policy/subscription, log retention, API access logging, OCR/historical/billing filters and alarms | AI-specific dashboard/alarms; budget |
| `backend/bin/deploy-analysis-observability` | AI metrics, failure/latency alarms, AI dashboard | General operations dashboard |
| `backend/bin/deploy-bedrock-budget` | Monthly Bedrock cost budget and 50/80/100-percent SNS notifications | Usage throttling or hard spend limit |

## 6. Post-deployment verification

### Data protection

```bash
aws dynamodb describe-continuous-backups \
  --table-name "$TABLE_NAME" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus' \
  --output text

aws dynamodb describe-table \
  --table-name "$TABLE_NAME" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Table.{Status:TableStatus,BillingMode:BillingModeSummary.BillingMode,DeletionProtection:DeletionProtectionEnabled}' \
  --output table

aws s3api get-public-access-block \
  --bucket "$RAW_BUCKET" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

aws s3api get-bucket-versioning \
  --bucket "$RAW_BUCKET" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

aws s3api get-bucket-lifecycle-configuration \
  --bucket "$RAW_BUCKET" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

Require table `ACTIVE`, on-demand billing, PITR enabled, deletion protection true, all four public-access flags true, versioning enabled, and the expected lifecycle rules.

### Lambda role and environment

```bash
aws lambda get-function-configuration \
  --function-name "${APP_NAME}-${STAGE}-api" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query '{State:State,Role:Role,Runtime:Runtime,Timeout:Timeout,Memory:MemorySize,EnvironmentKeys:keys(Environment.Variables)}' \
  --output json

aws iam get-role-policy \
  --role-name "${APP_NAME}-${STAGE}-lambda-basic-role" \
  --policy-name "${APP_NAME}-${STAGE}-bedrock-analysis" \
  --profile "$AWS_PROFILE" \
  --output json > .build/staging-bedrock-policy-check.json

python3 bin/jm8_bedrock_analysis_policy.py verify \
  .build/staging-bedrock-policy-check.json \
  .build/bedrock/bedrock-analysis-policy.json \
  "${APP_NAME}-${STAGE}-lambda-basic-role" \
  "${APP_NAME}-${STAGE}-bedrock-analysis" \
  "$BEDROCK_ANALYSIS_MODEL_ID" \
  "$AWS_PARTITION" "$AWS_REGION" "$EXPECTED_AWS_ACCOUNT_ID" "$APP_NAME" "$STAGE"
```

Require Lambda `Active`, the expected role, Python 3.12, timeout 28, memory 512, required safe environment keys, and no plaintext Stripe fields. The policy helper must report an exact match.

### State machines

```bash
for workflow in \
  "${APP_NAME}-${STAGE}-ocr-workflow" \
  "${APP_NAME}-${STAGE}-historical-reanalysis-workflow"
do
  workflow_arn="$(aws stepfunctions list-state-machines \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --query "stateMachines[?name=='${workflow}'].stateMachineArn | [0]" \
    --output text)"

  aws stepfunctions describe-state-machine \
    --state-machine-arn "$workflow_arn" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --query '{Status:status,Type:type,Role:roleArn,Logging:loggingConfiguration,Tracing:tracingConfiguration}' \
    --output json
done
```

Require `ACTIVE`, `STANDARD`, the exact stage role, `ERROR`, execution-data logging exactly false, one exact stage log destination ending in one `:*`, and tracing true.

### API routes and access logs

```bash
aws apigatewayv2 get-routes \
  --api-id "$API_ID" \
  --no-paginate \
  --max-results 500 \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Items[].{Route:RouteKey,Auth:AuthorizationType,Authorizer:AuthorizerId,Target:Target}' \
  --output table

aws apigatewayv2 get-stage \
  --api-id "$API_ID" \
  --stage-name '$default' \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query '{AutoDeploy:AutoDeploy,AccessLog:AccessLogSettings,DetailedMetrics:DefaultRouteSettings.DetailedMetricsEnabled}' \
  --output json
```

Require exactly one of every route declared in `create-api`, one Lambda integration target, JWT on all routes except `POST /billing/webhook`, `NONE` on that webhook, auto-deploy, detailed metrics, and the stage API access-log destination.

### Frontend hosting

```bash
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}' \
  --output json

aws cloudfront get-distribution \
  --id "$CLOUDFRONT_DISTRIBUTION_ID" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Distribution.{Status:Status,Domain:DomainName,Enabled:DistributionConfig.Enabled,Origin:DistributionConfig.Origins.Items[0],Cache:DistributionConfig.DefaultCacheBehavior.CachePolicyId}' \
  --output json
```

Require a complete stack, `Deployed` and enabled distribution, the exact private S3 regional origin, nonempty OAC ID, managed caching policy ID from the template, and no S3 website configuration.

### Observability and SNS

```bash
aws cloudwatch list-dashboards \
  --dashboard-name-prefix "${APP_NAME}-${STAGE}" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --output table

aws cloudwatch describe-alarms \
  --alarm-name-prefix "${APP_NAME}-${STAGE}" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue,Namespace:Namespace,Metric:MetricName}' \
  --output table

aws sns list-subscriptions-by-topic \
  --topic-arn "arn:${AWS_PARTITION}:sns:${AWS_REGION}:${EXPECTED_AWS_ACCOUNT_ID}:${APP_NAME}-${STAGE}-alerts" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Subscriptions[].{Protocol:Protocol,Status:SubscriptionArn}' \
  --output table
```

Require both dashboards, stage-prefixed alarms, custom namespaces containing `/staging/`, and a confirmed subscription ARN rather than a pending marker. A newly deployed alarm may be `INSUFFICIENT_DATA` until its first evaluation; investigate rather than forcing state.

## 7. Functional smoke tests

Never use real journal content. Record the timestamp, synthetic test identifier, expected result, actual result, and cleanup owner.

### Frontend

1. In a private browser window, open the exact `FRONTEND_ORIGIN`.
2. Confirm the staging edge gate rejects missing/incorrect input.
3. Enter the securely supplied staging value and require HTTP 200 for `/` and `/archive`.
4. Confirm a hard refresh of `/archive` renders the SPA rather than an S3 error.

`deploy-frontend` automates the HTTP status portion; the browser check validates rendering.

### Cognito

1. Select sign in from the staged frontend.
2. Require redirect to the stage Cognito domain, not dev or localhost.
3. Complete sign-in with the designated staging test user.
4. Require redirect to the exact staging callback and a visible authenticated state.
5. Sign out and require the exact staging logout redirect.

### Secured API

1. While signed out, trigger a normal protected read from the UI and require rejection.
2. Sign in again and repeat; require a successful response for the same user.
3. Inspect the API access log by request time/route and confirm no token or journal body is logged.

### Public Stripe webhook

1. Confirm route configuration shows `NONE` only for `POST /billing/webhook`.
2. Send an unsigned synthetic empty request to the endpoint and require a signature-related 4xx response, not a JWT rejection or 5xx.
3. Send a signed test event from Stripe Workbench and require a successful delivery.
4. Confirm the API log records the operational outcome without the signing material or full event body.

### Checkout and Customer Portal

1. Use a FREE staging test account and select Upgrade.
2. Require redirect to Stripe test Checkout; complete with Stripe test data.
3. Require return to the configured success URL.
4. Poll the UI entitlement until it shows Pro/active from Stripe.
5. Open Customer Portal and require the mapped customer session.
6. Exercise scheduled cancellation and subscription cancellation in test mode; confirm entitlement lifecycle follows the webhook events.

### Manual analysis

1. Create a synthetic typed entry with no personal data.
2. Analyze it from the UI.
3. Require `journal_analysis_completed`, a versioned result, nonnegative token counts, and latency metadata.
4. Require `${APP_NAME}-${STAGE}-analysis-failed` to remain or recover to `OK`.

### OCR

1. Upload a synthetic image containing harmless text through the UI.
2. Start OCR and require `PENDING`, then `COMPLETED`.
3. Confirm line/word counts and transcript appear.
4. Edit and save the review; require analysis state to reset as designed.
5. Confirm the Step Functions execution succeeded and `ocr_workflow_completed` appears.

### Historical dry run

1. Open the historical analysis panel as the staging test user.
2. Run dry run only.
3. Record eligible/evaluated counts.
4. Confirm no state-machine execution and no entry analysis mutation occurred.

### Historical execution

1. Create or identify exactly one eligible synthetic entry.
2. Submit a job constrained by the UI/API contract to that controlled test scope.
3. Require API acceptance, state-machine `SUCCEEDED`, job `COMPLETED`, one processed/completed entry, and zero failed entries.
4. Confirm the entry has a new historical analysis version and the worker/coordinator operational events exist.

### Observability and SNS

1. Confirm recent API, OCR, historical, billing, and AI signals appear in the stage dashboards.
2. Confirm every custom metric used by staging contains `JM8/staging/`.
3. Under an approved test window, trigger one safe synthetic failure covered by an alarm.
4. Require transition to `ALARM`, delivery to the confirmed SNS recipient, then natural recovery to `OK` after the signal clears.
5. Do not leave a forced alarm state or test subscription behind.

## 8. Troubleshooting decision trees

### CloudFormation early validation

```text
Did create-frontend-hosting fail before stack events appeared?
├─ Yes: inspect its sanitized diagnostic report and AWS CLI stderr classification.
│  ├─ Template/parameter validation: validate local template and exact parameter names.
│  ├─ Credential/account/network error: stop; do not treat the stack as absent.
│  └─ “stack does not exist” from preflight only: creation path is expected.
└─ No: inspect describe-stack-events for the first failing resource, not only the final status.
```

Use the script's local-template and deployed-template checks. Do not manually create individual stack resources; that would cause drift.

### Missing CloudFormation output

```text
Is the output key present in the local template?
├─ No: wrong commit/template; stop.
└─ Yes: is it present in the deployed template?
   ├─ No: stack is stale or wrong; redeploy reviewed template.
   └─ Yes: inspect stack status and output value.
      ├─ Missing/None/malformed: stop; do not fabricate an environment value.
      └─ Valid: export the literal value with an AWS query.
```

Required keys are `FrontendBucketName`, `FrontendBucketRegionalDomainName`, `DistributionId`, `DistributionDomainName`, `FrontendOrigin`, and `OriginAccessControlId`.

### IAM eventual consistency

```text
Did historical create/update report both the exact access-denied class
and “state machine IAM Role is not authorized to access the Log Destination”?
├─ Yes: the script retries 5 attempts with waits 5, 10, 20, 30 seconds.
│  ├─ Eventually succeeds: continue to final describe-state-machine verification.
│  └─ Exhausts: command fails; wait for IAM convergence, then rerun idempotently.
└─ No: it is unrelated and non-retryable; stop and diagnose credentials, policy, ARN, or input.
```

Do not broaden IAM, replace the role, or suppress the final error. A workflow is ready only after the AWS write and final postconditions succeed.

### Missing Lambda execution role

```text
Which Lambda is affected?
├─ API Lambda: run guarded create-resources; verify lambda-basic-role and trust.
├─ OCR worker/failure handler: run deploy-ocr-workflow.
└─ Historical worker/coordinator: run deploy-historical-reanalysis-workflow.
```

An IAM access error is not a missing role. Inspect `get-role` stderr and create only through the owning script.

### Missing Bedrock policy

```text
Does the API role lack ${APP_NAME}-${STAGE}-bedrock-analysis?
├─ Before API deploy: run deploy; it must reconcile and verify policy before Lambda write.
├─ Standalone setup needed: run configure-bedrock-analyzer, then deploy normally.
└─ Policy exists but analysis fails: compare exact profile ARN, destinations, condition,
   account, region, model access, and applied-policy verification.
```

Do not add broad Bedrock permissions. Historical success does not prove the API role is correct because the historical worker uses a different role.

### Invalid AWS CLI waiter

```text
Does AWS CLI say the waiter name is invalid?
├─ CloudFront invalidation: use `aws cloudfront wait invalidation-completed`.
├─ Lambda: use the exact waiter already present in the owning script.
└─ Other service: inspect `aws <service> wait help`; do not guess or loop forever.
```

Confirm AWS CLI v2 and avoid the nonexistent CloudFront `invalidation-deployed` waiter.

### Cross-stage metric collision

```text
Does a staging graph include dev/prod data?
├─ Namespace lacks /staging/: metric filter/dashboard is stale; redeploy observability.
├─ Namespace is correct but dimensions reference another stage: stop and repair definition.
└─ Both correct: verify selected console region/time range and alarm metric identity.
```

Never use legacy shared `JM8/OCR`, `JM8/HistoricalReanalysis`, `JM8/Billing`, or `JM8/AI` namespaces.

### Missing or expired JWT

```text
Does a protected call return 401?
├─ Signed out/no token: sign in through staging Cognito.
├─ Expired token: sign out, clear the app session through normal logout, sign in again.
├─ Wrong issuer/audience: compare API authorizer with generated staging Cognito values.
└─ Webhook route: it must not require JWT; inspect route authorization.
```

Never paste or decode a real token into shared tools. Use claims shown by approved local/browser diagnostics only.

### Malformed URL copied as Markdown

```text
Does URL validation or a callback fail?
├─ Value contains `[`, `]`, `(`, `)`, backticks, quotes, spaces, or trailing punctuation:
│  replace it with the literal HTTPS URL from the AWS output.
├─ Value has path/query/fragment where FRONTEND_ORIGIN is required: use origin only.
└─ Value is localhost in staging: stop and use the staging HTTPS endpoint.
```

Code blocks preserve literal URLs; copying rendered link text and markup together does not.

### Missing exported environment variables

```text
Did a script say a variable is required?
├─ Base variable: source backend/.env in the current shell.
├─ Cognito value: source generated/${STAGE}.cognito.env after create-auth.
├─ Stripe ARN: source generated/${STAGE}.stripe.env after provision.
├─ Safe Stripe catalog value missing after provision: restore it from the approved base
│  contract/current shell; do not put a secret in the generated file.
└─ Frontend Vite value: export from exact API/Cognito/CloudFront outputs.
```

Use `source`, not `./file`. Check names/presence without printing values.

### Pending SNS subscription

```text
Does list-subscriptions show a pending marker?
├─ Yes: recipient opens the AWS confirmation message and confirms the exact staging topic.
│  ├─ Message expired: rerun deploy-observability with the approved recipient.
│  └─ No message: check spam/quarantine and topic/recipient spelling.
└─ No: confirm the subscription ARN and run an approved alarm-transition test.
```

A pending subscription cannot receive alarm or budget notifications.

## 9. Rollback and stop conditions

### Stop immediately when

- STS account, profile, region, stage, confirmation, or resource name is wrong;
- a credential, access, network, malformed-response, or unexpected AWS error occurs;
- a script cannot distinguish absence from access failure;
- an applied IAM policy differs from the generated policy;
- a state-machine role/logging/tracing postcondition fails;
- any secured route is public or the webhook is JWT-protected;
- frontend origin/OAC/bucket output does not match;
- plaintext secret material appears in an environment file, Lambda configuration, logs, or Git;
- tests fail, the worktree is not the reviewed source, or a smoke test risks real user data;
- any alarm indicates an unexplained active incident.

### Rollback principles

1. Stop further stages of the sequence; do not stack additional changes on a failed postcondition.
2. Preserve sanitized command output, timestamps, request IDs, state-machine execution ARNs, stack events, and alarm history.
3. Redeploy the last known-good reviewed commit through the same idempotent owning scripts. Do not delete resources.
4. For the API Lambda, rebuild and redeploy the known-good package; the current script does not manage aliases or an automated version rollback.
5. For workflows, deploy the known-good ASL and worker package to the existing state-machine ARN and roles. Let in-flight STANDARD executions finish or use an explicitly approved execution stop; do not delete the state machine.
6. For the frontend, redeploy known-good assets and complete a CloudFront invalidation. Bucket versioning is a recovery aid, not an automatic release rollback.
7. Let CloudFormation perform its native rollback. If it is stuck, diagnose stack events before any continue/rollback operation.
8. Stripe product/price and webhook changes require a billing-owner decision. Do not delete catalog objects or rotate secrets merely to undo application code.
9. DynamoDB PITR and S3 versioning/lifecycle are protection mechanisms. Restoration must go to a controlled target and be verified before any cutover.
10. After rollback, repeat every relevant postcondition and smoke test, confirm alarms recover, and document the incident.

## 10. Related documentation

- [JM8 cloud architecture](JM8_CLOUD_ARCHITECTURE.md)
- [JM8 production promotion checklist](JM8_PRODUCTION_PROMOTION_CHECKLIST.md)
