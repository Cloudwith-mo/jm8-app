# JM8 User Data Contract

Status: privacy operations contract for the JM8 private beta

Effective date: August 28, 2026

This document maps user-related data across JM8 and defines the intended scope
and order for account export and deletion workflows. It documents the current
storage model, the Phase 3C2 in-app export boundary, and the Phase 3C3 account
deletion coordination, Phase 3C3B execution contract, and Phase 3C3C
user-experience and deployment integration. The deployment source activates
account deletion only through the guarded, stage-scoped deployment path.

## Identity boundary

The authenticated Cognito subject (`sub`) is the canonical JM8 `user_id`.
Cognito stores the sign-in identity, including the subject, email, verified
attributes, and optional display name. Application data refers to that stable
subject rather than using an email address as a database key.

Authentication credentials, password material, authorization codes, access
tokens, refresh tokens, and PKCE values are not application export data. They
must never be written into an export artifact.

## DynamoDB user partition

Most application records are colocated in the partition:

```text
PK = USER#<user_id>
```

The partition currently includes these record families:

| Sort-key family | Data represented |
| --- | --- |
| `ENTRY#<created_at>#<entry_id>` | Typed or image-backed journal entries, source metadata, OCR state and text, review state, current analysis, and the S3 object reference when applicable. |
| `ANALYSIS#<entry_id>#...` | Versioned analysis results and their model, prompt, schema, source, and completion metadata. |
| `ASK_HISTORY#<created_at>#<history_id>` | Ask JM8 question/answer history and the journal context references stored with an answer. |
| `USAGE#<YYYY-MM>` | Monthly Ask JM8 and entry-analysis usage counters. |
| `USAGE_RESERVATION#<YYYY-MM>#<reservation_id>` | Short-lived quota reservations used to make usage enforcement concurrency-safe. |
| `ENTITLEMENT` | Free or Pro entitlement, subscription state, access dates, and cancellation state. |
| `BILLING#STRIPE#CUSTOMER` | The user's Stripe customer mapping and private billing reference. |
| `REANALYSIS_JOB#...`, `REANALYSIS_PAGE#...`, `REANALYSIS_ACTIVE` | Historical re-analysis jobs, page progress, results metadata, and the active-job lock. |
| `ACCOUNT_EXPORT#exp_...`, `ACCOUNT_EXPORT_ACTIVE`, `ACCOUNT_EXPORT_REQUEST#...` | Short-lived export status, the one-active-job lock, and request idempotency coordination. Internal coordination fields are excluded from packages. |

Global secondary index keys attached to these records are indexes, not separate
user content. Export should expose meaningful record fields without exposing
internal DynamoDB keys where those keys add no user value.

## Stripe customer reverse lookup

The user partition contains the forward Stripe customer mapping. A separate
reverse-lookup item resolves a Stripe webhook customer back to a JM8 user:

```text
PK = STRIPE_CUSTOMER#<TEST|LIVE>#<stripe_customer_id>
SK = USER
```

The reverse lookup contains a private, hashed JM8 billing user reference and
the identifiers needed to reconcile Stripe events. It must be located and
deleted as part of account deletion, but raw lookup keys and internal hashes do
not need to appear in a user-facing export. A readable subscription summary
should be exported from the user entitlement and customer mapping instead.

## S3 uploaded journal images

Uploaded source images are stored beneath the user-specific prefix:

```text
users/<user_id>/uploads/<entry_id>/<sanitized_filename>
```

The bucket is versioned. A normal object deletion creates a delete marker, so
account deletion must enumerate and delete every current version, noncurrent
version, and delete marker under the prefix. The configured lifecycle removes
noncurrent versions after 30 days; therefore deleted versions may remain
recoverable for up to 30 days.

## CloudWatch logs

CloudWatch contains Lambda application logs, workflow logs, and API Gateway
access logs. Logs can include timestamps, event names, request identifiers,
route and response status, latency, failure codes, and API access source IPs.
Application and API access log groups have a 30-day retention policy.

Logs are operational streams rather than the system of record for journal
content. They are intentionally excluded from routine user exports and are not
expected to support per-user deletion. Privacy-sensitive values must not be
intentionally logged; retained log events expire under the 30-day policy.

## Backup and version-retention windows

- DynamoDB point-in-time recovery is enabled. Deleted or changed table records
  may remain recoverable in protected backups for up to 35 days.
- The S3 raw-image bucket is versioned. Deleted object versions may remain for
  up to 30 days under the noncurrent-version lifecycle rule.
- CloudWatch application and API access logs are retained for 30 days.
- Stripe controls retention of its own transaction and payment records under
  its legal and regulatory obligations.

