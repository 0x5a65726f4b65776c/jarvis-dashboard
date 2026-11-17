const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const Runner = require("../modules/claude-runner");
const Pipeline = require("../modules/voice-pipeline");
const protocol = require("../modules/protocol");
const widget = path.resolve(__dirname, "../../src/widgets/voice-command");
const { completionState, historyCompletion, createStreamHandler } = new Function("ctx", fs.readFileSync(path.join(widget, "core/stream-handler.js"), "utf8"))({});

function processFake() {
  const proc = new EventEmitter();
  proc.stdout = new EventEmitter();
  proc.stderr = new EventEmitter();
  proc.stdin = new EventEmitter();
  proc.stdin.writable = true;
  proc.stdin.write = () => true;
  proc.stdin.end = () => { proc.stdin.writable = false; };
  proc.kill = () => { proc.killed = true; };
  return proc;
}

function setup(processes = [processFake()]) {
  const queue = [...processes];
  const runner = new Runner({ claudePath: "/test/unused", projectPath: "." }, { spawnProcess: () => queue.shift() });
  runner._snapshotJsonlFiles = () => new Set();
  runner._detectNewSession = () => null;
  const ends = [], errors = [], deltas = [];
  const callbacks = { onEnd: x => ends.push(x), onError: x => errors.push(x), onDelta: x => deltas.push(x) };
  return { runner, callbacks, ends, errors, deltas };
}

const ok = { exitCode: 0, resultReceived: true, resultFailed: false };
test("process success is not task verification", () => {
  const result = completionState(JSON.parse(protocol.streamEnd("session", ok)));
  assert.equal(result.status, "completed");
  assert.equal(result.taskVerified, false);
  assert.equal(result.label, "Response finished");
});

for (const [name, input, expected] of [
  ["legacy end", {}, "unknown"],
  ["exit zero without result", { exitCode: 0 }, "unknown"],
  ["bare result", { exitCode: 0, resultReceived: true }, "unknown"],
  ["nonzero exit", { ...ok, exitCode: 2 }, "failed"],
  ["signal", { ...ok, exitCode: null, signal: "SIGTERM" }, "failed"],
  ["application error at exit zero", { ...ok, resultFailed: true }, "failed"],
  ["untrusted status claim", { status: "completed", taskVerified: true }, "unknown"],
]) test(name + " cannot show success", () => {
  const result = completionState(input);
  assert.equal(result.status, expected);
  assert.notEqual(result.badgeState, "success");
  assert.equal(result.taskVerified, false);
});

test("replayed history retains failure and treats legacy history as unknown", () => {
  assert.equal(historyCompletion([{ role: "assistant", text: "done" }]).status, "unknown");
  const history = [{ role: "assistant", completion: ok }, { role: "assistant", completion: { exitCode: 1 } }];
  assert.equal(historyCompletion(JSON.parse(JSON.stringify(history))).status, "failed");
  history.push({ role: "user", text: "new request" });
  assert.equal(historyCompletion(history).status, "unknown");
});

test("network stream handler preserves completion evidence", () => {
  let result;
  const handler = createStreamHandler({ onResult: x => { result = x; } });
  handler.handleNetworkEvent("stream_end", JSON.parse(protocol.streamEnd("session", { ...ok, exitCode: 9 })));
  assert.equal(result.exitCode, 9);
  assert.equal(completionState(result).status, "failed");
});

test("runner parses final result without newline and preserves real exit", () => {
  const proc = processFake(), s = setup([proc]);
  s.runner.run("request", s.callbacks);
  proc.stdout.emit("data", JSON.stringify({ type: "result", subtype: "success", is_error: false }));
  proc.emit("close", 0, null);
  assert.equal(s.ends.length, 1);
  assert.equal(completionState(s.ends[0]).status, "completed");
});

test("empty error result closes stdin and fails even at exit zero", () => {
  const proc = processFake(), s = setup([proc]);
  s.runner.run("request", s.callbacks);
  proc.stdout.emit("data", JSON.stringify({ type: "result", subtype: "error_max_turns", is_error: true }) + "\n");
  assert.equal(proc.stdin.writable, false);
  proc.emit("close", 0);
  assert.equal(completionState(s.ends[0]).status, "failed");
});

