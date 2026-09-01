import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";


function source(relativePath) {
  return fs.readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

const component = source("src/components/account/AccountDeletionDangerZone.tsx");
const styles = source("src/components/account/AccountDeletionDangerZone.css");
const client = source("src/api/client.ts");
const types = source("src/types/accountDeletion.ts");
const auth = source("src/auth/cognito.ts");
const archive = source("src/pages/ArchivePage.tsx");
const landing = source("src/pages/AuthLandingPage.tsx");
const transpiledTypes = ts.transpileModule(types, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const deletionContract = await import(
  `data:text/javascript;base64,${Buffer.from(transpiledTypes).toString("base64")}`
);


test("account panel has an isolated permanent-deletion danger zone", () => {
  assert.match(archive, /<AccountDeletionDangerZone/);
  assert.match(component, /Danger zone/);
  assert.match(component, /Account deletion is permanent and cannot be undone/);
  for (const phrase of [
    "journal entries", "uploaded images", "OCR and analysis data",
    "Ask JM8 history", "generated exports", "subscription access",
    "sign-in identity", "Minimal non-content audit evidence",
  ]) {
    assert.match(component, new RegExp(phrase));
  }
  assert.match(component, /> Delete account/);
});


test("confirmation is a separate accessible step with exact phrase enforcement", () => {
  assert.match(component, /setIsConfirming\(true\)/);
  assert.match(component, /role="dialog"/);
  assert.match(component, /aria-modal="true"/);
  assert.match(component, /Confirmation phrase/);
  assert.match(component, /confirmation !== CONFIRMATION \|\| isSubmitting/);
  assert.match(component, /const CONFIRMATION = "DELETE_MY_ACCOUNT"/);
  assert.match(component, /event\.key === "Escape"/);
  assert.match(component, /confirmationRef\.current\?\.focus\(\)/);
  assert.match(component, /Cancel/);
});


test("each submission gets a fresh token and concurrent submits are blocked", () => {
  assert.match(component, /const requestToken = crypto\.randomUUID\(\)/);
  assert.match(component, /submittingRef\.current/);
  assert.match(component, /if \(confirmation !== CONFIRMATION \|\| submittingRef\.current\) return/);
  assert.match(client, /requestAccountDeletion[\s\S]*confirmation[\s\S]*requestToken/);
  assert.doesNotMatch(component, /useState[^\n]*requestToken/);
  assert.doesNotMatch(component, /localStorage|sessionStorage|console\./);
});


test("typed API boundary sanitizes public deletion fields", () => {
  assert.match(client, /POST|method: "POST"/);
  assert.match(client, /\/account\/deletion-requests/);
  assert.match(client, /encodeURIComponent\(requestId\)/);
  assert.match(client, /result\.status !== 200 && result\.status !== 202/);
  assert.match(types, /httpStatus: 200 \| 202/);
  for (const safeField of [
    "requestId", "status", "requestedAt", "startedAt",
    "destructiveStartedAt", "completedAt", "failedAt", "failure",
  ]) {
    assert.match(types, new RegExp(safeField));
  }
  for (const forbidden of [
    "subjectDigest", "stripeCustomerId", "workflowExecutionArn",
    "objectKey", "accountExportTtlEpoch", "BILLING_RECOVERY",
  ]) {
    assert.doesNotMatch(types, new RegExp(forbidden));
  }
});


test("runtime projection strips malicious storage and identity fields", () => {
  const response = deletionContract.parseAccountDeletionResponse({
    deletionRequest: {
      requestId: "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      status: "IN_PROGRESS",
      requestedAt: "2026-08-31T12:00:00.123456Z",
      destructiveStartedAt: "not-a-timestamp",
      PK: "USER#raw-cognito-subject",
      SK: "ACCOUNT_DELETION",
      subjectDigest: "private-digest",
      email: "user@example.com",
      stripeCustomerId: "cus_secret",
      workflowExecutionArn: "arn:aws:states:private",
      requestTokenDigest: "private-token-digest",
      objectKey: "users/private/image.jpg",
      accountExportTtlEpoch: 123456789,
      journalContent: "private journal text",
      recovery: { cognitoSubject: "raw-cognito-subject" },
    },
    claims: { sub: "raw-cognito-subject" },
  });
  assert.deepEqual(response, {
    deletionRequest: {
      requestId: "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      status: "IN_PROGRESS",
      requestedAt: "2026-08-31T12:00:00.123456Z",
    },
  });
  const serialized = JSON.stringify(response);
  for (const secret of [
    "raw-cognito-subject", "private-digest", "user@example.com",
    "cus_secret", "private-token-digest", "private journal text",
  ]) {
    assert.doesNotMatch(serialized, new RegExp(secret));
  }
});


test("auth loss is completion-like only after a validated destructive marker", () => {
  const parse = (status, destructiveStartedAt) => (
    deletionContract.parseAccountDeletionResponse({
      deletionRequest: {
        requestId: "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        status,
        ...(destructiveStartedAt ? { destructiveStartedAt } : {}),
      },
    }).deletionRequest
  );
  const validMarker = "2026-08-31T12:00:00Z";
  assert.equal(deletionContract.hasObservedDestructiveDeletion(null), false);
  assert.equal(
    deletionContract.hasObservedDestructiveDeletion(parse("REQUESTED", validMarker)),
    false,
  );
  assert.equal(
    deletionContract.hasObservedDestructiveDeletion(parse("IN_PROGRESS")),
    false,
  );
  assert.equal(
    deletionContract.hasObservedDestructiveDeletion(
      parse("IN_PROGRESS", "not-a-timestamp"),
    ),
    false,
  );
  assert.equal(
    deletionContract.hasObservedDestructiveDeletion(
      parse("IN_PROGRESS", validMarker),
    ),
    true,
  );
  assert.equal(
    deletionContract.hasObservedDestructiveDeletion(parse("FAILED", validMarker)),
    true,
  );
  const queued = parse("REQUESTED");
  const quiescing = parse("IN_PROGRESS");
  assert.equal(
    deletionContract.shouldTreatTrackingAuthLossAsProcessed(401, false), false
  );
  assert.equal(
    deletionContract.shouldTreatTrackingAuthLossAsProcessed(
      401,
      deletionContract.hasObservedDestructiveDeletion(queued),
    ), false
  );
  assert.equal(
    deletionContract.shouldTreatTrackingAuthLossAsProcessed(
      401,
      deletionContract.hasObservedDestructiveDeletion(quiescing),
    ), false
  );
  assert.equal(
    deletionContract.shouldTreatTrackingAuthLossAsProcessed(undefined, true),
    false,
  );
  assert.equal(
    deletionContract.shouldTreatTrackingAuthLossAsProcessed(404, true), false
  );
  assert.equal(
    deletionContract.shouldTreatTrackingAuthLossAsProcessed(500, true), false
  );
  assert.equal(
    deletionContract.shouldTreatTrackingAuthLossAsProcessed(401, true), true
  );
});


test("backend errors map to fixed public messages without raw bodies", () => {
  for (const code of [
    "InvalidConfirmation", "DeletionConfirmationRequired", "InvalidRequestToken",
    "RecentAuthenticationRequired", "ActiveDeletionExists",
    "AccountDeletionUnavailable", "DeletionRequestNotFound", "Unauthorized",
  ]) {
    assert.match(component, new RegExp(`case "${code}"`));
  }
  assert.match(component, /The account deletion request could not be processed/);
  assert.doesNotMatch(component, /error\.message|error\.payload|JSON\.stringify\(error/);
});


test("accepted and replayed responses track only the returned owned request", () => {
  assert.match(component, /response\.deletionRequest/);
  assert.match(component, /trackRequest\(accepted\.requestId\)/);
  assert.match(types, /replayed\?: boolean/);
  assert.doesNotMatch(component, /ActiveDeletionExists[\s\S]{0,300}requestId/);
  assert.doesNotMatch(component, /requestAccountDeletion[\s\S]{0,500}catch[\s\S]{0,300}requestAccountDeletion/);
});


test("recent authentication clears volatile confirmation before redirect", () => {
  assert.match(component, /setConfirmation\(""\)[\s\S]*onReauthenticate\(\)/);
  assert.match(archive, /rememberAccountDeletionReturnIntent\(\)/);
  assert.match(archive, /loginWithCognito\(\)/);
  assert.match(archive, /consumeAccountDeletionReturnIntent\(\)/);
  assert.match(archive, /setIsAccountPanelOpen\(true\)/);
  assert.match(archive, /setFocusAccountDeletion\(true\)/);
  assert.match(auth, /ACCOUNT_DELETION_RETURN_VALUE = "account-deletion"/);
  assert.doesNotMatch(auth, /DELETE_MY_ACCOUNT|requestToken/);
});


test("polling is bounded, non-overlapping, cancellable, and manually refreshable", () => {
  assert.match(component, /MAX_AUTOMATIC_POLLS = 12/);
  assert.match(component, /MAX_POLL_DELAY_MS = 15_000/);
  assert.match(component, /Math\.min\(2_000 \* \(2 \*\*/);
  assert.match(component, /pollInFlightRef\.current/);
  assert.match(component, /controllerRef\.current\?\.abort\(\)/);
  assert.match(component, /window\.clearTimeout/);
  assert.match(component, /generation !== generationRef\.current/);
  assert.match(component, /useEffect\(\(\) => abortTracking/);
  assert.match(component, /Refresh status/);
  assert.match(component, /safe\.notFound/);
});


test("status presentation distinguishes queue, quiescence, destructive work, and failures", () => {
  for (const text of [
    "NOT REQUESTED", "QUEUED", "QUIESCING", "DESTRUCTIVE PROCESSING",
    "COMPLETED", "RETRYABLE FAILURE", "FAILED",
  ]) {
    assert.match(component, new RegExp(text));
  }
  assert.match(component, /request\.destructiveStartedAt/);
  assert.match(component, /Account writes are blocked/);
  assert.match(component, /Support intervention may be needed/);
});


test("completion and expected destructive auth loss use centralized sign-out", () => {
  assert.match(component, /shouldTreatTrackingAuthLossAsProcessed/);
  assert.match(component, /next\.status === "COMPLETED"/);
  assert.match(component, /onDeletionProcessed\(\)/);
  assert.match(archive, /clearProtectedClientState\(\)/);
  assert.match(archive, /logoutFromCognito\(\)/);
  assert.match(archive, /Your account deletion request has been processed\. You have been signed out\./);
  assert.match(landing, /acknowledgement/);
  assert.doesNotMatch(component, /onDeletionProcessed[\s\S]{0,200}requestAccountDeletion/);
});


test("danger-zone interaction is responsive and reduced-motion safe", () => {
  assert.match(component, /aria-labelledby="account-deletion-heading"/);
  assert.match(component, /role="status" aria-live="polite"/);
  assert.match(styles, /min-height: 44px/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /@media \(max-width: 520px\)/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
});
