import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

function source(relativePath) {
  return fs.readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

const archive = source("src/pages/ArchivePage.tsx");
const appRoute = source("src/navigation/appRoute.ts");
const topbar = source("src/components/layout/ArchiveTopbar.tsx");
const card = source("src/components/archive/EntryCard.tsx");
const detail = source("src/components/archive/EntryDetailView.tsx");
const styles = source("src/styles/phase2a.css");
const api = source("src/api/client.ts");

test("Archive is the primary authenticated viewport and account data is compact", () => {
  assert.match(archive, /<h1>Archive<\/h1>/);
  assert.match(archive, /All your journals\. Every page\. Always yours\./);
  assert.match(archive, /<details[\s\S]{0,180}className="phase2-account-surface"/);
  assert.match(archive, /<AccountPlanCard[\s\S]*<UsageMeter/);
  assert.equal((archive.match(/<AccountPlanCard/g) || []).length, 1);
  assert.equal((archive.match(/<UsageMeter/g) || []).length, 1);
});

test("Archive controls are wired to real search, source, status, and sorting behavior", () => {
  assert.match(topbar, /placeholder="Search your archive…"/);
  assert.match(topbar, /value="typed">Typed/);
  assert.match(topbar, /value="image">Images/);
  for (const status of ["ANALYZED", "REVIEWED", "OCR_COMPLETED", "OCR_FAILED", "NOT_ANALYZED"]) {
    assert.match(topbar, new RegExp(`value="${status}"`));
    assert.match(archive, new RegExp(`statusFilter === "${status}"`));
  }
  assert.match(topbar, /value="newest"/);
  assert.match(topbar, /value="oldest"/);
});

test("Archive groups valid dates without inventing dates and exposes functional year disclosure", () => {
  assert.match(archive, /Number\.isNaN\(date\.getTime\(\)\) \? null : date/);
  assert.match(archive, /"Date unavailable"/);
  assert.doesNotMatch(archive, /entry\.createdAt \? new Date\(entry\.createdAt\) : new Date\(\)/);
  assert.match(archive, /<details className="phase2-year-group"[\s\S]*<summary>/);
  assert.match(archive, /year\.months\.map/);
  assert.match(archive, /month\.entries\.map/);
});

test("dense entry cards use real thumbnails or returned transcript text only", () => {
  assert.match(card, /entry\.imagePreviewUrl/);
  assert.match(card, /entry\.cleanText \|\| entry\.rawText/);
  assert.match(card, /Transcript unavailable/);
  assert.doesNotMatch(card, /GROWTH IS|UNCOMFORTABLE|TODAY I|REFLECTED|\["ocr"|\["reflection"|\|\| "NEW"/);
  assert.doesNotMatch(card, /MoreHorizontal|entry-more/);
});

test("entry detail is route-compatible and replaces the selected-entry drawer", () => {
  assert.match(appRoute, /getAll\("entry"\)/);
  assert.match(appRoute, /searchParams\.set\("entry", route\.entryId\)/);
  assert.match(appRoute, /pushState|replaceState/);
  assert.match(archive, /addEventListener\("popstate"/);
  assert.match(archive, /<EntryDetailView/);
  assert.doesNotMatch(archive, /SelectedEntryPanel|selected-panel-backdrop|selected entry drawer/i);
  assert.equal(fs.existsSync(new URL("../src/components/layout/SelectedEntryPanel.tsx", import.meta.url)), false);
  assert.match(detail, /Back to Archive/);
});

test("entry detail preserves every supported real entry action", () => {
  for (const handler of [
    "onReview", "onAnalyze", "onRetryOcr", "onCopyTranscript", "onExportTranscript",
    "onDownloadImage", "onDelete",
  ]) assert.match(detail, new RegExp(handler));
  assert.match(archive, /retryOcrJob\(selectedEntry\.entryId\)/);
  assert.match(api, /export async function retryOcrJob/);
  assert.match(archive, /window\.confirm/);
});

test("entry detail renders analysis only from returned API fields", () => {
  for (const field of ["summary", "mood", "sentiment", "themes", "nextStep"]) {
    assert.match(detail, new RegExp(`analysis\\?\\.${field}`));
  }
  assert.match(detail, /entry\.wordCount \?\? analysis\?\.wordCount \?\? entry\.ocrWordCount/);
  assert.doesNotMatch(detail, /Reflective|Neutral|related entries|fake|demo/i);
  assert.doesNotMatch(detail, /\|\| \["ocr"|\|\| "Reflective"|\|\| "Neutral"/);
});

test("Phase 2A remains responsive and accessible without horizontal overflow", () => {
  assert.match(styles, /grid-template-columns: minmax\(250px, \.9fr\) minmax\(300px, 1fr\) minmax\(280px, \.8fr\)/);
  assert.match(styles, /@media \(max-width: 1120px\)/);
  assert.match(styles, /@media \(max-width: 800px\)/);
  assert.match(styles, /@media \(max-width: 520px\)/);
  assert.match(styles, /min-height: 44px/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
  assert.match(styles, /overflow-x: clip/);
});

test("Phase 2A introduces no unsafe bypasses or new dependencies", () => {
  const combined = [archive, topbar, card, detail, styles].join("\n");
  assert.doesNotMatch(combined, /demo-user|x-user-id|\|\| true|\beval\b|set -x/i);
  assert.doesNotMatch(combined, /react-router|mocked response|sample data/i);
});
