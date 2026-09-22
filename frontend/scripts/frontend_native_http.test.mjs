import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(new URL("../src/platform/http.ts", import.meta.url), "utf8");
function setup(platform = "ios") {
  const calls = [];
  const native = { async request(options) { calls.push(options); return { status: 200, headers: { "content-type": "application/json" }, data: { entries: [] } }; } };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, {
    exports, URL, URLSearchParams, Headers, Response, DOMException,
    require: () => ({ Capacitor: { getPlatform: () => platform }, CapacitorHttp: native }),
    fetch: async (...args) => { calls.push(args); return new Response("web"); },
  });
  return { send: exports.serviceFetch, calls, native };
}
const api = "https://u06tdrfsua.execute-api.us-east-1.amazonaws.com/entries";
test("native JSON request preserves bearer, body and response and disables redirects", async () => {
  const h = setup();
  const response = await h.send(api, { method: "POST", headers: { Authorization: "Bearer test", "content-type": "application/json" }, body: '{"text":"hello"}' });
  assert.equal(h.calls[0].headers.authorization, "Bearer test");
  assert.equal(h.calls[0].data, '{"text":"hello"}');
  assert.equal(h.calls[0].disableRedirects, true);
  assert.deepEqual(await response.json(), { entries: [] });
});
test("token form body is encoded once", async () => {
  const h = setup();
  const body = new URLSearchParams({ code: "x+y", code_verifier: "abc" });
  await h.send("https://journalm8-dev-114743615542.auth.us-east-1.amazoncognito.com/oauth2/token", { method: "POST", body, headers: { "content-type": "application/x-www-form-urlencoded" } });
  assert.equal(h.calls[0].data, "code=x%2By&code_verifier=abc");
});
test("unapproved native destinations fail before sending credentials", async () => {
  const h = setup();
  for (const url of ["https://evil.test", "http://u06tdrfsua.execute-api.us-east-1.amazonaws.com/entries", "https://journalm8-dev-114743615542.auth.us-east-1.amazoncognito.com/other"]) {
    await assert.rejects(h.send(url));
  }
  assert.equal(h.calls.length, 0);
});
test("native HTTP failures remain ordinary responses for existing API error handling", async () => {
  const h = setup();
  h.native.request = async () => ({ status: 401, headers: {}, data: { message: "Unauthorized" } });
  const response = await h.send(api);
  assert.equal(response.status, 401);
  assert.equal(response.ok, false);
});
test("abort before sending makes no request; abort during request rejects promptly", async () => {
  const h = setup();
  const c = new AbortController(); c.abort();
  await assert.rejects(h.send(api, { signal: c.signal }), { name: "AbortError" });
  assert.equal(h.calls.length, 0);
  h.native.request = () => new Promise(() => {});
  const other = new AbortController();
  const pending = h.send(api, { signal: other.signal });
  other.abort();
  await assert.rejects(pending, { name: "AbortError" });
});
test("browser keeps fetch behavior", async () => {
  const h = setup("web");
  assert.equal(await (await h.send(api)).text(), "web");
  assert.equal(h.calls[0][0], api);
});
