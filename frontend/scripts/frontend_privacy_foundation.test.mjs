import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";


function source(relativePath) {
  return fs.readFileSync(
    new URL(`../${relativePath}`, import.meta.url),
    "utf8",
  );
}

const app = source("src/App.tsx");
const metadata = source("src/content/policyMetadata.ts");
const layout = source("src/components/policy/PublicPolicyLayout.tsx");
const policyStyles = source("src/components/policy/PublicPolicyLayout.css");
const privacy = source("src/pages/PrivacyPolicyPage.tsx");
const terms = source("src/pages/TermsPage.tsx");
const landing = source("src/pages/AuthLandingPage.tsx");
const archive = source("src/pages/ArchivePage.tsx");
const globalStyles = source("src/styles/v2.css");
const dataContract = fs.readFileSync(
  new URL("../../docs/JM8_USER_DATA_CONTRACT.md", import.meta.url),
  "utf8",
);


test("privacy and terms routes render before the authenticated archive", () => {
  assert.match(app, /pathname === "\/privacy"[\s\S]*<PrivacyPolicyPage \/>/);
  assert.match(app, /pathname === "\/terms"[\s\S]*<TermsPage \/>/);

  const privacyRoute = app.indexOf('pathname === "/privacy"');
  const termsRoute = app.indexOf('pathname === "/terms"');
  const protectedArchive = app.indexOf("return <ArchivePage />");

  assert.ok(privacyRoute >= 0);
  assert.ok(termsRoute > privacyRoute);
  assert.ok(protectedArchive > termsRoute);
});


