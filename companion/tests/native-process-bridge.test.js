const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

function bridge({ spawnResult = Promise.resolve(1234), pendingWrite } = {}) {
  const calls = [], events = new Map(), unsubscribed = [];
  const window = { __pendingBinaryWrite: pendingWrite, __TAURI__: {
    core: { invoke(name, args) {
      calls.push({ name, args });
      if (name === "spawn_process") return spawnResult;
      return Promise.resolve();
    } },
    event: { listen(name, cb) {
      events.set(name, cb);
      return Promise.resolve(() => { unsubscribed.push(name); events.delete(name); });
    } },
  } };
  new Function("window", "crypto", fs.readFileSync(path.join(root, "shared/bridge/tauri-adapter.js"), "utf8"))(window, { randomUUID: () => "owned-id" });
  return { adapter: window.__tauriAdapter, calls, events, unsubscribed };
}

test("native cancellation before a pending file write prevents the spawn", async () => {
  const pending = deferred(), b = bridge({ pendingWrite: pending.promise }), closes = [];
  const proc = b.adapter.spawn("fixture", [], { detached: true });
  proc.on("close", code => closes.push(code));
  proc.stdin.write("must not run");
  proc.killTree();
  await flush();
  assert.deepEqual(closes, [-1], "cancellation settles without waiting for an unfinished file write");
  assert.equal(b.events.size, 0);
  pending.resolve(); await flush();
  assert.deepEqual(b.calls, []);
  assert.deepEqual(closes, [-1]);
  assert.equal(b.events.size, 0);
});

test("native cancellation during asynchronous spawn waits for registry ownership and suppresses queued stdin", async () => {
  const pending = deferred(), b = bridge({ spawnResult: pending.promise });
  const proc = b.adapter.spawn("fixture", [], { detached: true });
  proc.stdin.write("must not run");
  await flush();
  assert.deepEqual(b.calls.map(x => x.name), ["spawn_process"]);
  proc.killTree();
  pending.resolve(1234); await flush();
  assert.deepEqual(b.calls.map(x => x.name), ["spawn_process", "kill_process"]);
  assert.equal(b.calls[1].args.id, "owned-id");
  proc.killTree(); await flush();
  assert.equal(b.calls.filter(x => x.name === "kill_process").length, 1);
});

test("native normal launch subscribes first, writes valid stdin, forwards output and closes once", async () => {
  const b = bridge(), output = [], closes = [];
  const proc = b.adapter.spawn("fixture", ["argument"], { detached: true });
  proc.stdout.on("data", value => output.push(value));
  proc.on("close", code => closes.push(code));
  proc.stdin.on("error", () => assert.fail("valid stdin should succeed"));
  proc.stdin.write("valid request"); await flush();
  assert.equal(b.calls[0].args.opts.detached, true);
  assert.deepEqual(b.calls.map(x => x.name), ["spawn_process", "stdin_write"]);
  b.events.get("process-stdout-owned-id")({ payload: "valid result" });
  const close = b.events.get("process-close-owned-id");
  close({ payload: 0 }); close({ payload: 0 });
  assert.deepEqual(output, ["valid result"]);
  assert.deepEqual(closes, [0]);
  assert.equal(b.unsubscribed.length, 3);
});

test("native bootstrap preserves the process-group request through its Node compatibility layer", () => {
  const source = fs.readFileSync(path.join(root, "macos/web/macos-bootstrap.js"), "utf8");
  const start = source.indexOf("  const tauriChildProcess = {");
  const end = source.indexOf("  const tauriUtil = {", start);
  assert.ok(start >= 0 && end > start);
  let received;
  const shim = new Function("adapter", source.slice(start, end) + "\nreturn tauriChildProcess;")({ spawn(...args) { received = args; } });
  shim.spawn("fixture", ["arg"], { cwd: "/work", detached: true, env: { FORCE_COLOR: "0" }, stdio: ["pipe", "pipe", "pipe"] });
  assert.deepEqual(received, ["fixture", ["arg"], { cwd: "/work", detached: true, env: { FORCE_COLOR: "0" } }]);
});
