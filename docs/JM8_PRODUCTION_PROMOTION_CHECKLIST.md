# JM8 Production Promotion Checklist

**Current decision: NO-GO. Production is not deployed or ready.**

By owner decision, the initial production deployment may share AWS account `114743615542` with dev and staging, provided production uses exact stage-scoped resources and the separate `jm8-prod` deployment profile/role. Separate-account isolation is deferred, not abandoned. The initial production frontend may use its generated CloudFront HTTPS hostname; custom-domain, ACM-certificate, and Route 53 integration are deferred. Production must not use the staging Basic Auth function. Production hosting automation is implemented but remains unverified until supported by actual production deployment evidence, and the other blocked gates below remain unresolved. No checklist item may be inferred complete from staging alone.

## Status legend

| Status | Meaning |
| --- | --- |
| **Required** | Must be completed and evidenced before go-live |
| **Verified — staging only** | Accepted in staging; must be repeated in production |
| **Blocked** | Cannot proceed until a named prerequisite or implementation gap is resolved |
| **Deferred** | Explicitly accepted for after launch with owner, risk, and due date |
| **Owner decision** | Product/security/operations owner must choose and record an option |

Every production item needs an evidence link, verifier, verification time, and result. Replace statuses only after reviewing actual production evidence.

## 1. Staging evidence baseline

These results demonstrate that the design can work; they do not certify production.

| Gate | Status | Staging evidence supplied with this documentation |
| --- | --- | --- |
| Private frontend hosting | Verified — staging only | Private S3, CloudFront, OAC, and staging Basic Auth passed |
| Identity and API authorization | Verified — staging only | Cognito login, JWT authorization, secured routes, and public webhook passed |
| Data protection | Verified — staging only | DynamoDB and raw/frontend S3 resources were deployed and hardened |
| Bedrock | Verified — staging only | API least-privilege policy and manual journal analysis passed |
| Stripe lifecycle | Verified — staging only | Test Checkout, webhook, Portal, persistence, cancellation, and Pro entitlement passed |
| OCR and historical workflows | Verified — staging only | Secure workflow logging/tracing and one-record historical run passed |
| Observability | Verified — staging only | Stage metrics, dashboards, alarms, 30-day retention, SNS delivery, transition, and recovery passed |
| Secret hygiene | Verified — staging only | No plaintext Stripe secret in generated environment files |
| Release baseline | Verified — staging only | Supplied evidence states `main` and `staging` referenced `d988997` |

## 2. Release governance and account isolation

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Separate production AWS account | Deferred | Owner accepted stage-scoped same-account isolation for initial production. Record the owner, risk acceptance, compensating controls, migration trigger, and due date for revisiting separate-account isolation |
| Same-account stage isolation | Required | Allow only `journalm8-prod-*` production resources (including exact table, API, user-pool, and account-scoped bucket names) plus `journalm8/prod/stripe`; reject dev, staging, unrelated, cross-account, and cross-region AWS resources |
| Production environment contract | Required | Use the reviewed fail-closed production contract; prove all exact production controls pass before any deployment mutation |
| Production confirmation guard | Required | Require exact `STAGE=prod`, `DEPLOY_CONFIRMATION=prod`, `AWS_PROFILE=jm8-prod`, account `114743615542`, region, and stage-scoped names before any mutation |
| Production AWS identity | Required | Record read-only STS evidence that the `jm8-prod` principal and `${EXPECTED_AWS_ACCOUNT_ID}` are both account `114743615542` |
| Separate production deploy role/profile | Required | Provision and use only the distinct `jm8-prod` role/profile for production; do not use the dev or staging deployment identity |
| Least-privilege deploy principal | Required | Provision only `journalm8-prod-deployer` through `jm8-dev`; attach the six reviewed, size-checked customer-managed policy groups, verify exact default versions and attachment isolation, require `App=journalm8`/`Stage=prod` tags wherever an AWS API exposes only opaque resource IDs, and restrict untaggable CloudFront OAC writes to CloudFormation-forwarded calls |
| Environment file | Required | Create an ignored production base environment from `backend/infra/environments/prod.env.example`; include safe configuration only |
| Stage resource names | Required | Require `${APP_NAME}-prod-*`, `${APP_NAME}-prod-main`, and account-scoped production buckets; reject dev/staging references |
| Commit promotion | Required | Tag or otherwise immutably identify the exact reviewed commit; require clean worktree and passing release checks |
| Change window and owners | Required | Name release lead, security approver, billing owner, incident lead, and rollback decision-maker |
| Staging regression | Required | Repeat the complete staging runbook against the exact production candidate before promotion |

