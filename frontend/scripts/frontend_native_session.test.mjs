import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/auth/nativeSession.ts", import.meta.url), "utf8");
function harness(platform = "ios", stored) {
  const local = new Map();
  let secure = stored;
  let deletionFails = false;
  const plugin = {
    async getSession() { return { value: secure }; },
    async setSession({ value }) { secure = value; },
    async clearSession() { if (deletionFails) throw new Error("locked"); secure = undefined; },
    async authenticate() { return { url: "com.cloudwithmo.journalm8.dev://auth/callback?code=abc&state=xyz" }; },
  };
  const exports = {};
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, {
    exports, URL, Map, Date, Promise,
    require: () => ({ Capacitor: { getPlatform: () => platform }, registerPlugin: () => plugin }),
    localStorage: {
      getItem: key => local.get(key) ?? null,
      setItem: (key, value) => local.set(key, value),
      removeItem: key => local.delete(key),
    },
  });
  return { api: exports, local, plugin, get secure() { return secure; }, failDelete() { deletionFails = true; } };
}
const valid = () => ({ jm8_access_token: "access", jm8_id_token: "identity", jm8_token_expires_at: String(Date.now() + 60000) });

test("native session persists tokens securely but never the PKCE verifier or browser tokens", async () => {
  const h = harness();
  for (const [k,v] of Object.entries(valid())) h.api.authStorage.setItem(k,v);
  h.api.authStorage.setItem("jm8_pkce_verifier", "private-verifier");
  await h.api.persistNativeSession();
  assert.equal(h.local.size, 0);
  assert.equal(JSON.parse(h.secure).jm8_access_token, "access");
  assert.ok(!h.secure.includes("private-verifier"));
});
test("native relaunch restores unexpired session", async () => {
  const h = harness("ios", JSON.stringify(valid()));
  await h.api.restoreNativeSession();
  assert.equal(h.api.authStorage.getItem("jm8_access_token"), "access");
});
test("expired and malformed sessions are removed", async () => {
  for (const stored of ["broken", JSON.stringify({ ...valid(), jm8_token_expires_at: "1" }), JSON.stringify({ ...valid(), unexpected: "bad" })]) {
    const h = harness("ios", stored);
    await h.api.restoreNativeSession();
    assert.equal(h.api.authStorage.getItem("jm8_access_token"), null);
    assert.equal(h.secure, undefined);
  }
});
test("failed secure deletion leaves a tombstone and cannot restore the old session", async () => {
  const h = harness("ios", JSON.stringify(valid()));
  await h.api.restoreNativeSession();
  h.failDelete();
  await assert.rejects(h.api.clearNativeSession());
  assert.equal(h.api.authStorage.getItem("jm8_access_token"), null);
  await assert.rejects(h.api.restoreNativeSession());
  assert.equal(h.api.authStorage.getItem("jm8_access_token"), null);
});
test("callback requires the exact native destination", async () => {
  const h = harness();
  assert.equal((await h.api.openNativeAuthorization("https://example.test")).searchParams.get("code"), "abc");
  for (const url of ["https://evil.test/auth/callback?code=x", "com.cloudwithmo.journalm8.dev://auth/other", "com.cloudwithmo.journalm8.dev://auth/callback#token"]) {
    h.plugin.authenticate = async () => ({ url });
    await assert.rejects(h.api.openNativeAuthorization("https://example.test"));
  }
});
test("web storage behavior is retained without invoking native storage", async () => {
  const h = harness("web");
  h.api.authStorage.setItem("jm8_access_token", "web-token");
  assert.equal(h.local.get("jm8_access_token"), "web-token");
  await h.api.persistNativeSession();
  assert.equal(h.secure, undefined);
});