Backups and noncurrent versions are not active application data. They must not
be restored except for a legitimate disaster-recovery need, and any restore
procedure must reapply completed deletion requests before returning the data to
active use.

## Account export contents

The worker limits downloaded source images to 2 GiB and the completed ZIP to
3 GiB. Its configured ephemeral storage is 6 GiB, so the source and archive
ceilings satisfy `MAX_SOURCE_BYTES + MAX_ARCHIVE_BYTES < ephemeral storage`
while both coexist during ZIP construction. The archive ceiling also satisfies
`MAX_ARCHIVE_BYTES < the 5 GB single-request PutObject limit`, ensuring
capacity failures are reported as `ExportTooLarge` before disk or upload
limits are exhausted.

The portable export is assembled asynchronously only after identity
verification and contains:

1. account profile fields suitable for disclosure, including Cognito subject,
   verified email when available, optional display name, account status, and
   creation date;
2. all journal entries, timestamps, source type, OCR transcript and corrections,
   review state, and user-visible metadata;
3. uploaded journal images in their original stored format, organized by entry;
4. current and historical analysis results, including available model, prompt,
   schema, and completion metadata;
5. Ask JM8 questions, answers, timestamps, status, and user-visible context
   references;
6. historical re-analysis job outcomes that are meaningful to the user;
7. monthly usage totals and current Free or Pro entitlement details; and
8. a readable subscription summary containing status, access dates, cancellation
   state, and the limited Stripe customer reference held by JM8.

The manifest should state the export generation time, format version, included
files, and any record category that could not be exported. Presigned download
URLs must be short-lived and must not be embedded as permanent data.

## Data intentionally excluded from export

The routine export should exclude:

- passwords, authorization codes, access or refresh tokens, PKCE values,
  credentials, secrets, encryption material, and complete card details;
- internal DynamoDB index keys, conditional-write tokens, quota reservation
  leases, idempotency keys, private hashed billing references, and other
  implementation-only coordination fields;
- CloudWatch logs, security telemetry, abuse-detection details, infrastructure
  configuration, and data about other users;
- DynamoDB backup snapshots, S3 noncurrent versions, and delete markers;
- Amazon Bedrock or AWS internal service records not controlled by JM8; and
- Stripe's internal payment, risk, dispute, tax, and compliance records.

Exclusion from the portable export does not prevent a verified access request
from being evaluated for additional personal information when required by
applicable law.

## Phase 3C3 account deletion coordination

Phase 3C3A provides authenticated request coordination and status lookup:

```text
POST /account/deletion-requests
GET  /account/deletion-requests/{requestId}
```

POST requires the exact explicit confirmation value `DELETE_MY_ACCOUNT`, a
request token, and a recent Cognito `auth_time`. A new coordinated request
returns 202 when the account-deletion workflow ARN is configured; replaying the
same request token returns 200 without starting another execution. Phase 3C3C
requires the exact stage-scoped workflow during guarded deployment and passes
that ARN to the shared API. If this configuration is absent or mismatched, POST
fails closed with its retryable temporary-unavailable response. GET remains
available to the same authenticated Cognito subject that owns the request, and
workflow execution history is never exposed by the public API.

The durable audit is outside the deletable `USER#<user_id>` partition:

```text
PK = ACCOUNT_DELETION#<del_request_id>
SK = REQUEST
```

The audit may contain only the request ID, a one-way Cognito-subject digest,
status, lifecycle timestamps, a safe failure code and retryability flag, and a
workflow execution ARN. It must not contain the raw Cognito subject, email,
journal content, request token, Stripe customer ID, access token, secret, or
complete Cognito claims. A separate `ACCOUNT_DELETION_SUBJECT#<subject_digest>`
partition holds the active lock and request-token digest coordination. Temporary
pre-destructive coordination and failed audits use the table's configured TTL
attribute. At the destructive boundary, one atomic transaction removes TTL from
the audit, active lock, and internal billing recovery record. Those records do
not expire automatically while destructive recovery is incomplete. Completed
audits retain the minimum request ID and completion evidence needed to prevent
restored data from becoming active without reapplying deletion.

Public responses strictly exclude DynamoDB keys, the subject digest, workflow
ARN, TTL, and all other storage coordination. They disclose that CloudWatch
logs may remain for up to 30 days, S3 retained versions for up to 30 days if
immediate removal fails, DynamoDB PITR data for up to 35 days, and Stripe
financial records according to Stripe and applicable legal retention.