## 3. Cognito, domains, and TLS

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Production user pool | Blocked | Create a separate `${APP_NAME}-prod-users`; do not reuse staging users or pool |
| Production app client | Blocked | Create a separate `${APP_NAME}-prod-web`, no client secret, authorization-code flow with PKCE |
| Production Cognito domain | Blocked | Create a production-only Hosted UI domain and record issuer/client IDs in the ignored production generated file |
| Callback URLs | Required | Allow only exact production HTTPS callback URLs; verify the effective existing-client configuration because `create-auth` does not reconcile an existing client's URLs |
| Logout URLs | Required | Allow only exact production HTTPS logout URLs; reject localhost and staging origins |
| JWT authorizer | Required | Verify issuer and audience match only the production pool/client; every declared route except the webhook must be JWT-secured |
| Custom frontend domain | Deferred | Owner accepted the generated CloudFront HTTPS hostname for initial launch; record the owner, risk acceptance, and due date for custom-domain and Route 53 integration |
| ACM certificate | Deferred | Not required while the generated CloudFront hostname is used; request and validate the CloudFront-region certificate before adding a custom domain |
| Explicit TLS policy | Required if custom domain | Configure and verify an approved minimum TLS/security policy after the alternate domain and ACM certificate are attached |
| Cognito custom domain | Owner decision | Decide whether the Hosted UI also needs a branded custom domain; document certificate/DNS impact |

## 4. Frontend hosting and delivery

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Production hosting automation | Required | The stage-aware production path is implemented; remain unverified until the exact production stack, bucket, OAC, distribution, tags, outputs, and deployment postconditions have actual production evidence |
| Dedicated frontend bucket | Required | Use a production/account-scoped bucket distinct from raw uploads; block all public access, enforce ownership, encryption, HTTPS, versioning, and lifecycle |
| CloudFront OAC | Required | Permit reads only from the exact production distribution through OAC/SigV4; verify no public S3 website path |
| SPA behavior | Required | Verify default root and 403/404 fallback to `/index.html` without caching errors |
| Cache policy | Required | Keep hashed assets immutable and long-lived, `index.html` no-store, and non-hashed content revalidating |
| Invalidation | Required | Use `invalidation-completed`; verify a release is visible globally before traffic acceptance |
| Staging Basic Auth behavior | Required | Preserve staging Basic Auth exactly; production Basic Auth is explicitly rejected and the production distribution must have no viewer-request function association |
| Frontend build contract | Required | Build with production API/Cognito values only; scan `dist` for source maps, secrets, staging endpoints, and localhost |
| CloudFront logging/WAF | Deferred or owner decision | Decide before launch; if deferred, record abuse/forensics risk, owner, and due date |

## 5. API and Lambda

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Production API | Required | Create `${APP_NAME}-prod-api`, one Lambda proxy integration, `$default` auto-deploy stage, and exact route inventory |
| Production CORS | Required | Allow only the exact production frontend origin(s); no staging, dev, wildcard, or localhost origin |
| Public-route exception | Required | Verify only `POST /billing/webhook` has authorization type `NONE`; all other declared routes use the production JWT authorizer |
| API execution role | Required | Attach policies only to `${APP_NAME}-prod-lambda-basic-role`; verify trust and exact stage/account resources |
| Lambda environment | Required | Require production table/bucket/workflow/model/version/usage/Stripe reference values and no plaintext secret values |
| Timeout alignment | Required | Verify API Lambda 28 seconds and HTTP API integration 30 seconds remain appropriate after load tests |
| API access logs | Required | Use `/aws/apigateway/${APP_NAME}-prod-api`, structured fields, detailed metrics, resource policy, and 30-day retention |
| Route postconditions | Required | Run create/secure postconditions and independently inventory route count, integration target, and authorization |

