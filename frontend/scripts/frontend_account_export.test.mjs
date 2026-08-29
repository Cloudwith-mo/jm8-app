import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";


function source(relativePath) {
  return fs.readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

const component = source("src/components/account/AccountDataExport.tsx");
const styles = source("src/components/account/AccountDataExport.css");
const client = source("src/api/client.ts");
const validator = source("src/security/exportDownloadUrls.ts");
const archive = source("src/pages/ArchivePage.tsx");
const privacy = source("src/pages/PrivacyPolicyPage.tsx");


test("account panel provides a compact export request and accurate retention", () => {
  assert.match(archive, /<AccountDataExport isPanelOpen=\{isAccountPanelOpen\}/);
  assert.match(component, /Your data/);
  assert.match(component, /Request data export/);
  assert.match(component, /expire 24 hours after completion/);
  for (const status of ["QUEUED", "RUNNING", "COMPLETED", "FAILED", "EXPIRED"]) {
    assert.match(component, new RegExp(`case "${status}"`));
  }
  assert.match(component, /crypto\.randomUUID\(\)/);
  assert.match(component, /disabled=\{isRequesting \|\| isPending\(job\)\}/);
});

test("polling runs only for active jobs and aborts or suppresses stale work", () => {
  assert.match(component, /job\?\.status === "QUEUED" \|\| job\?\.status === "RUNNING"/);
  assert.match(component, /controllerRef\.current\?\.abort\(\)/);
  assert.match(component, /window\.clearTimeout/);
  assert.match(component, /generation !== generationRef\.current/);
  assert.match(component, /return stop/);
  assert.match(archive, /onToggle=.*setIsAccountPanelOpen/s);
  assert.match(archive, /setIsAccountPanelOpen\(false\)/);
});

test("download validation requires the one expected stage bucket hostname", () => {
  assert.match(validator, /url\.protocol === "https:"/);
  assert.match(validator, /url\.hostname === expectedHostname/);
  assert.match(validator, /url\.port === ""/);
  assert.match(validator, /url\.username === ""/);
  assert.match(validator, /url\.password === ""/);
  assert.match(validator, /journalm8-\$\{stage\}-exports-\$\{JM8_AWS_ACCOUNT_ID\}\.s3\.\$\{JM8_AWS_REGION\}\.amazonaws\.com/);
  assert.doesNotMatch(validator, /endsWith\([^)]*amazonaws\.com/);
  assert.match(component, /job\?\.status === "COMPLETED"/);
});

test("API client sends only a request token and supports cancellation", () => {
  assert.match(client, /requestAccountExport[\s\S]*requestToken[\s\S]*signal/);
  assert.match(client, /listAccountExports[\s\S]*AbortSignal/);
  assert.match(client, /getAccountExport[\s\S]*encodeURIComponent\(exportId\)/);
  assert.doesNotMatch(client, /requestAccountExport[\s\S]{0,500}userId/);
  assert.match(client, /useIdentityToken \? getIdToken\(\) : getAccessToken\(\)/);
  assert.match(client, /requestAccountExport[\s\S]{0,400}useIdentityToken: true/);
});

test("export controls are accessible, responsive, and reduced-motion safe", () => {
  assert.match(component, /aria-labelledby="account-data-export-heading"/);
  assert.match(component, /role="status" aria-live="polite"/);
  assert.match(component, /<button[\s\S]*type="button"/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /min-height: 44px/);
  assert.match(styles, /@media \(max-width: 520px\)/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
});

test("privacy copy distinguishes available export from planned deletion", () => {
  assert.match(privacy, /In-app account export is available/);
  assert.match(privacy, /account deletion is planned for Phase 3C3/);
  assert.match(privacy, /deletion requests are currently handled by email/);
  assert.doesNotMatch(privacy, /Automated in-app account export and deletion are planned/);
});
