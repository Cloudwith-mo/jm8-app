# JM8 Phase 5 Alpha Telemetry Contract

## Decision

JM8 will measure the five-person alpha with stage-isolated, aggregate product
metrics. The product will not send journal content or per-user identifiers to
CloudWatch dimensions, logs, or an external analytics vendor.

The telemetry answers one question: do invited journalers reach a meaningful,
grounded insight and return? It is not a surveillance record of what they wrote
or asked.

## Collection boundary

The canonical namespace is `JM8/<stage>/Product`. Every event is built from the
closed registry in `product_telemetry_contract.py`. The builder accepts only a
stage, an approved metric name, a numeric value, and a timestamp. It accepts no
dimensions or arbitrary metadata.

Never collect or emit:

- journal, OCR, prompt, answer, analysis, or source-evidence text;
- email addresses, filenames, access tokens, or payment references;
- raw or hashed user IDs, entry IDs, Ask-history IDs, export IDs, or deletion IDs;
- per-user CloudWatch dimensions or small-cohort labels;
- client fingerprints, advertising identifiers, or third-party analytics IDs.

## Metrics

| Outcome | Metrics | Semantics |
| --- | --- | --- |
| Activation | `ActivatedUser`, `FirstEntryCreated`, `SecondWeekReturned`, `WeeklyActiveUser` | Conditional user-partition markers emit first/weekly counts once. |
| OCR | `OcrStarted`, `OcrCompleted`, `OcrFailed` | One count at the authoritative backend/workflow transition. |
| Intelligence | `FirstAnalysisCompleted`, `FirstGroundedAskCompleted` | Emit only after persisted success; grounded Ask requires source evidence. |
| Data rights | `AccountExportRequested`, `AccountExportCompleted`, `AccountExportFailed`, `AccountDeletionRequested`, `AccountDeletionCompleted`, `AccountDeletionFailed` | Workflow lifecycle counts; no request identifier is emitted. |
| Billing | `CheckoutStarted`, `ProEntitlementActivated`, `CancellationRequested` | Backend-confirmed lifecycle counts; no Stripe reference is emitted. |
| Meaning | `MeaningfulInsightYes`, `MeaningfulInsightNo`, `TimeToFirstMeaningfulInsightMs` | Explicit feedback only. Duration emits once after the first affirmative response. |
| Safety | `TelemetryEmissionFailed` | Operational count proving a milestone-storage or metric-sink failure did not break the user journey. |

## Deduplication

First-time and weekly milestones use conditional records in the existing user
partition. Keys contain only the metric name and, for weekly activity, an ISO
week. The user's existing partition key supplies tenancy; the metric event does
not include that key. Account deletion removes these markers with the rest of
the user's application partition.

Retries must not double-count a successfully stored first-time or weekly
milestone. OCR, export,
deletion, Checkout, cancellation, feedback, and failure metrics represent
individual authoritative transitions and require operation-level idempotency at
their existing domain boundary.

## Failure behavior

Telemetry is secondary to the product action. A storage or emission failure
must not turn a successful journal, OCR, analysis, Ask, export, deletion, or
billing operation into a user-visible failure. The caller records only the safe
`TelemetryEmissionFailed` operational event and continues. Telemetry failures
must be alarmed because silent measurement loss invalidates the alpha readout.

The milestone record is the deduplication authority. If metric emission fails
after a milestone is stored, the safe result reports
`RECORDED_EMISSION_FAILED`; it does not delete the marker or retry blindly.
Before production deployment, reconciliation must define how a missing derived
metric is repaired without double-counting. CloudWatch event counts are not an
exactly-once database and must not replace the milestone inventory.

## Interpretation limits

- `ActivatedUser` is the first authenticated JM8 request, not a Cognito signup.
- Visitor-to-signup conversion is not collected in this slice. Public landing
  analytics require a separate consent, retention, and vendor decision.
- `WeeklyActiveUser` is a count of users active at least once in a UTC ISO week;
  it does not expose which users were active.
- `TimeToFirstMeaningfulInsightMs` begins at activation and ends only when the
  user explicitly marks a grounded result as meaningful.
- Support-request count is maintained in the approved support channel until a
  separate privacy-reviewed support integration exists.
- With only five testers, dashboards are directional. No individual behavior
  may be inferred or presented as statistically generalizable.

## Release gate

Instrumentation is not production-ready until contract, store, emitter,
integration, dashboard, namespace-isolation, deletion, and failure-path tests
all pass. Deployment requires a read-only preflight, exact production identity,
clean Git state, no journal-content inspection, and post-deployment proof that
the user journey still succeeds when telemetry is unavailable.

## Observability and activation gate

Each environment owns an exact `JM8/<stage>/Product` namespace, an
`<app>-<stage>-alpha-telemetry` dashboard, and one dimensionless
`TelemetryEmissionFailed` alarm routed to the existing stage alert topic.
The dashboard enumerates the closed metric registry exactly and contains no log
queries, dimensions, identifiers, journal text, prompts, answers, or evidence.

`PRODUCT_MILESTONE#*` records are internal deduplication state. Account exports
exclude them because exports use an explicit user-data allowlist. Account
deletion removes them because the deletion engine exhaustively deletes and
verifies the user's complete application partition.

The Lambda build packages every `function/*.py` module, including the telemetry
contract, store, emitter, and integration. Runtime IAM needs only the existing
main-table `dynamodb:PutItem` permission; observability deployment is limited to
stage-prefixed alarms, dashboards, and the exact alert topic.

Production activation is blocked until the complete journey is exercised in
staging, the staging dashboard receives its expected aggregate metrics, the
emission-failure alarm and notification path are proven, export and deletion
canaries pass, and staging returns to a healthy state. Synthetic events must
never be emitted in production, preserving the five-person alpha baseline.