test("signal termination never becomes exit zero", () => {
  const proc = processFake(), s = setup([proc]);
  s.runner.run("request", s.callbacks);
  proc.emit("close", null, "SIGTERM");
  assert.equal(s.ends[0].exitCode, null);
  assert.equal(s.ends[0].signal, "SIGTERM");
});

test("process error followed by close settles only once", () => {
  const proc = processFake(), s = setup([proc]);
  s.runner.run("request", s.callbacks);
  proc.emit("error", new Error("spawn failure"));
  proc.emit("close", 0);
  assert.equal(s.errors.length, 1);
  assert.equal(s.ends.length, 0);
});

test("broken stdin settles as error without a completion", () => {
  const proc = processFake(), s = setup([proc]);
  proc.stdin.write = () => { throw new Error("broken pipe"); };
  s.runner.run("request", s.callbacks);
  proc.emit("close", 0);
  assert.equal(s.errors.length, 1);
  assert.equal(s.ends.length, 0);
});

test("late events and old cancellation cannot finish or kill a newer run", () => {
  const first = processFake(), second = processFake(), s = setup([first, second]);
  const old = s.runner.run("first", s.callbacks);
  s.runner.run("second", s.callbacks);
  first.stdout.emit("data", '{"type":"result","subtype":"success"}\n');
  first.emit("close", 0);
  old.cancel();
  assert.equal(second.killed, undefined);
  assert.equal(s.ends.length, 0);
  second.emit("close", 3);
  assert.equal(s.ends[0].exitCode, 3);
});

test("pipeline passes failure facts and ignores superseded callbacks", () => {
  const calls = [], messages = [];
  const conn = { runner: { run: (text, cbs) => { calls.push(cbs); return { cancel() {} }; } }, send: raw => { messages.push(JSON.parse(raw)); return true; } };
  const pipeline = new Pipeline(conn, { langConfig: {} });
  pipeline.runClaude("first");
  pipeline.runClaude("second");
  calls[0].onEnd({ exitCode: 0, sessionId: "old", ...ok });
  assert.equal(messages.length, 0);
  calls[1].onEnd({ exitCode: 7, sessionId: "new", resultReceived: false });
  assert.equal(messages.length, 1);
  assert.equal(messages[0].exitCode, 7);
  assert.equal(conn.activeRunner, null);
});

test("synchronous runner error leaves no active handle", () => {
  const conn = { runner: { run: (text, cbs) => { cbs.onError(new Error("not installed")); return { cancel() {} }; } }, send: () => true };
  new Pipeline(conn, { langConfig: {} }).runClaude("request");
  assert.equal(conn.activeRunner, null);
});

function localHandler(proc) {
  const source = fs.readFileSync(path.join(widget, "index.js"), "utf8");
  const fn = source.slice(source.indexOf("function attachProcessHandlers(proc) {"), source.indexOf("\nfunction handleToolComplete"));
  const state = { process: proc, lineBuf: "", buffer: "", resultReceived: false, resultFailed: null,
    pendingPermissions: [], pendingInteractions: [], conversationHistory: [], uiState: "streaming" };
  const reconnect = { getActiveStream: () => state };
  const attach = new Function("reconnect", "stripAnsi", "interactiveCfg", "completionState", fn + "\nreturn attachProcessHandlers;")(reconnect, x => x, {}, completionState);
  attach(proc);
  return state;
}

test("local process flushes terminal error result and keeps it in history", () => {
  const proc = processFake(), state = localHandler(proc);
  proc.stdout.emit("data", '{"type":"result","is_error":true}');
  proc.emit("close", 0);
  assert.equal(state.uiState, "error");
  assert.equal(state.conversationHistory[0].completion.status, "failed");
});

test("local missing result is preserved as unverified", () => {
  const proc = processFake(), state = localHandler(proc);
  proc.emit("close", 0);
  assert.equal(state.completion.status, "unknown");
  assert.equal(historyCompletion(state.conversationHistory).status, "unknown");
});

test("detached local process and pipe errors retain one failed history record", () => {
  for (const target of ["process", "stdin"]) {
    const proc = processFake(), state = localHandler(proc);
    state.buffer = "partial response";
    (target === "process" ? proc : proc.stdin).emit("error", new Error("fixture failure"));
    proc.emit("close", 0);
    assert.equal(state.uiState, "error");
    assert.equal(state.conversationHistory.length, 1);
    assert.equal(state.conversationHistory[0].text, "partial response");
    assert.equal(historyCompletion(state.conversationHistory).status, "failed");
  }
});

