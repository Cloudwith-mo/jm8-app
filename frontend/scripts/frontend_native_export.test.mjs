import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = readFileSync(new URL('../src/platform/export.ts', import.meta.url), 'utf8');
function setup(platform = 'ios') {
  const calls = [];
  const native = { async shareTranscript(options) { calls.push(options); return { completed: true }; } };
  const anchor = { click() { calls.push('click'); }, remove() { calls.push('remove'); } };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, {
    exports, Blob,
    require: () => ({ Capacitor: { getPlatform: () => platform }, registerPlugin: (name) => { assert.equal(name, 'JM8NativeExport'); return native; } }),
    document: { createElement: () => anchor, body: { appendChild: () => calls.push('append') } },
    window: { URL: { createObjectURL: (blob) => { calls.push(blob); return 'blob:test'; }, revokeObjectURL: () => calls.push('revoke') }, setTimeout: (f) => f() },
  });
  return { send: exports.exportTranscriptFile, calls, native, anchor };
}
test('native share preserves Unicode transcript and filename and awaits completion', async () => {
  const h = setup(); let finish;
  h.native.shareTranscript = (options) => { h.calls.push(options); return new Promise(resolve => { finish = resolve; }); };
  let settled = false;
  const pending = h.send('jm8-entry-test.txt', 'Journal\nGratitude — الحمد لله 🌱').then(result => { settled = true; return result; });
  await Promise.resolve(); assert.equal(settled, false);
  assert.equal(h.calls[0].text, 'Journal\nGratitude — الحمد لله 🌱');
  assert.equal(h.calls[0].filename, 'jm8-entry-test.txt');
  finish({ completed: true }); assert.equal(await pending, 'shared');
  assert.equal(h.calls.length, 1);
});
test('cancellation does not report a successful export', async () => {
  const h = setup(); h.native.shareTranscript = async () => ({ completed: false });
  assert.equal(await h.send('entry.txt', 'text'), 'cancelled');
});
test('native errors propagate without falling back to a fake browser download', async () => {
  const h = setup(); h.native.shareTranscript = async () => { throw new Error('write failed'); };
  await assert.rejects(h.send('entry.txt', 'text'), /write failed/);
  assert.equal(h.calls.length, 0);
});
test('browser downloads a UTF-8 text file and cleans up the anchor and object URL', async () => {
  const h = setup('web');
  assert.equal(await h.send('entry.txt', 'Hello 🌱'), 'download-started');
  assert.equal(await h.calls[0].text(), 'Hello 🌱');
  assert.equal(h.calls[0].type, 'text/plain;charset=utf-8');
  assert.equal(h.anchor.download, 'entry.txt');
  assert.deepEqual(h.calls.slice(1), ['append', 'click', 'remove', 'revoke']);
});
