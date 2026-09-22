import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { parseAppRoute, serializeAppRoute, isSafeEntryId } from "../src/navigation/appRoute.ts";
import { isTrustedStripeRedirect } from "../src/security/externalUrls.ts";

function source(relativePath) {
  return fs.readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

const page = source("src/pages/ArchivePage.tsx");
const auth = source("src/auth/cognito.ts");
const api = source("src/api/client.ts");
const modal = source("src/components/archive/ActionModal.tsx");
const mobile = source("src/components/ui/V2Primitives.tsx");
const ocr = source("src/components/ocr/OcrJobsPanel.tsx");
const historical = source("src/components/analysis/HistoricalJobsPanel.tsx");
const insights = source("src/components/insights/InsightsOverviewPanel.tsx");
const themes = source("src/components/insights/InsightsTrendsPanel.tsx");
const reports = source("src/components/insights/ReportsPanel.tsx");
const ask = source("src/components/insights/AskJm8Panel.tsx");
const productionValidator = source("scripts/validate_production_build.mjs");
const checklist = fs.readFileSync(new URL("../../docs/JM8_V2_STAGING_RELEASE_CHECKLIST.md", import.meta.url), "utf8");
const css = ["src/App.css", "src/styles/v2.css", "src/styles/phase2a.css", "src/styles/phase2b-home.css",
  "src/styles/phase2c-insights-themes.css", "src/components/insights/ReportsPanel.css"]
  .map(source).join("\n");

const baseReport = { type: "WEEKLY", window: null };

test("one canonical parser accepts every implemented view and serializes stable URLs", () => {
  const cases = [
    ["https://app.test/", "home"], ["https://app.test/?view=home", "home"],
    ["https://app.test/?view=archive", "archive"], ["https://app.test/?view=insights", "insights"],
    ["https://app.test/?view=themes&theme=theme-0123abcd", "themes"],
    ["https://app.test/?view=reports&period=weekly", "reports"],
    ["https://app.test/?view=reports&period=monthly", "reports"],
    ["https://app.test/?view=askJm8", "askJm8"], ["https://app.test/?view=ocrJobs", "ocrJobs"],
    ["https://app.test/?view=analysisJobs", "analysisJobs"],
  ];
  for (const [url, view] of cases) assert.equal(parseAppRoute(url).view, view);
  const monthly = parseAppRoute("https://app.test/?view=reports&period=monthly&window=2026-07");
  assert.equal(parseAppRoute(serializeAppRoute(monthly, "https://app.test/")).report.window, "2026-07");
});

test("entry IDs take precedence while malformed and duplicate parameters fail closed", () => {
  const entry = parseAppRoute("https://app.test/?view=reports&period=monthly&entry=entry_abc-123");
  assert.deepEqual(entry, { view: "archive", entryId: "entry_abc-123", themeId: null, report: baseReport });
  for (const url of [
    "https://app.test/?entry=../../billing", "https://app.test/?entry=a&entry=b",
    "https://app.test/?view=archive&view=reports", "https://app.test/?view=https://evil.test/",
  ]) assert.equal(parseAppRoute(url).view, "home");
  assert.equal(isSafeEntryId("entry_abc-123"), true);
  assert.equal(isSafeEntryId("entry/a?x=1"), false);
});

test("serialization removes duplicate, unsupported, callback, and injected parameters", () => {
  const canonical = serializeAppRoute(
    { view: "archive", entryId: null, themeId: null, report: baseReport },
    "https://app.test/?view=archive&view=home&code=secret&next=https://evil.test/#token",
  );
  assert.equal(canonical, "https://app.test/?view=archive");
  assert.equal(parseAppRoute("https://app.test/?view=reports&period=weekly&window=2026-W54").report.window, null);
  assert.equal(parseAppRoute("https://app.test/?view=themes&theme=x&theme=theme-0123abcd").themeId, null);
});

test("Cognito callback uses PKCE plus correlated state and always removes callback material", () => {
  assert.match(auth, /code_challenge_method: "S256"/);
  assert.match(auth, /authStorage\.setItem\(OAUTH_STATE_KEY, state\)/);
  assert.match(auth, /callbackState !== expectedState/);
  assert.match(auth, /getAll\(name\)\.length > 1/);
  assert.match(auth, /finally \{[\s\S]*removeCallbackParameters\(url\)/);
  assert.match(auth, /url\.hash = ""/);
  assert.match(auth, /parseTokenResponse\(await response\.json\(\)\)/);
  assert.match(auth, /typeof claims\.sub !== "string" \|\| !claims\.sub\.trim\(\)/);
  assert.doesNotMatch(auth, /errorDescription \|\| error|error_description[^\n]*throw/);
});

test("401 responses expire authentication and clear every protected client state surface", () => {
  assert.match(api, /response\.status === 401[\s\S]*expireAuthSession\(\)/);
  assert.match(auth, /clearAuthTokens\(\);[\s\S]*AUTH_SESSION_EXPIRED_EVENT/);
  assert.match(page, /addEventListener\(AUTH_SESSION_EXPIRED_EVENT, clearProtectedClientState\)/);
  assert.match(page, /getAuthSessionExpiresAt\(\)[\s\S]*setTimeout\(expireAuthSession/);
  for (const setter of ["setAuthUser(null)", "setEntries([])", "setSelectedEntry(null)", "setUsage(null)",
    "setAccountEntitlement(null)", "setModalMode(null)", "setToasts([])"]) assert.ok(page.includes(setter));
  assert.match(page, /function handleLogout\(\) \{[\s\S]*clearProtectedClientState\(\);[\s\S]*logoutFromCognito\(\)/);
});

test("all entry request paths encode opaque identifiers", () => {
  for (const suffix of ["`/entries/${encodeURIComponent(entryId)}`", "`/entries/${encodeURIComponent(entryId)}/analyze`",
    "`/entries/${encodeURIComponent(entryId)}/analysis-history?${query.toString()}`",
    "`/entries/${encodeURIComponent(entryId)}/ocr`", "`/entries/${encodeURIComponent(entryId)}/review`"]) {
    assert.ok(api.includes(suffix), suffix);
  }
  assert.match(api, /`\/entries\/\$\{encodeURIComponent\(entryId\)\}`[\s\S]*method: "DELETE"/);
});

test("only exact Stripe Checkout and Portal origins can receive browser navigation", () => {
  assert.equal(isTrustedStripeRedirect("https://checkout.stripe.com/c/pay/test", "checkout"), true);
  assert.equal(
    isTrustedStripeRedirect(
      "https://checkout.stripe.com/c/pay/test#fidkdWxOYHwnPyd1blpxYHZxWjA0",
      "checkout"
    ),
    true
  );
  assert.equal(isTrustedStripeRedirect("https://billing.stripe.com/p/session/test", "portal"), true);
  for (const value of ["http://checkout.stripe.com/x", "https://evil.test/x", "https://checkout.stripe.com.evil.test/x",
    "https://user@checkout.stripe.com/x", "https://checkout.stripe.com:444/x"]) {
    assert.equal(isTrustedStripeRedirect(value, "checkout"), false);
  }
  assert.match(page, /isTrustedStripeRedirect\(checkoutUrl, "checkout"\)/);
  assert.match(page, /isTrustedStripeRedirect\(portalUrl, "portal"\)/);
});

test("replaceable view requests abort and stale completions cannot overwrite current state", () => {
  for (const component of [insights, themes, reports, ocr, historical, ask]) {
    assert.match(component, /AbortController/);
    assert.match(component, /[Rr]equestRef\.current/);
    assert.match(component, /\.abort\(\)/);
  }
  assert.match(page, /entryControllerRef\.current\?\.abort\(\)/);
  assert.match(page, /request !== entryRequestRef\.current/);
  assert.match(page, /function handlePopState\(\)[\s\S]*entryControllerRef\.current\?\.abort\(\)/);
  assert.match(api, /getEntry\(entryId: string, signal\?: AbortSignal\)/);
});

test("entry mutations reconcile shared entry, selection, usage, and delete state", () => {
  assert.match(page, /createEntry\(text\)[\s\S]*refreshEntries\(result\.entry\.entryId\)/);
  assert.match(page, /reviewEntry\(entryId, cleanText\)[\s\S]*refreshEntries\(result\.entry\.entryId\)/);
  assert.match(page, /analyzeEntry\(selectedEntry\.entryId\)[\s\S]*refreshEntries[\s\S]*refreshUsage/);
  assert.match(page, /deleteEntry\(selectedEntry\.entryId\)[\s\S]*setEntries\(remainingEntries\)[\s\S]*setSelectedEntry\(null\)/);
});

test("entry actions are a labelled modal dialog with contained and restored focus", () => {
  assert.match(modal, /role="dialog"/);
  assert.match(modal, /aria-modal="true"/);
  assert.match(modal, /aria-labelledby="action-modal-title"/);
  assert.match(modal, /htmlFor="jm8-entry-text"/);
  assert.match(modal, /htmlFor="jm8-review-text"/);
  assert.match(modal, /event\.key === "Escape"/);
  assert.match(modal, /event\.key !== "Tab"/);
  assert.match(modal, /previouslyFocused\?\.focus\(\)/);
});

test("mobile navigation is inert when hidden and restores or advances focus", () => {
  assert.match(page, /inert=\{!isMobileNavOpen\}/);
  assert.match(page, /aria-expanded=\{isMobileNavOpen\}/);
  assert.match(page, /aria-controls="jm8-mobile-navigation"/);
  assert.match(page, /mobileNavCloseRef\.current\?\.focus\(\)/);
  assert.match(page, /mobileNavTriggerRef\.current\?\.focus\(\)/);
  assert.match(page, /heading\.focus\(\)/);
  assert.match(mobile, /forwardRef<HTMLButtonElement/);
});

test("responsive and reduced-motion safeguards cover approved V2 surfaces", () => {
  assert.match(css, /min-width:\s*320px/);
  assert.match(css, /min-height:\s*44px|height:\s*44px/);
  assert.match(css, /@media \(max-width:\s*520px\)/);
  assert.match(css, /@media \(max-width:\s*(?:760|780|820)px\)/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /overflow-x:\s*(?:clip|hidden)/);
});

test("release surfaces retain strict validators and omit unsupported sample controls", () => {
  assert.match(api, /parseInsightsOverviewResponse/);
  assert.match(api, /parseInsightsThemesResponse/);
  assert.match(api, /parseInsightsMoodsResponse/);
  assert.match(api, /parseInsightsReportResponse/);
  const combined = [page, insights, themes, reports].join("\n");
  assert.doesNotMatch(combined, /4,215|11,842|27-day|87%|Tuesday as|fear of failure/i);
  assert.doesNotMatch(reports, />\s*(?:Daily|Yearly|Custom|Export PDF)\s*</i);
});

test("removed V1 drawer components have no surviving implementation or style references", () => {
  const combined = [page, css].join("\n");
  assert.doesNotMatch(combined, /SelectedEntryPanel|selected-panel|selected-actions|selected-summary|selected-date-row/i);
  assert.equal(fs.existsSync(new URL("../src/components/layout/SelectedEntryPanel.tsx", import.meta.url)), false);
});

test("production restrictions and the exact manual journey remain repository-enforced", () => {
  assert.match(productionValidator, /x-user-id/);
  assert.match(productionValidator, /demo\[-_ \]/);
  assert.match(productionValidator, /localhost/);
  assert.match(productionValidator, /source map/i);
  assert.match(productionValidator, /Stripe secret/);
  for (let step = 1; step <= 14; step += 1) assert.match(checklist, new RegExp(`\\| ${step} \\|`));
  assert.match(checklist, /Manual items remain `NOT RUN`/);
  assert.match(checklist, /private-browser session/i);
  assert.match(checklist, /401 or 403/);
});