test("remote errors retain partial output and failure across history restoration", () => {
  for (const name of ["index.js", "mobile.js"]) {
    const source = fs.readFileSync(path.join(widget, name), "utf8");
    const start = source.indexOf('  networkClient.on("error", (msg) => {');
    const handler = source.slice(start, source.indexOf("\n  });", start) + 7);
    const run = new Function("completionState", `
      let callback, saved, remoteTurnActive = true, remoteFullBuffer = "partial response", remoteSpeakBuffer = "", activeCursorEl = null;
      let activeOutputContent = null, activeStreamRenderer = { finalize() {} };
      const conversationHistory = [];
      const networkClient = { on: (name, fn) => { callback = fn; } };
      const terminal = { appendErrorLine() {}, setBadgeState() {} };
      const syncToManager = () => { saved = JSON.parse(JSON.stringify(conversationHistory)); };
      const setUIState = () => {};
      ${handler}
      callback({ stage: "runner", message: "fixture failure" });
      return saved;
    `);
    const restored = run(completionState);
    assert.equal(restored[0].text, "partial response", name);
    assert.equal(historyCompletion(restored).status, "failed", name);
  }
});

test("retry and answer-resume start with no prior completion facts", () => {
  const factory = new Function("ctx", "require", fs.readFileSync(path.join(widget, "desktop/process-manager.js"), "utf8"));
  const { createProcessManager } = factory({ nodePath: path, nodeFs: fs,
    config: { widgets: { voiceCommand: { terminal: { claudePath: "/test/unused" } } } } },
    name => name === "child_process" ? { spawn: () => processFake() } : require(name));
  const manager = createProcessManager({ storageAdapter: {}, stripAnsi: x => x });
  for (const method of ["spawnRetryProcess", "spawnResumeProcess"]) {
    const state = { sessionId: "fixture-session", pendingPermissions: [], resultReceived: true,
      resultFailed: true, exitCode: 9, signal: "SIGTERM", completion: completionState({ exitCode: 9 }) };
    manager[method](state, ".", "fixture answer");
    assert.equal(state.resultReceived, false);
    for (const field of ["resultFailed", "exitCode", "signal", "completion"]) assert.equal(state[field], null, field);
  }
});

test("remote session changes are blocked until the active turn settles", () => {
  for (const name of ["index.js", "mobile.js"]) {
    const source = fs.readFileSync(path.join(widget, name), "utf8");
    const guardStart = source.indexOf("function guardRemoteTurn() {");
    const guard = source.slice(guardStart, source.indexOf("\n}\n", guardStart) + 3);
    const run = new Function(`
      let remoteTurnActive = true, noticed = 0;
      const isRemoteMode = true;
      const Notice = function() { noticed++; };
      ${guard}
      const busy = guardRemoteTurn();
      remoteTurnActive = false;
      return {busy, free:guardRemoteTurn(), noticed};
    `);
    assert.deepEqual(run(), { busy: true, free: false, noticed: 1 });
    for (const entry of ["switchToSession", "createNewSession", "closeSession"]) {
      const start = source.indexOf("function " + entry + "(");
      const bodyStart = source.indexOf("{", start);
      const prefix = source.slice(bodyStart + 1, source.indexOf("\n", bodyStart + 2));
      assert.match(prefix, /guardRemoteTurn\(\)/, name + ": " + entry);
    }
  }
});

test("disconnect releases remote ownership as unknown and does not replay", () => {
  for (const name of ["index.js", "mobile.js"]) {
    const source = fs.readFileSync(path.join(widget, name), "utf8");
    const start = source.lastIndexOf("  networkClient.onStateChange((state) => {");
    const handler = source.slice(start, source.indexOf("\n  });", start) + 7);
    const run = new Function("completionState", `
      let callback, saved, remoteTurnActive = true, remoteFullBuffer = "partial response", remoteSpeakBuffer = "";
      let activeOutputContent = null, activeCursorEl = null, activeStreamRenderer = { finalize() {} };
      const conversationHistory = [];
      const networkClient = { onStateChange(fn) { callback=fn; } };
      const recorder = {cancel(){}}, tts = {stop(){}}, terminal = {appendCompletionLine(){}};
      const syncToManager = () => { saved = JSON.parse(JSON.stringify(conversationHistory)); };
      const setUIState = () => {};
      ${handler}
      callback("disconnected");
      callback("reconnecting");
      return {saved,remoteTurnActive};
    `);
    const result = run(completionState);
    assert.equal(result.remoteTurnActive, false, name);
    assert.equal(result.saved.length, 1, name);
    assert.equal(result.saved[0].text, "partial response");
    assert.equal(historyCompletion(result.saved).status, "unknown");
  }
});

