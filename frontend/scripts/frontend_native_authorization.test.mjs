import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { webcrypto } from "node:crypto";
import ts from "typescript";

const text = readFileSync(new URL("../src/auth/cognito.ts", import.meta.url), "utf8");
function harness(callbackMode = "valid") {
  const data = new Map();
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key,value) => data.set(key,value), removeItem: key => data.delete(key) };
  const counts = { persisted: 0, exchanged: 0, reloads: 0 };
  const callback = "com.cloudwithmo.journalm8.dev://auth/callback";
  const native = {
    isNativeIos: true, nativeCallback: callback, authStorage: storage,
    async clearNativeSession() { data.clear(); },
    async persistNativeSession() { counts.persisted++; },
    async restoreNativeSession() {},
    async openNativeAuthorization(value) {
      const url = new URL(value);
      assert.equal(url.searchParams.get("redirect_uri"), callback);
      assert.equal(url.searchParams.get("code_challenge_method"), "S256");
      assert.ok(url.searchParams.get("code_challenge"));
      if (callbackMode === "cancel") throw new Error("Sign-in was canceled.");
      const result = new URL(callback);
      result.searchParams.set("code", "test-code");
      result.searchParams.set("state", callbackMode === "wrong-state" ? "wrong" : url.searchParams.get("state"));
      if (callbackMode === "duplicate") result.searchParams.append("code", "second-code");
      return result;
    },
  };
  const exports = {};
  const code = ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = {
    exports, crypto: webcrypto, Uint8Array, TextEncoder, URL, URLSearchParams, Date, Event, atob, btoa,
    sessionStorage: storage,
    window: { location: { href: "capacitor://localhost/", reload() { counts.reloads++; } }, dispatchEvent() {} },
    require: name => name === "../platform/http" ? { serviceFetch: (...args) => context.fetch(...args) } : name === "./nativeSession" ? native : { frontendEnv: {
      appStage: "dev", apiEndpoint: "https://u06tdrfsua.execute-api.us-east-1.amazonaws.com",
      cognitoEnabled: true, cognitoDomain: "https://journalm8-dev-114743615542.auth.us-east-1.amazoncognito.com",
      cognitoClientId: "4t37mcfdkg5gdvl7ev8vt91ojg", cognitoRedirectUri: "http://localhost:5173/", cognitoLogoutUri: "http://localhost:5173/",
    } },
    async fetch(url, options) {
      counts.exchanged++;
      assert.ok(url.endsWith("/oauth2/token"));
      assert.equal(options.body.get("redirect_uri"), callback);
      assert.ok(options.body.get("code_verifier"));
      return { ok: true, async json() { return {
        access_token: "test-access", id_token: `header.${Buffer.from(JSON.stringify({ sub: "test-user" })).toString("base64url")}.signature`,
        expires_in: 3600, token_type: "Bearer",
      }; } };
    },
  };
  vm.runInNewContext(code, context);
  return { api: exports, counts, data };
}

test("native PKCE success persists session then reloads and removes verifier/state", async () => {
  const h = harness();
  await h.api.loginWithCognito();
  assert.equal(h.counts.exchanged, 1);
  assert.equal(h.counts.persisted, 1);
  assert.equal(h.counts.reloads, 1);
  assert.equal(h.api.getCurrentUser().sub, "test-user");
  assert.equal(h.data.has("jm8_pkce_verifier"), false);
  assert.equal(h.data.has("jm8_oauth_state"), false);
});
for (const mode of ["wrong-state", "duplicate", "cancel"]) {
  test(`native ${mode} cannot exchange or persist tokens`, async () => {
    const h = harness(mode);
    await assert.rejects(h.api.loginWithCognito());
    assert.equal(h.counts.exchanged, 0);
    assert.equal(h.counts.persisted, 0);
    assert.equal(h.counts.reloads, 0);
    assert.equal(h.data.size, 0);
  });
}
