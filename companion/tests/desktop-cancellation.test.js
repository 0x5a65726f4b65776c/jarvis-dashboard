const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const source = fs.readFileSync(path.resolve(__dirname, "../../src/widgets/voice-command/desktop/process-manager.js"), "utf8");
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

function fakeProcess() {
  const proc = new EventEmitter();
  proc.stdout = new EventEmitter();
  proc.stderr = new EventEmitter();
  proc.stdin = new EventEmitter();
  proc.stdin.writable = true;
  proc.stdin.write = () => true;
  proc.signals = [];
  proc.kill = signal => proc.signals.push(signal);
  return proc;
}

function manager({ spawn, claudePath = "/unused", config = {}, options = {} } = {}) {
  const ctx = { nodeFs: fs, nodePath: path, config: { widgets: { voiceCommand: {
    ...config, terminal: { claudePath },
  } } } };
  const { createProcessManager } = new Function("ctx", "require", "process", source)(ctx,
    name => name === "child_process" && spawn ? { spawn } : require(name), process);
  return createProcessManager({ storageAdapter: { removeSettingsPermission() {} }, stripAnsi: x => x, ...options });
}

test("every local launch owns a separate process group; replacement only cancels its predecessor", () => {
  const children = [], launchOptions = [], timers = [];
  const pm = manager({ spawn: (_bin, _args, opts) => {
    launchOptions.push(opts); const child = fakeProcess(); children.push(child); return child;
  }, options: { schedule: (fn, delay) => { const t = { fn, delay }; timers.push(t); return t; }, cancelTimer() {} } });
  pm.spawnProcess(null, ".");
  const state = { sessionId: "session", pendingPermissions: [] };
  pm.spawnRetryProcess(state, ".");
  pm.spawnResumeProcess(state, ".", "answer");
  assert.ok(launchOptions.every(opts => opts.detached === (process.platform !== "win32")));
  assert.deepEqual(children.map(p => p.signals), [["SIGTERM"], ["SIGTERM"], []]);
  timers.filter(t => t.delay === 2000).forEach(t => t.fn());
  assert.deepEqual(children[2].signals, []);
  children[0].emit("close", 0);
  assert.equal(pm.process, children[2]);
  pm.kill();
});

test("desktop deadline enters the existing failure path and cannot be disabled or widened", () => {
  const proc = fakeProcess(), timers = [], errors = [];
  const pm = manager({ spawn: () => proc, config: { executionTimeoutMs: 100 }, options: {
    schedule: (fn, delay) => { const t = { fn, delay }; timers.push(t); return t; }, cancelTimer() {},
  } });
  pm.spawnProcess(null, ".");
  proc.on("error", err => errors.push(err));
  timers.find(t => t.delay === 100).fn();
  assert.match(errors[0].message, /deadline/);
  assert.deepEqual(proc.signals, ["SIGTERM"]);
  assert.equal(pm.sendStdinMessage({ type: "control_response" }), false, "timed-out work loses stdin authority immediately");
  proc.emit("close", null, "SIGTERM");
  timers.find(t => t.delay === 2000).fn();
  assert.deepEqual(proc.signals, ["SIGTERM"]);
  for (const executionTimeoutMs of [0, -1, Infinity, true, 7200001]) {
    assert.throws(() => manager({ config: { executionTimeoutMs } }), /executionTimeoutMs/);
  }
});

test("native cancellation uses the adapter owner, never the browser PID shim", () => {
  const proc = fakeProcess(); proc.pid = 12345;
  proc.killTree = signal => proc.signals.push(signal);
  const pm = manager({ spawn: () => proc, options: {
    killProcessGroup() { assert.fail("native PID must not reach process.kill"); },
  } });
  pm.spawnProcess(null, "."); pm.kill(); proc.emit("close", -1);
  assert.deepEqual(proc.signals, ["SIGTERM"]);
});

async function processFixture(cancel, ignoreLeader = false) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "jarvis-cancellation-test-"));
  const marker = path.join(dir, "effect"), ready = path.join(dir, "ready"), bin = path.join(dir, "fixture");
  const delay = ignoreLeader ? 2600 : 350;
  const descendant = `process.on('SIGTERM',()=>{});require('fs').writeFileSync(${JSON.stringify(ready)},'ready');setTimeout(()=>require('fs').writeFileSync(${JSON.stringify(marker)},'effect'),${delay});setTimeout(()=>{},${delay + 200});`;
  fs.writeFileSync(bin, `#!${process.execPath}\n${ignoreLeader ? "process.on('SIGTERM',()=>{});" : ""}require('child_process').spawn(process.execPath,['-e',${JSON.stringify(descendant)}],{stdio:'ignore'});setInterval(()=>{},1000);\n`, { mode: 0o700 });
  const pm = manager({ claudePath: bin });
  const proc = pm.spawnProcess(null, dir);
  try {
    for (let n = 0; n < 200 && !fs.existsSync(ready); n++) await wait(10);
    assert.ok(fs.existsSync(ready), "descendant installed its signal handler");
    if (cancel) pm.kill();
    await wait(delay + 200);
    return fs.existsSync(marker);
  } finally {
    pm.kill();
    try { process.kill(-proc.pid, "SIGKILL"); } catch {}
    await wait(30);
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("real descendant control writes its marker; cancellation prevents it after the leader exits", { skip: process.platform === "win32", timeout: 6000 }, async () => {
  assert.equal(await processFixture(false), true, "positive control exercises the actual side effect");
  assert.equal(await processFixture(true), false, "a descendant ignoring TERM must not outlive its cancelled leader");
});

test("real cancellation escalates when both leader and descendant ignore termination", { skip: process.platform === "win32", timeout: 6000 }, async () => {
  assert.equal(await processFixture(true, true), false);
});