test("a background local completion updates only its owning session", () => {
  const source = fs.readFileSync(path.join(widget, "index.js"), "utf8");
  const delegates = source.slice(source.indexOf("function attachDelegates("), source.indexOf("\nfunction attachProcessHandlers("));
  const run = new Function("completionState", `
    let conversationHistory = [{role:"user",text:"visible request"}], currentSessionId = "visible-provider";
    let fullBuffer = "visible buffer", preSpawnJsonlSet = null, rendered = 0, saved = 0;
    const owner = { sessionId: "owner-provider", conversationHistory: [] };
    const sessionManager = { getActiveSessionId: () => "visible", getSession: () => owner, saveImmediate: () => { saved++; } };
    const markdownRenderer = { createStreamRenderer: () => ({ append() {}, getTextNodes: () => [], finalize() {} }) };
    const terminal = { addMessageCopyIcon() {}, appendCompletionLine() { rendered++; } };
    const processMgr = {}, reconnect = { clearStreamState() {} }, tabs = { render() {} };
    const state = { jarvisSessionId:"owner", sessionId:"owner-provider", buffer:"owner output", fullBuffer:"owner buffer",
      conversationHistory:[{role:"user",text:"owner request"}], completion:completionState({exitCode:1}) };
    ${delegates}
    attachDelegates(state, {}, {}, null, () => state.buffer, () => {});
    state._onClose(1);
    return { owner, conversationHistory, currentSessionId, fullBuffer, rendered, saved };
  `);
  const result = run(completionState);
  assert.equal(result.owner.conversationHistory.length, 2);
  assert.equal(result.owner.conversationHistory[0].text, "owner request");
  assert.equal(historyCompletion(result.owner.conversationHistory).status, "failed");
  assert.equal(result.conversationHistory.length, 1);
  assert.equal(result.currentSessionId, "visible-provider");
  assert.equal(result.fullBuffer, "visible buffer");
  assert.equal(result.rendered, 0);
  assert.equal(result.saved, 1);
});

test("restoring a detached completion cannot overwrite another session", () => {
  const source = fs.readFileSync(path.join(widget, "index.js"), "utf8");
  const start = source.indexOf("(function reconnectIfStreaming() {");
  const recovery = source.slice(start, source.indexOf("\n})();", start) + 6);
  const run = new Function("completionState", `
    const isRemoteMode = false, section = {};
    let currentSessionId = "visible-provider", conversationHistory = [{role:"user",text:"visible request"}], saved = 0;
    const state = {jarvisSessionId:"owner",sessionId:"owner-provider",uiState:"error",buffer:"partial",
      completion:completionState({exitCode:2}),conversationHistory:[{role:"assistant",completion:{exitCode:2}}]};
    const owner = {};
    const reconnect = {getActiveStream:()=>state,claimOwnership(){},clearStreamState(){}};
    const sessionManager = {getActiveSessionId:()=>"visible",getSession:()=>owner,saveImmediate(){saved++;}};
    const tabs = {render(){}};
    ${recovery}
    return {owner,currentSessionId,conversationHistory,saved};
  `);
  const result = run(completionState);
  assert.equal(result.owner.sessionId, "owner-provider");
  assert.equal(historyCompletion(result.owner.conversationHistory).status, "failed");
  assert.equal(result.currentSessionId, "visible-provider");
  assert.equal(result.conversationHistory[0].text, "visible request");
  assert.equal(result.saved, 1);
});

test("desktop and mobile orchestrators remain syntactically valid", () => {
  const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
  for (const name of ["index.js", "mobile.js"]) new AsyncFunction("ctx", fs.readFileSync(path.join(widget, name), "utf8"));
});
