import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = readFileSync(new URL('../src/platform/upload.ts', import.meta.url), 'utf8');
const url = 'https://journalm8-dev-raw-114743615542.s3.amazonaws.com/users/test/uploads/entry/image%20one.jpg?X-Amz-Credential=a%2Fb&X-Amz-Signature=test%2Bsignature';
function setup(platform = 'ios') {
  const calls = [];
  const native = { async request(options) { calls.push(options); return { status: 200 }; } };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, {
    exports, URL, Uint8Array, btoa,
    require: () => ({ Capacitor: { getPlatform: () => platform }, CapacitorHttp: native }),
    fetch: async (...args) => { calls.push(args); return new Response('', { status: 200 }); },
  });
  return { ...exports, calls, native };
}
test('native PUT preserves every image byte across encoding chunks and the signed URL', async () => {
  const h = setup();
  const bytes = Uint8Array.from({ length: 80000 }, (_, i) => i % 256);
  const file = new File([bytes], 'image.jpg', { type: 'image/jpeg' });
  await h.uploadJournalImage(url, file);
  const call = h.calls[0];
  assert.equal(call.url, url);
  assert.equal(call.method, 'PUT');
  assert.equal(call.dataType, 'file');
  assert.deepEqual(Buffer.from(call.data, 'base64'), Buffer.from(bytes));
  assert.deepEqual(Object.keys(call.headers), ['Content-Type']);
  assert.equal(call.headers['Content-Type'], 'image/jpeg');
  assert.equal(call.disableRedirects, true);
  assert.equal(call.shouldEncodeUrlParams, false);
});
test('PNG and empty MIME fallback match the content type used for signing', async () => {
  const h = setup();
  for (const mime of ['image/png', '']) {
    const file = new File(['image'], 'image', { type: mime });
    await h.uploadJournalImage(url, file);
    assert.equal(h.calls.at(-1).headers['Content-Type'], h.uploadContentType(file));
    assert.equal(h.uploadContentType(file), mime || 'image/jpeg');
  }
});
test('unapproved hosts, user info, protocols and paths fail before reading private image bytes', async () => {
  const h = setup();
  const file = { size: 10, type: 'image/jpeg', arrayBuffer() { throw new Error('must not read'); } };
  for (const target of [
    url.replace('.s3.amazonaws.com', '.s3.amazonaws.com.evil.test'),
    url.replace('https:', 'http:'), url.replace('https://', 'https://user@'),
    url.replace('/users/', '/other/'), url + '#fragment',
    url.replace('journalm8-dev-raw', 'journalm8-prod-raw'),
  ]) await assert.rejects(h.uploadJournalImage(target, file), /Unsupported development/);
  assert.equal(h.calls.length, 0);
});
test('native preflight bounds image memory before read or network', async () => {
  const h = setup();
  for (const size of [0, 10 * 1024 * 1024 + 1]) {
    assert.throws(() => h.validateImageUpload({ size }), /10 MiB/);
    await assert.rejects(h.uploadJournalImage(url, { size }), /10 MiB/);
  }
  assert.equal(h.calls.length, 0);
});
test('redirects and HTTP failures reject; transport errors do not expose signed URLs', async () => {
  const h = setup();
  const file = new File(['image'], 'image.jpg', { type: 'image/jpeg' });
  for (const status of [301, 403, 500]) {
    h.native.request = async () => ({ status });
    await assert.rejects(h.uploadJournalImage(url, file), new RegExp(`HTTP ${status}`));
  }
  h.native.request = async () => { throw new Error(url); };
  await assert.rejects(h.uploadJournalImage(url, file), (error) => {
    assert.match(error.message, /Image transfer failed/);
    assert.ok(!error.message.includes('X-Amz')); return true;
  });
});
test('web upload retains the original File as its fetch body', async () => {
  const h = setup('web');
  const file = new File(['image'], 'image.png', { type: 'image/png' });
  await h.uploadJournalImage(url, file);
  assert.equal(h.calls[0][0], url);
  assert.equal(h.calls[0][1].body, file);
  assert.equal(h.calls[0][1].headers['content-type'], 'image/png');
});
