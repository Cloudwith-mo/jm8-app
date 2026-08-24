import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";


function source(relativePath) {
  return fs.readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

const landing = source("src/pages/AuthLandingPage.tsx");
const archivePage = source("src/pages/ArchivePage.tsx");
const sidebar = source("src/components/layout/ArchiveSidebar.tsx");
const auth = source("src/auth/cognito.ts");
const primitives = source("src/components/ui/V2Primitives.tsx");
const tokens = source("src/styles/tokens.css");
const v2Styles = source("src/styles/v2.css");

test("signed-out experience is the real Cognito V2 landing", () => {
  for (const copy of [
    "Your life.",
    "Remembered.",
    "Turn years of handwritten journals into a private, searchable",
    "Welcome back",
    "Continue your journal.",
    "Private by design",
    "Your journal belongs to you",
    "Sign in",
    "Create account",
  ]) {
    assert.match(landing, new RegExp(copy.replace(".", "\\.")));
  }

  assert.match(archivePage, /<AuthLandingPage/);
  assert.match(archivePage, /onSignIn=\{\(\) => \{[\s\S]*loginWithCognito/);
  assert.match(archivePage, /onCreateAccount=\{\(\) => \{[\s\S]*signupWithCognito/);
  assert.match(landing, /<div className="jm8-journal-sheet sheet-one">/);
  assert.match(landing, /<div className="jm8-archive-preview">/);
  assert.doesNotMatch(landing, /Google|password|type="email"|type="password"|demo-user|x-user-id/i);
});

test("one accessible typographic brand mark supplies controlled size variants", () => {
  assert.match(primitives, /role="img"/);
  assert.match(primitives, /aria-label="JM8, Journalm8"/);
  assert.match(primitives, /className="jm8-brand-letters">JM</);
  assert.match(primitives, /className="jm8-brand-eight">8</);
  assert.match(primitives, /className="jm8-brand-descriptor"[^>]*>JOURNALM8</);
  assert.match(primitives, /compact \? " compact" : large \? " large"/);
  assert.doesNotMatch(primitives, /jm8-brand-symbol|<img|backgroundImage/);

  assert.match(landing, /<BrandMark large \/>/);
  assert.match(sidebar, /<BrandMark large \/>/);
  assert.match(archivePage, /<BrandMark compact \/>/);
});

test("Cognito sign-in and signup retain one PKCE authorization implementation", () => {
  assert.match(auth, /beginCognitoAuthorization/);
  assert.match(auth, /code_challenge_method: "S256"/);
  assert.match(auth, /loginWithCognito[\s\S]*"\/oauth2\/authorize"/);
  assert.match(auth, /signupWithCognito[\s\S]*"\/signup"/);
  assert.doesNotMatch(auth, /console\.(?:log|debug).*token/i);
});

test("authenticated users bypass the signed-out landing", () => {
  const guard = archivePage.indexOf("if (!isAuthReady || !authUser)");
  const shell = archivePage.indexOf("jm8-archive-shell");
  assert.ok(guard >= 0);
  assert.ok(shell > guard);
  assert.match(archivePage, /if \(!isAuthReady \|\| !authUser\)[\s\S]*return \([\s\S]*<AuthLandingPage/);
});

test("sidebar exposes only implemented navigation and real actions", () => {
  for (const label of [
    "Archive",
    "Insights",
    "Themes",
    "Reports",
    "Ask JM8",
    "OCR Jobs",
    "Analysis Jobs",
  ]) {
    assert.match(sidebar, new RegExp(`label: "${label}"`));
  }

  for (const unsupported of ["Timeline", "Search", "Settings"]) {
    assert.doesNotMatch(sidebar, new RegExp(`label: "${unsupported}"`));
  }

  assert.match(sidebar, /onClick=\{onNewEntry\}/);
  assert.match(sidebar, /onClick=\{onUpload\}/);
  assert.match(sidebar, /className="sidebar-identity"/);
  assert.match(sidebar, /aria-label=\{`Signed in as \$\{displayName\}`\}/);
  assert.doesNotMatch(sidebar, /aria-disabled/);
  assert.doesNotMatch(sidebar, /profile-stats|Today’s Reflection|<Moon/);
});

test("V2 foundation includes the required reusable primitives and tokens", () => {
  for (const component of [
    "BrandMark",
    "Button",
    "IconButton",
    "Surface",
    "StatusBadge",
    "PageHeader",
    "LoadingState",
    "EmptyState",
    "ErrorState",
  ]) {
    assert.match(primitives, new RegExp(`export function ${component}`));
  }

  for (const token of [
    "--jm8-gradient-brand",
    "--jm8-bg",
    "--jm8-text",
    "--jm8-success",
    "--jm8-warning",
    "--jm8-danger",
    "--jm8-border",
    "--jm8-shadow-md",
    "--jm8-radius-xl",
    "--jm8-space-8",
    "--jm8-font-display",
    "--jm8-focus-ring",
    "--jm8-breakpoint-mobile",
  ]) {
    assert.match(tokens, new RegExp(token));
  }
});

test("responsive navigation and primary actions remain accessible", () => {
  assert.match(archivePage, /isMobileNavOpen \? "mobile-sidebar-shell open"/);
  assert.match(archivePage, /label="Open navigation"/);
  assert.match(archivePage, /aria-expanded=\{isMobileNavOpen\}/);
  assert.match(archivePage, /inert=\{!isMobileNavOpen\}/);
  assert.match(archivePage, /event\.key === "Escape"/);
  assert.match(archivePage, /aria-label="Close navigation"/);
  assert.match(v2Styles, /@media \(max-width: 979px\)/);
  assert.match(v2Styles, /@media \(max-width: 760px\)/);
  assert.match(v2Styles, /\.jm8-auth-action \{[\s\S]*order: 1/);
  assert.match(v2Styles, /min-height: 48px/);
  assert.match(v2Styles, /:focus-visible/);
  assert.match(v2Styles, /prefers-reduced-motion: reduce/);
});

test("Phase 1 sources introduce no unsafe shell or authentication markers", () => {
  const combined = [landing, archivePage, sidebar, primitives, tokens, v2Styles].join("\n");
  assert.doesNotMatch(combined, /demo-user|x-user-id|Google login/i);
  assert.doesNotMatch(combined, /\|\| true|\beval\b|set -x/);
});
