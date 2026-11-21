const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const Runner = require("../modules/claude-runner");

function child() {
  const proc = new EventEmitter();
  proc.stdout = new EventEmitter();
  proc.stderr = new EventEmitter();
  proc.stdin = new EventEmitter();
  proc.stdin.writable = true;
  proc.stdin.write = () => true;
  proc.stdin.end = () => { proc.stdin.writable = false; };
  proc.signals = [];
  proc.kill = signal => { proc.signals.push(signal); return true; };
  return proc;
}

function setup(processes = [child()]) {
  const queue = [...processes], timers = [], ends = [], errors = [], output = [], interactions = [];
  const runner = new Runner({ claudePath: "/unused", projectPath: ".", executionTimeoutMs: 1000 }, {
    spawnProcess: () => queue.shift(),
    schedule: (fn, delay) => { const timer = { fn, delay, cancelled: false }; timers.push(timer); return timer; },
    cancelTimer: timer => { if (timer) timer.cancelled = true; },
  });
  runner._snapshotJsonlFiles = () => new Set();
  runner._detectNewSession = () => null;
  return { runner, timers, ends, errors, output, interactions, callbacks: {
    onEnd: value => ends.push(value), onError: value => errors.push(value), onDelta: value => output.push(value),
    onPermissionRequest: value => interactions.push(value),
  }};
}

test("cancel fences terminal output, permission requests, and session mutation immediately", () => {
  const proc = child(), s = setup([proc]);
  s.runner.run("request", s.callbacks);
  s.runner.cancel();
  proc.stdout.emit("data", '{"type":"result","subtype":"success","session_id":"stale","result":"late success"}\n');
  proc.stdout.emit("data", '{"type":"control_request","request_id":"stale","request":{"subtype":"can_use_tool"}}\n');
  proc.emit("close", 0);
  assert.equal(s.ends.length, 0);
  assert.equal(s.output.length, 0);
  assert.equal(s.interactions.length, 0);
  assert.equal(s.runner.sessionId, null);
  assert.equal(s.runner.sendControlResponse({ behavior: "allow" }), false);
  assert.deepEqual(proc.signals, ["SIGTERM"]);
});

test("ignored termination escalates only the original process", () => {
  const first = child(), second = child(), s = setup([first, second]);
  const old = s.runner.run("old", s.callbacks);
  s.runner.run("replacement", s.callbacks);
  old.cancel();
  const escalation = s.timers.find(timer => timer.delay === 2000);
  escalation.fn();
  assert.deepEqual(first.signals, ["SIGTERM", "SIGKILL"]);
  assert.deepEqual(second.signals, []);
});

test("observed process close cancels escalation", () => {
  const proc = child(), s = setup([proc]);
  s.runner.run("request", s.callbacks);
  s.runner.cancel();
  const escalation = s.timers.find(timer => timer.delay === 2000);
  proc.emit("close", null, "SIGTERM");
  assert.equal(escalation.cancelled, true);
  escalation.fn();
  assert.deepEqual(proc.signals, ["SIGTERM"]);
});

test("direct child exit still terminates surviving members of its POSIX group", { skip: process.platform === "win32" }, () => {
  const proc = child(), s = setup([proc]), signals = [];
  proc.pid = 12345;
  s.runner._killProcessGroup = (pid, signal) => signals.push({ pid, signal });
  s.runner.run("request", s.callbacks);
  s.runner.cancel();
  proc.emit("close", 0, null);
  assert.deepEqual(signals, [{ pid: 12345, signal: "SIGTERM" }, { pid: 12345, signal: "SIGKILL" }]);
  assert.equal(s.timers.find(timer => timer.delay === 2000).cancelled, true);
});

test("deadline terminates execution and never publishes later success", () => {
  const proc = child(), s = setup([proc]);
  s.runner.run("request", s.callbacks);
  s.timers.find(timer => timer.delay === 1000).fn();
  proc.stdout.emit("data", '{"type":"result","subtype":"success","result":"late"}\n');
  proc.emit("close", 0);
  assert.equal(s.errors.length, 1);
  assert.match(s.errors[0].message, /deadline/);
  assert.equal(s.ends.length, 0);
  assert.equal(s.output.length, 0);
  assert.deepEqual(proc.signals, ["SIGTERM"]);
});

test("configured timeout cannot disable or widen its hard ceiling", () => {
  for (const executionTimeoutMs of [0, -1, Infinity, true, 7200001]) {
    assert.throws(() => new Runner({ claudePath: "/unused", executionTimeoutMs }));
  }
});

test("cancellation inside the first message fences the rest of the same output chunk", () => {
  const proc = child(), s = setup([proc]);
  const delta = text => JSON.stringify({ type: "stream_event", event: {
    type: "content_block_delta", delta: { type: "text_delta", text },
  }});
  s.callbacks.onDelta = text => { s.output.push(text); s.runner.cancel(); };
  s.runner.run("request", s.callbacks);
  proc.stdout.emit("data", delta("cancel now") + "\n" + delta("LATE OUTPUT") + "\n"
    + '{"type":"result","subtype":"success","session_id":"late-session","result":"late"}\n');
  assert.deepEqual(s.output, ["cancel now"]);
  assert.equal(s.runner.sessionId, null);
  assert.equal(s.ends.length, 0);
});

test("replacement during final buffer flush cannot clear the new run", () => {
  const first = child(), second = child(), s = setup([first, second]);
  s.callbacks.onDelta = () => { s.runner.run("replacement", { ...s.callbacks, onDelta() {} }); };
  s.runner.run("first", s.callbacks);
  first.stdout.emit("data", "unterminated non-json output");
  first.emit("close", 0);
  assert.equal(s.ends.length, 0);
  assert.equal(s.runner._activeProcess, second);
  assert.equal(s.runner.sendControlResponse({ behavior: "deny" }), true);
});
