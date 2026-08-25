import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

function source(relativePath) {
  return fs.readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

const page = source("src/pages/ArchivePage.tsx");
const home = source("src/components/home/HomeDashboard.tsx");
const sidebar = source("src/components/layout/ArchiveSidebar.tsx");
const auth = source("src/auth/cognito.ts");
const styles = source("src/styles/phase2b-home.css");
const packageJson = JSON.parse(source("package.json"));

test("authenticated navigation defaults to Home and exposes functional Home and Archive destinations", () => {
  assert.match(page, /return ROUTABLE_SECTIONS\.find[\s\S]*\|\| "home"/);
  assert.match(page, /useState<ArchiveSection>\(\(\) => getSectionRoute\(\)\)/);
  assert.match(sidebar, /label: "Home"[\s\S]*section: "home"/);
  assert.match(sidebar, /label: "Archive"[\s\S]*section: "archive"/);
  assert.match(sidebar, /onNavigate\([\s\S]*item\.section/);
  assert.match(page, /setSectionRoute\(section\)/);
});

test("entry deep links override Home while preserving browser navigation", () => {
  assert.match(page, /if \(url\.searchParams\.get\("entry"\)\) return "archive"/);
  assert.match(page, /searchParams\.set\("entry", entryId\)/);
  assert.match(page, /window\.history\[mode === "push" \? "pushState" : "replaceState"\]/);
  assert.match(page, /addEventListener\("popstate", handlePopState\)/);
  assert.match(page, /setActiveSection\(getSectionRoute\(\)\)/);
  assert.match(page, /if \(entryId\) void openEntry\(entryId, false\)/);
  assert.doesNotMatch(JSON.stringify(packageJson.dependencies), /react-router/);
});

test("Cognito callback cleanup and PKCE authentication remain intact", () => {
  assert.match(auth, /code_challenge_method: "S256"/);
  assert.match(auth, /url\.search = "";[\s\S]*window\.history\.replaceState/);
  assert.match(page, /handleCognitoCallback\(\)/);
  assert.match(page, /if \(!isAuthReady \|\| !authUser\)/);
});

test("Home greeting uses local time and only a returned Cognito name", () => {
  assert.match(home, /new Date\(\)/);
  assert.match(home, /greetingForHour\(now\.getHours\(\)\)/);
  assert.match(home, /user\.name\?\.trim\(\)/);
  assert.doesNotMatch(home, /email\.split|split\("@"\)|Muhammad|Guest/);
  assert.match(home, /displayName \? `, \$\{displayName\}` : ""/);
});

test("On-this-day reflection requires a matching prior-year calendar date", () => {
  assert.match(home, /date\.getFullYear\(\) < now\.getFullYear\(\)/);
  assert.match(home, /date\.getMonth\(\) === now\.getMonth\(\)/);
  assert.match(home, /date\.getDate\(\) === now\.getDate\(\)/);
  assert.match(home, /entry\.cleanText \|\| entry\.rawText \|\| entry\.analysis\?\.summary \|\| ""/);
  assert.match(home, /reflection \? "On this day" : "Continue your journal"/);
  assert.match(home, /onOpenEntry\(featuredEntry\.entryId\)/);
  assert.doesNotMatch(home, /fear of failure|accomplished what|mountain|streak/i);
});

test("missing dates remain unavailable and cannot become a historical reflection", () => {
  assert.match(home, /if \(!entry\.createdAt\) return null/);
  assert.match(home, /Number\.isNaN\(date\.getTime\(\)\) \? null : date/);
  assert.match(home, /"Date unavailable"/);
  assert.match(home, /datedEntries = orderedEntries\.filter\(\(entry\) => validDate\(entry\)\)/);
  assert.doesNotMatch(home, /new Date\(entry\.createdAt \|\|/);
});

test("quick actions are functional and Continue Writing is gated by real editable data", () => {
  assert.match(home, /entry\.sourceType === "typed" && Boolean\(entry\.cleanText \|\| entry\.rawText\)/);
  assert.match(home, /editableEntry && <button[\s\S]*onContinueWriting\(editableEntry\.entryId\)/);
  assert.match(page, /onContinueWriting=\{\(entryId\) => void handleContinueWriting\(entryId\)\}/);
  assert.match(page, /setModalMode\("write"\)/);
  assert.match(page, /setModalMode\("upload"\)/);
  assert.match(page, /navigateToSection\("askJm8"\)/);
});

test("statistics disclose loaded-set provenance and use returned fields only", () => {
  assert.match(home, /Calculated only from entries returned in this archive load/);
  assert.match(home, /Loaded entries/);
  assert.match(home, /Documented years/);
  assert.match(home, /entry\.wordCount \?\? entry\.analysis\?\.wordCount \?\? entry\.ocrWordCount/);
  assert.match(home, /entry\.analysisStatus === "COMPLETED"/);
  assert.doesNotMatch(home, /Total Entries|Total Words|Day Streak|Years Documented/);
});

test("recent entries reuse Phase 2A cards and selection opens dedicated detail", () => {
  assert.match(home, /const recentEntries = orderedEntries\.slice\(0, 4\)/);
  assert.match(home, /<EntryCard[\s\S]*onClick=\{\(\) => onOpenEntry\(entry\.entryId\)\}/);
  assert.match(page, /onOpenEntry=\{\(entryId\) => void openEntry\(entryId\)\}/);
  assert.match(page, /<EntryDetailView/);
  assert.doesNotMatch(home, /placeholder journal|sample entry|mock/i);
});

test("Home is responsive, accessible, and contains no unsupported dashboard controls or bypasses", () => {
  assert.match(home, /aria-labelledby="home-reflection-title"/);
  assert.match(home, /aria-labelledby="quick-actions-title"/);
  assert.match(home, /role="status" aria-live="polite"/);
  assert.match(styles, /min-height: 44px/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /@media \(max-width: 1050px\)/);
  assert.match(styles, /@media \(max-width: 760px\)/);
  assert.match(styles, /@media \(max-width: 520px\)/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
  const combined = [page, home, sidebar, styles].join("\n");
  assert.doesNotMatch(combined, /demo-user|x-user-id|\|\| true|\beval\b|set -x/i);
  assert.doesNotMatch(home, /notification|global search|theme toggle/i);
});