## 6. DynamoDB, S3, backup, and retention

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Production main table | Required | Create `${APP_NAME}-prod-main` with `PK`/`SK`, GSI1, on-demand billing, tags, and `ACTIVE` status |
| DynamoDB PITR | Required | Verify point-in-time recovery is enabled and record the available recovery window |
| DynamoDB deletion protection | Required | Verify enabled before application traffic |
| Raw S3 bucket | Required | Use `${APP_NAME}-prod-raw-${EXPECTED_AWS_ACCOUNT_ID}` with public-access block, encryption, HTTPS-only policy, stage tags, and exact CORS |
| S3 versioning | Required | Verify enabled on raw and frontend buckets |
| S3 lifecycle | Required | Verify abort-incomplete-multipart at seven days and noncurrent-version expiration at 30 days; reconcile with approved retention policy |
| Backup restore test | Required | Restore a controlled DynamoDB point and representative S3 object versions to isolated recovery targets; verify integrity and documented RTO/RPO |
| Data deletion test | Required | Verify user-entry deletion, object deletion/version behavior, entitlement/history retention, and audit expectations |
| Cross-account backup | Deferred or owner decision | Decide whether launch risk requires a separate backup account; record compensating controls if deferred |

## 7. Stripe live billing

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Live-mode account readiness | Blocked | Billing owner must approve legal/business settings, tax, branding, support, statement descriptor, and payout access |
| Live product and price | Required | Run the idempotent catalog setup only after its production path is approved; verify JM8 Pro amount, currency, interval, lookup key, and live mode |
| Live API credential | Required | Inject only for catalog/secret provisioning; never store in `.env`, generated files, Lambda environment, logs, tickets, or frontend |
| Secrets Manager object | Required | Create `${APP_NAME}/prod/stripe` in the production account; verify ARN, tags, encryption policy, and exact API-role read permission |
| Live webhook endpoint | Required | Create the exact production HTTPS endpoint and subscribe only to handled events |
| Live webhook signing material | Required | Store with the API credential in Secrets Manager; production deployment must reject a missing or malformed webhook field |
| Secret runtime validation | Required | Verify the API role can retrieve only the exact secret and the application accepts the production mode without printing values |
| Return URLs | Required | Verify production-only Checkout success/cancel and Portal return URLs; no staging or localhost values |
| End-to-end live test | Owner decision | Approve a minimal real transaction/refund/cancellation plan, accounting treatment, and cleanup before executing it |
| Webhook replay/idempotency | Required | Verify duplicate and out-of-order subscription events preserve correct customer mapping and entitlement state |

## 8. Bedrock, OCR, and historical workflows

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Bedrock regional/model access | Blocked | Confirm the selected production region/account can resolve and invoke the configured inference profile and every destination model |
| API Bedrock policy | Required | Reconcile `${APP_NAME}-prod-bedrock-analysis` on only the production API role before Lambda create/update; exact profile, destinations, actions, and condition only |
| Applied-policy verification | Required | Retrieve and compare the inline policy; reject extra actions/resources, wildcard permissions, wrong account, wrong region, or wrong stage |
| Analysis versions | Required | Freeze and record `BEDROCK_ANALYSIS_MODEL_ID`, `ANALYSIS_SCHEMA_VERSION`, and `ANALYSIS_PROMPT_VERSION` for the release |
| Model quality/safety | Required | Evaluate synthetic and approved representative data for schema validity, harmful output, prompt injection, latency, and retry classification |
| OCR workflow | Required | Deploy the production STANDARD workflow, workers/roles, 30-day log group, ERROR logging without execution data, one destination, and tracing |
| Textract access | Required | Verify exact worker path and expected `DetectDocumentText` access; validate synthetic handwriting/image behavior |
| Historical workflow | Required | Deploy production worker/coordinator/roles and STANDARD state machine with `MaxConcurrency: 2`, secure logs, tracing, and postconditions |
| IAM propagation handling | Required | Confirm only the exact historical log-destination propagation denial retries and exhaustion remains a hard failure |
| Historical dry run | Required | Run against a controlled production test user before any job; verify inventory without mutation |
| Historical execution | Required | Run one controlled synthetic entry; require job `COMPLETED`, execution `SUCCEEDED`, and zero failed entries before broader use |
| Broad reanalysis authorization | Owner decision | Define who may start large jobs, approval threshold, maintenance window, and cost/rollback stop criteria |

