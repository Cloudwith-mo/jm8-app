# JM8 User Data Contract

Status: proposed privacy operations contract for the JM8 private beta

Effective date: August 28, 2026

This document maps user-related data across JM8 and defines the intended scope
and order for future account export and deletion workflows. It documents the
current storage model; it does not claim that automated export or account
deletion exists today.

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

## Proposed export contents

A future portable export should be assembled only after identity verification
and should contain:

1. account profile fields suitable for disclosure, including Cognito subject,
   email, optional display name, account status, and creation date;
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

## Proposed deletion order

Automated deletion is not yet available. A future deletion workflow should be
idempotent, auditable without logging journal content, and use this order:

1. Verify the requester and record a deletion request identifier without
   copying journal content into the audit record.
2. Block new writes and capture the Stripe customer identifier and S3 keys
   needed to finish cleanup.
3. Cancel future Stripe subscription renewal, where applicable. Do not attempt
   to erase financial records that Stripe must retain.
4. Delete every current and noncurrent S3 object version and delete marker under
   `users/<user_id>/uploads/`.
5. Delete every item in the `USER#<user_id>` DynamoDB partition, including
   entries, analysis history, Ask JM8 history, usage, entitlement, billing
   mapping, and re-analysis records.
6. Delete the corresponding `STRIPE_CUSTOMER#<mode>#<customer_id>` reverse
   lookup item.
7. Delete the Cognito identity last so authentication remains available while
   the preceding user-scoped cleanup is verified.
8. Verify that active S3, DynamoDB, reverse-lookup, and Cognito records are gone;
   then record completion using only the request identifier and completion time.

CloudWatch records expire within 30 days, S3 versions within the applicable
30-day lifecycle window if any version could not be immediately removed, and
DynamoDB backup recovery within 35 days. The completion response must explain
those residual recovery windows.

## Stripe financial records

Stripe is the payment processor and system of record for payment methods and
financial transactions. JM8 does not store complete card details. Account
deletion removes JM8's active customer mappings and stops future renewal as
applicable, but Stripe may retain transaction, refund, dispute, tax, fraud, and
related financial records for the periods required by law and its own policies.