Phase 3C3B enforces the write-blocking boundary. GET deletion status, deletion
request replay, and necessary read-only routes remain available. Entry
creation/update/deletion, upload URLs, OCR and retries, analysis/reanalysis,
Ask JM8 creation and history deletion, billing checkout/portal, Stripe webhook
entitlement writes, and new account exports are blocked while the lock is
active. A lock read failure fails closed for mutations. Workers check the lock
again immediately before final user-scoped persistence.

## Account deletion execution order

The Phase 3C3B Standard Step Functions workflow source is idempotent, auditable
without logging journal content, and preserves this order:

1. `START` verifies request ownership and makes the deletion lock active.
2. `QUIESCE` inspects the complete user partition, lists configured OCR,
   re-analysis, and export workflows, and stops only actual execution ARNs whose
   stored execution input belongs to the subject. It then waits out the maximum
   already-running Lambda duration before destructive work.
3. `CANCEL_SUBSCRIPTION` stops future Stripe renewal, where applicable. It does
   not erase Stripe financial records.
4. `DELETE_RAW_OBJECTS` deletes every current version, noncurrent version, and
   delete marker under `users/<user_id>/uploads/`.
5. `DELETE_EXPORT_OBJECTS` performs the same version-aware deletion under
   `exports/<user_id>/`.
6. `DELETE_APPLICATION_DATA` repeatedly queries and batch-deletes the exact
   `USER#<user_id>` partition, then deletes the Stripe reverse lookup.
7. `DELETE_COGNITO_IDENTITY` first verifies S3, DynamoDB, and reverse-lookup
   absence, globally signs out the exact matched user, and deletes the Cognito
   identity last.
8. `VERIFY` proves both S3 prefixes, the user partition, reverse lookup, and
   Cognito identity are absent.
9. `COMPLETE` records minimal completion evidence and atomically removes the
   active lock and internal billing recovery record.

The state machine input is limited to the opaque deletion request ID. The raw
Cognito subject is never placed in workflow input or execution history. A
separate internal `SUBJECT_RECOVERY` item, keyed by the opaque request ID,
contains the subject only while deletion coordination requires it. It has a
bounded TTL before destructive work, loses that TTL at the destructive boundary,
survives a durable partial failure, and is atomically removed on verified
completion or a terminal pre-destructive failure. It is never logged or returned
by the public API. ERROR logging uses `includeExecutionData=false`; workflow
history must not contain subjects, email, claims, tokens, journal content, or
Stripe identifiers.

Before destructive work begins, the worker copies only the validated Stripe
customer identifier and mode to a subject-digest-scoped internal recovery item
outside the `USER#` partition. It may have a bounded TTL while work remains
non-destructive. The destructive-boundary transaction removes that TTL, so the
record persists until verified completion or explicit operator remediation.
This is not audit or workflow data and is never logged or returned. It permits
an idempotent retry to remove the reverse lookup after the user partition is
gone, without a table scan. Verified completion atomically removes the recovery
item with the active lock.

A terminal pre-destructive failure records a fixed safe code, removes any
temporary billing recovery record, releases the active lock, and permits normal
account use. A failure after destructive work begins retains the active lock
and billing recovery record without TTL, blocking normal account writes and
preventing data recreation until recovery is verified. The same request can be
operator-redriven safely because every action is idempotent; a persistent
partial failure may require explicit operator intervention. Neither failure
path records exception text. Phase 3C3C integrates the workflow into the
stage-scoped deployment source; source integration does not itself deploy or
invoke a deletion.

The worker policy scopes DynamoDB, S3, Cognito, Step Functions, and Secrets
Manager access to the exact stage resources. The Step Functions execution role
uses `Resource: "*"` only for the CloudWatch Logs delivery control-plane actions
for which AWS does not support resource-level authorization; that statement is
limited to the documented log-delivery action set and is covered by deployment
regression tests.
The worker's remaining resource patterns are limited to dynamic identifiers the
deletion engine must discover: `users/*/uploads/*` and `exports/*` inside the
two exact stage buckets, and `execution:<known-workflow-name>:*` for actual
executions of the three exact asynchronous workflows. No wildcard service
actions are granted, and the DynamoDB policy targets only the exact main table.

CloudWatch records expire within 30 days. S3 versions can remain within the
applicable 30-day lifecycle window if immediate removal fails. DynamoDB PITR
and backup recovery can retain deleted values for up to 35 days. Stripe retains
financial/compliance records under its own and legal schedules. The completion
response explains these residual windows.

## Stripe financial records

Stripe is the payment processor and system of record for payment methods and
financial transactions. JM8 does not store complete card details. Account
deletion removes JM8's active customer mappings and stops future renewal as
applicable, but Stripe may retain transaction, refund, dispute, tax, fraud, and
related financial records for the periods required by law and its own policies.