## 9. Observability, alerting, and cost

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Stage-isolated custom metrics | Required | Require only `JM8/prod/OCR`, `JM8/prod/HistoricalReanalysis`, `JM8/prod/Billing`, and `JM8/prod/AI`; prove no staging/dev collision |
| Operations dashboard | Required | Deploy and verify `${APP_NAME}-prod-operations` with API, workers, workflows, billing, log widgets, and alarm state |
| AI dashboard | Required | Deploy and verify `${APP_NAME}-prod-ai-operations` with outcome, token, latency, SDK-retry, alarm, and log views |
| CloudWatch alarms | Required | Verify every `${APP_NAME}-prod-*` alarm's metric identity, dimensions, threshold, missing-data behavior, and production SNS action |
| Log retention | Required | Verify 30 days on API, API Gateway, OCR, historical, analysis, and both Step Functions log groups; reconcile with privacy/legal policy |
| X-Ray | Required | Verify tracing enabled on both production state machines and that traces do not expose journal content |
| Production SNS topic | Required | Deploy `${APP_NAME}-prod-alerts` with scoped CloudWatch/Budgets publish policy |
| SNS recipient | Required | Use an approved on-call/team recipient, confirm the subscription, and prove alarm delivery plus recovery |
| Bedrock budget | Required | Set an owner-approved monthly USD amount; deploy `${APP_NAME}-prod-bedrock-monthly` and verify 50/80/100-percent notifications |
| Broader account budgets | Owner decision | Decide total AWS budget, anomaly detection, and escalation outside Bedrock-specific spend |
| Incident routing | Deferred or owner decision | Decide whether SNS email is sufficient at launch or integrate paging/ticketing with an owner and due date |

## 10. Security, performance, privacy, and recovery review

| Gate | Status | Production requirement and evidence |
| --- | --- | --- |
| Security review | Required | Review auth flows, public webhook, CORS, presigned upload scope, all IAM policies/trusts, secret paths, dependency scan, logging, and tenant isolation |
| No broad privilege | Required | Prove no administrator attachment, wildcard action, broad Lambda/States/Bedrock action, or wildcard resource where service-level scoping is supported |
| Secret scan | Required | Scan source, history, generated configs, artifacts, logs, and frontend output; resolve every suspected credential before go-live |
| Dependency and artifact provenance | Required | Pin/install from reviewed lockfiles, record build environment, and retain checksums of promoted backend/frontend artifacts |
| Load/performance test | Required | Exercise API concurrency, DynamoDB access patterns, presigned uploads, OCR starts, Bedrock latency, and historical cap; record p50/p95/p99 and throttles |
| Quota review | Required | Check Lambda concurrency, Step Functions, Bedrock, Textract, Cognito, API Gateway, DynamoDB, and SNS quotas against forecast and failure mode |
| Privacy review | Required | Approve journal/image/analysis classification, subprocessors, user consent, access controls, export/deletion behavior, and operational-data minimization |
| Data-retention review | Required | Approve 30-day logs, S3 noncurrent lifecycle, DynamoDB/analysis/history retention, Stripe records, and deletion/legal-hold rules |
| Disaster recovery plan | Required | Document regional/account dependencies, restore order, RTO/RPO, communications, and degraded operation |
| Rollback rehearsal | Required | Rehearse known-good Lambda/workflow/frontend rollback without deleting durable resources; verify postconditions and alarm recovery |
| Runbook ownership | Required | Assign owner/reviewer and next review date for the staging runbook and this checklist |

## 11. Final go/no-go record

All **Blocked** items must be resolved. Every **Required** item must be verified with production evidence. Every **Deferred** item needs written risk acceptance, owner, compensating control, and due date. Every **Owner decision** must have a recorded outcome.

| Approval | Status | Required record |
| --- | --- | --- |
| Release lead | Blocked | Candidate commit/artifact checksums, completed deployment plan, and rollback authority |
| Security owner | Blocked | Signed security/privacy/IAM/secret review |
| Operations owner | Blocked | Monitoring, confirmed alerts, incident response, backup restore, and rollback evidence |
| Billing owner | Blocked | Live catalog, secret, webhook, test/financial controls, and customer-support readiness |
| Product/data owner | Blocked | CloudFront-hostname launch and no-production-Basic-Auth decisions are recorded; model quality, retention, user communication, and deferred custom-domain ownership still require approval/evidence |
| Final go/no-go | Blocked | Timestamped unanimous approval after all gates above; otherwise remain NO-GO |

Phase 1 environment-contract support does not authorize production deployment. Production remains **NO-GO** until every other **Blocked** gate is resolved, every **Required** gate is evidenced, every deferral is accepted, and the final status is explicitly changed to **Verified** by the named owners.

## 12. Related documentation

- [JM8 cloud architecture](JM8_CLOUD_ARCHITECTURE.md)
- [JM8 staging operations runbook](JM8_STAGING_OPERATIONS_RUNBOOK.md)