test("policy pages are public and do not initialize authentication", () => {
  const publicSources = [privacy, terms, layout].join("\n");

  assert.doesNotMatch(
    publicSources,
    /from ["'].*auth\/cognito|getCurrentUser|handleCognitoCallback|loginWithCognito|apiRequest/i,
  );
  assert.doesNotMatch(publicSources, /AuthLandingPage|authUser|isAuthReady/);
  assert.match(layout, /<a href="\/" aria-label="JM8 home">/);
});


test("one metadata source owns policy identity and eligibility values", () => {
  assert.match(metadata, /operatorName: "Muhammad Adeyemi"/);
  assert.match(metadata, /contactEmail: "muhammadadeyemi\.it@outlook\.com"/);
  assert.match(metadata, /effectiveDate: "August 28, 2026"/);
  assert.match(metadata, /minimumAge: 13/);
  assert.match(metadata, /name: "European Economic Area"[\s\S]*shortName: "EEA"/);
  assert.match(metadata, /name: "United Kingdom"[\s\S]*shortName: "UK"/);

  for (const policySource of [privacy, terms]) {
    assert.match(policySource, /POLICY_METADATA/);
    assert.doesNotMatch(policySource, /Muhammad Adeyemi|muhammadadeyemi\.it@outlook\.com/);
  }

  assert.match(layout, /POLICY_METADATA\.effectiveDate/);
  assert.match(layout, /POLICY_METADATA\.operatorName/);
  assert.match(landing, /POLICY_METADATA\.minimumAge/);
  assert.match(landing, /EXCLUDED_REGION_SHORT_NAMES/);
});


test("signed-out and account surfaces expose public policy links and eligibility", () => {
  for (const href of ["/privacy", "/terms"]) {
    assert.match(landing, new RegExp(`<a href="${href}"`));
    assert.match(archive, new RegExp(`<a href="${href}"`));
  }

  assert.match(landing, /className="jm8-auth-footer"/);
  assert.match(archive, /className="phase2-account-policy-links"/);
  assert.match(landing, /By continuing, you confirm that you are at least/);
  assert.match(landing, /do not reside in the/);
  assert.doesNotMatch(landing, /not located in the/);
  assert.match(landing, /and agree to the/);
  assert.match(landing, />Terms<\/a> and/);
  assert.match(landing, />Privacy Policy<\/a>\. If you are under the age of/);
  assert.match(landing, /parent or legal[\s\S]*guardian has authorized your use of JM8/);
});


test("privacy policy contains the required collection and processing disclosures", () => {
  for (const required of [
    "Account data",
    "Journal data",
    "Uploaded-image and OCR data",
    "Analysis and Ask JM8 data",
    "Usage data",
    "Subscription data",
    "Support data",
    "Technical data",
    "AWS hosts and processes JM8 application data in the United States",
    "Amazon Bedrock processes journal content",
    "Stripe processes payments and subscription management",
    "does not store complete card details",
    "does not sell personal information",
    "does not use personal information for behavioral advertising",
    "not used by JM8 to train AI models",
    "retained while your account exists",
    "CloudWatch application logs are retained for 30 days",
    "up to 35",
    "up to 30 days",
    "request access to, correction of, export of, or deletion",
    "Automated in-app account export and deletion are planned but are not",
    "cannot guarantee absolute security",
    "published with a new effective date",
  ]) {
    assert.match(privacy, new RegExp(required.replaceAll(" ", "\\s+"), "i"));
  }

  assert.match(privacy, /not directed to children under/);
  assert.match(privacy, /POLICY_METADATA\.minimumAge/);
  assert.match(privacy, /POLICY_METADATA\.contactEmail/);
});


test("terms include eligibility, content, AI, billing, and legal controls", () => {
  for (const required of [
    "truthful, current account information",
    "private journal archive and AI-analysis service",
    "retain ownership",
    "limited, non-exclusive license",
    "Prohibited use",
    "unlawful content",
    "incomplete, inaccurate, misleading",
    "not medical, mental-health, legal, or emergency advice",
    "contact local emergency services",
    "Free plan",
    "Pro plan",
    "$10 monthly recurring subscription",
    "Cancellation stops future renewal",
    "paid access continues through the current billing period",
    "not prorated or refunded",
    "Availability and service changes",
    "Suspension and termination",
    "Disclaimers",
    "Limitation of liability",
    "Governing law",
    "Changes to these Terms",
  ]) {
    const escaped = required.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    assert.match(terms, new RegExp(escaped.replaceAll(" ", "\\s+"), "i"));
  }

  assert.match(terms, /POLICY_METADATA\.minimumAge/);
  assert.match(terms, /eea\.name[\s\S]*uk\.name/);
  assert.match(
    terms,
    /parent or[\s\S]*legal guardian must review and agree to these Terms and authorize your[\s\S]*use of JM8/,
  );
  assert.match(terms, /laws of the State of Texas, United[\s\S]*States/);
  assert.match(terms, /lawful jurisdiction in Texas/);
  assert.match(terms, /browse your archive/);
  assert.doesNotMatch(terms, /search your archive/);
  assert.doesNotMatch(
    terms,
    /(?:LLC|Inc\.|Corporation|certified|SOC 2|HIPAA compliant|guaranteed secure)/i,
  );
});


test("policy markup and styles remain semantic, readable, and responsive", () => {
  assert.equal((layout.match(/<h1/g) || []).length, 1);
  assert.equal((privacy.match(/<h1/g) || []).length, 0);
  assert.equal((terms.match(/<h1/g) || []).length, 0);
  assert.match(layout, /<main className="jm8-policy-main">/);
  assert.match(layout, /<article className="jm8-policy-document">/);
  assert.match(privacy, /<h2/);
  assert.match(terms, /<h2[\s\S]*<h3/);
  assert.match(policyStyles, /width: min\(100% - 40px, 780px\)/);
  assert.match(policyStyles, /@media \(max-width: 600px\)/);
  assert.match(globalStyles, /:where\(button, a, input, textarea, select\):focus-visible/);
});


test("user data contract maps export, deletion, and residual retention", () => {
  for (const required of [
    "PK = USER#<user_id>",
    "STRIPE_CUSTOMER#<TEST|LIVE>#<stripe_customer_id>",
    "users/<user_id>/uploads/",
    "Cognito subject",
    "CloudWatch",
    "up to 35 days",
    "up to 30 days",
    "Proposed export contents",
    "Data intentionally excluded from export",
    "Proposed deletion order",
    "Stripe may retain transaction",
  ]) {
    assert.match(dataContract, new RegExp(required.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
  }
});
