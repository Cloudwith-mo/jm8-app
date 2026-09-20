# JM8 enterprise deployment pipeline

The `Staged deployment` workflow converts the existing fail-closed deployment
scripts into a controlled GitHub Actions release path. It does not provision
foundational infrastructure, Stripe resources, GitHub environments, or AWS
roles. Those remain explicit bootstrap operations.

## Release guarantees

- Releases are manually dispatched from the selected GitHub ref. The entered
  40-character commit SHA must exactly match GitHub's resolved workflow commit;
  it is confirmation only and is never used as an untrusted checkout ref.
- The commit must already be contained in `main`.
- Backend and frontend tests run before a release artifact is created.
- The backend ZIP is checksummed, described by a manifest, and retained for 90
  days.
- Deployment downloads and verifies that artifact before AWS authentication.
- AWS authentication uses GitHub OIDC and short-lived role credentials. Static
  AWS access keys are not accepted by the workflow.
- GitHub Environments provide stage isolation and the production approval gate.
- Only one deployment per stage may run at a time, and an active deployment is
  never cancelled by a newer run.
- The existing JM8 environment contract runs before mutation.
- Production remains blocked until the `jm8-prod` environment variable
  `PRODUCTION_RELEASE_STATE` is explicitly set to `verified` after the
  production promotion checklist is complete.
- A sanitized evidence artifact records the release commit, package digest,
  stage, and final Lambda state.

## Required GitHub Environments

Create `jm8-dev`, `jm8-staging`, and `jm8-prod`. Configure an environment
protection reviewer for production and prevent self-review when the repository
plan supports it. Restrict production deployment branches/tags to `main` and
approved release tags.

Each environment requires the safe configuration variables used by its
matching file under `backend/infra/environments/`. The workflow reads these as
GitHub Environment variables:

`AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `EXPECTED_AWS_ACCOUNT_ID`, `TABLE_NAME`,
`ENTRY_CHUNKS_TABLE_NAME`, `RAW_BUCKET`, `EXPORT_BUCKET`, `FRONTEND_BUCKET`,
`CLOUDFRONT_DISTRIBUTION_ID`, `FRONTEND_ORIGIN`, `ALLOWED_ORIGINS`, `API_NAME`,
`API_ENDPOINT`, `COGNITO_USER_POOL_NAME`, `COGNITO_USER_POOL_ID`,
`COGNITO_APP_CLIENT_ID`, `COGNITO_DOMAIN`, `COGNITO_ISSUER`, `CALLBACK_URL`,
`LOGOUT_URL`, `BEDROCK_ANALYSIS_MODEL_ID`, `ANALYSIS_SCHEMA_VERSION`,
`ANALYSIS_PROMPT_VERSION`, `STRIPE_SECRET_ARN`,
`STRIPE_PRO_MONTHLY_PRICE_ID`, `STRIPE_CHECKOUT_SUCCESS_URL`,
`STRIPE_CHECKOUT_CANCEL_URL`, `STRIPE_PORTAL_RETURN_URL`,
`SEMANTIC_MEMORY_MAPPING_ENABLED`, and `SEMANTIC_EMBEDDING_MAPPING_ENABLED`.

`jm8-prod` additionally requires `PRODUCTION_ISOLATION_MODE` and, only after a
formal go-live decision, `PRODUCTION_RELEASE_STATE=verified`.

`jm8-staging` requires the Environment secrets
`STAGING_BASIC_AUTH_USERNAME` and `STAGING_BASIC_AUTH_PASSWORD`. Do not create
repository-wide copies of those secrets.

## AWS OIDC trust boundary

Create one least-privilege deploy role per stage. Its trust policy must accept
`sts:AssumeRoleWithWebIdentity` only from GitHub's OIDC provider, require the
audience `sts.amazonaws.com`, and restrict the subject to the exact repository
and environment, for example:

```text
repo:Cloudwith-mo/jm8-app:environment:jm8-staging
```

The role permissions must be derived from the services and resources already
validated by the JM8 deployment policy tests. Do not attach AdministratorAccess
and do not reuse the production role in dev or staging.

## Promotion and rollback

1. Merge a reviewed PR after every required CI and CodeQL check passes.
2. Select `main` (or an approved immutable release tag) when dispatching the
   workflow and enter its exact resolved commit SHA.
3. Dispatch `Staged deployment` for `dev` with confirmation `deploy-dev`.
4. Review sanitized evidence and perform the relevant smoke tests.
5. Dispatch the same SHA for `staging` with `deploy-staging`; complete the V2
   staging release checklist.
6. Production stays blocked until the production checklist and GitHub
   Environment approval are complete. Then dispatch the same SHA with
   `deploy-prod`.

Rollback is a forward redeployment: dispatch the last verified `main` commit
for the affected stage, then repeat postconditions and smoke tests. Never
delete durable DynamoDB, S3, Cognito, Stripe, or workflow resources as a release
rollback.

## Current boundary

This pipeline deploys application code, API routes/authorization, and—when
selected—staging or production frontend assets to already provisioned stage
infrastructure. Initial resource creation, Stripe webhook bootstrap, semantic
mapping activation, broad historical processing, and production go-live remain
separate explicitly approved operations.
