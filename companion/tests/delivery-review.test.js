const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const Pipeline = require("../modules/voice-pipeline");
const Runner = require("../modules/claude-runner");
const { verifyDelivery, BLOCKED } = require("../modules/delivery-review");
const root = path.resolve(__dirname, "../..");
const widget = path.join(root, "src/widgets/voice-command");
const streamModule = new Function("ctx", fs.readFileSync(path.join(widget, "core/stream-handler.js"), "utf8"))({});
const { completionState, createRemoteDeliveryBuffer } = streamModule;
const releaseId = "a".repeat(64);
const ok = { exitCode: 0, signal: null, resultReceived: true, resultFailed: false };
const terminal = { ...ok, finalText: "Reviewed final answer.", sessionId: "session", runtimeSessionId: "session" };

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

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function pipelineFixture(reviewDelivery) {
  const calls = [], messages = [], audio = [], events = [];
  const conn = {
    runner: { run(text, callbacks) { calls.push({ text, callbacks }); return { cancel() {} }; }, sendControlResponse() {} },
    send(raw) { const msg = JSON.parse(raw); messages.push(msg); events.push(msg.type); return true; },
    tts: { isEnabled: true, enqueue(...args) { audio.push(args); events.push("audio"); } },
    _rawConfig: {}, resetActivity() {},
  };
  const pipeline = new Pipeline(conn, { langConfig: { supported: { en: { label: "English" } } }, reviewDelivery });
  return { conn, pipeline, calls, messages, audio, events };
}

function gateFixture(t, mode = "cleared") {
  // Use the shared gate's offline fixture builder, then invoke its actual CLI.
  // Reviewer events are test fixtures; this does not claim an LLM reviewed them.
  const script = `
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("transport_fixture", root / "tests/test_agent_gate.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fixture = module.DeliveryGateTests()
fixture.setUp()
fixture.temp._finalizer.detach()
started = module.gate_module.now()
if sys.argv[2] == "missing-review":
    fixture.start()
    release = fixture.stage()
    fixture.gate.owner_review(fixture.review(release))
else:
    release = fixture.cleared()
    if sys.argv[2] != "before-stop":
        assert fixture.stop() == {}
print(json.dumps({"projectPath": str(fixture.root), "startedAt": started,
    "runtimeSessionId": fixture.gate.session, "sessionId": fixture.gate.session,
    "prompt": "Verify the requested value.", "finalText": fixture.reply,
    "releaseId": release["release_id"]}))
`;
  const fixture = JSON.parse(execFileSync("python3", ["-c", script, root, mode], { encoding: "utf8" }));
  t.after(() => fs.rmSync(fixture.projectPath, { recursive: true, force: true }));
  return { ...ok, ...fixture };
}

test("transport uses the actual two-review gate and binds session, exact reply, prompt and launch time", async t => {
  const fixture = gateFixture(t);
  const result = await verifyDelivery(fixture, fixture.prompt);
  assert.deepEqual(result, { text: fixture.finalText, releaseId: fixture.releaseId });
  for (const changes of [
    { finalText: "Unreviewed substitute" },
    { runtimeSessionId: "another-session" },
    { startedAt: new Date(Date.now() + 60000).toISOString() },
    { exitCode: 1 }, { signal: "SIGTERM" }, { resultReceived: false }, { resultFailed: true },
    { runtimeSessionId: null }, { finalText: null },
  ]) await assert.rejects(verifyDelivery({ ...fixture, ...changes }, fixture.prompt), { message: BLOCKED });
  await assert.rejects(verifyDelivery(fixture, "Different request"), { message: BLOCKED });
  fs.writeFileSync(path.join(fixture.projectPath, "evidence/receipt.json"), '{"result":"changed"}\n');
  await assert.rejects(verifyDelivery(fixture, fixture.prompt), { message: BLOCKED });
});

for (const mode of ["before-stop", "missing-review"]) {
  test("transport refuses " + mode + " even with a successful provider result", async t => {
    const fixture = gateFixture(t, mode);
    await assert.rejects(verifyDelivery(fixture, fixture.prompt), { message: BLOCKED });
  });
}

test("companion and independently packaged desktop use the identical gate adapter", () => {
  assert.equal(fs.readFileSync(path.join(root, "companion/modules/delivery-review.js"), "utf8"),
    fs.readFileSync(path.join(widget, "desktop/delivery-review.js"), "utf8"));
});

test("pipeline withholds all drafts and audio until the real gate clears the terminal result", async t => {
  const fixture = gateFixture(t);
  const s = pipelineFixture();
  s.pipeline.runClaude(fixture.prompt);
  const callbacks = s.calls[0].callbacks;
  callbacks.onDelta("Draft claims everything is done. ");
  callbacks.onDelta("[stderr] Unreviewed provider text");
  assert.deepEqual(s.messages, []);
  assert.deepEqual(s.audio, []);
  await callbacks.onEnd(fixture);
  assert.equal(s.messages[0].text, fixture.finalText);
  assert.equal(s.messages[0].releaseId, fixture.releaseId);
  assert.equal(s.messages[1].deliveryReview, "cleared");
  assert.equal(s.messages[1].taskVerified, false);
  assert.deepEqual(s.events.slice(0, 2), ["stream_delta", "stream_end"]);
  assert.ok(s.audio.length > 0);
  assert.ok(s.audio.every(item => item[2] === fixture.releaseId));
});

test("review delay preserves control events, then releases only the exact reviewed text", async () => {
  const pending = deferred();
  let reviewedPrompt;
  const s = pipelineFixture(async (result, prompt) => { reviewedPrompt = prompt; return pending.promise; });
  s.pipeline.runClaude("Request", "en");
  const callbacks = s.calls[0].callbacks;
  callbacks.onDelta("Draft");
  const ending = callbacks.onEnd(terminal);
  callbacks.onPermissionRequest("tool-request", { tool_name: "Read" });
  callbacks.onQuestionRequest("question", { prompt: "Choose" });
  assert.deepEqual(s.messages.map(msg => msg.type), ["permission_request", "question_request"]);
  assert.deepEqual(s.audio, []);
  assert.equal(reviewedPrompt, "[Language: English]\nRequest");
  pending.resolve({ text: terminal.finalText, releaseId });
  await ending;
  assert.deepEqual(s.messages.filter(msg => msg.type === "stream_delta").map(msg => msg.text), [terminal.finalText]);
});

for (const action of ["reject", "cancel", "replace"]) {
  test("pipeline never releases pending text after " + action, async () => {
    const pending = deferred();
    const s = pipelineFixture(() => pending.promise);
    s.pipeline.runClaude("first");
    s.calls[0].callbacks.onDelta("Never deliver");
    const ending = s.calls[0].callbacks.onEnd(terminal);
    if (action === "cancel") s.conn.activeRunner.cancel();
    if (action === "replace") s.pipeline.runClaude("new request");
    if (action === "reject") pending.reject(new Error("review failed"));
    else pending.resolve({ text: terminal.finalText, releaseId });
    await ending;
    assert.equal(s.messages.filter(msg => msg.type === "stream_delta").length, 0);
    assert.deepEqual(s.audio, []);
    if (action === "reject") assert.equal(s.messages.at(-1).deliveryReview, "blocked");
  });
}

test("runner records actual terminal session, final text, cwd and launch time separately from drafts", () => {
  const proc = processFake();
  const runner = new Runner({ claudePath: "/fixture/claude", projectPath: root }, { spawnProcess: () => proc });
  runner._snapshotJsonlFiles = () => new Set();
  runner.sessionId = "resumed-session";
  let result;
  const before = Date.now();
  runner.run("Prompt", { onDelta() {}, onEnd(value) { result = value; } });
  proc.stdout.emit("data", JSON.stringify({ type: "stream_event", event: { type: "content_block_delta", delta: { type: "text_delta", text: "draft" } } }) + "\n");
  proc.stdout.emit("data", JSON.stringify({ type: "result", session_id: "actual-terminal-session", subtype: "success", result: terminal.finalText }));
  proc.emit("close", 0);
  assert.equal(result.runtimeSessionId, "actual-terminal-session");
  assert.equal(result.finalText, terminal.finalText);
  assert.equal(result.projectPath, root);
  assert.ok(Date.parse(result.startedAt) >= before);
});

function localFixture(review = verifyDelivery, facts = {}) {
  const source = fs.readFileSync(path.join(widget, "index.js"), "utf8");
  const fn = source.slice(source.indexOf("function attachProcessHandlers(proc) {"), source.indexOf("\nfunction handleToolComplete"));
  const proc = processFake(), delivered = [];
  let state = { process: proc, lineBuf: "", buffer: "", fullBuffer: "", resultReceived: false, resultFailed: null,
    pendingPermissions: [], pendingInteractions: [], conversationHistory: [], uiState: "streaming", ...facts };
  state._onTextDelta = text => delivered.push(text);
  const original = state;
  const reconnect = { getActiveStream: () => state };
  const attach = new Function("reconnect", "stripAnsi", "interactiveCfg", "completionState", "verifyDelivery", fn + "\nreturn attachProcessHandlers;")
    (reconnect, x => x, {}, completionState, review);
  attach(proc);
  function output(event) { proc.stdout.emit("data", JSON.stringify(event) + "\n"); }
  return { proc, state: original, delivered, output, cancel() { state = null; }, close: () => proc.listeners("close")[0](0) };
}

test("local widget suppresses stdout/stderr and releases its terminal reply through the real gate", async t => {
  const facts = gateFixture(t);
  const s = localFixture(verifyDelivery, { ...facts, submittedPrompt: facts.prompt });
  s.output({ type: "stream_event", event: { type: "content_block_delta", delta: { type: "text_delta", text: "Unreviewed draft" } } });
  s.proc.stderr.emit("data", Buffer.from("Unreviewed stderr"));
  s.output({ type: "result", subtype: "success", session_id: facts.runtimeSessionId, result: facts.finalText });
  assert.deepEqual(s.delivered, []);
  await s.close();
  assert.deepEqual(s.delivered, [facts.finalText]);
  assert.equal(s.state.completion.deliveryReview, "cleared");
  assert.equal(s.state.conversationHistory[0].text, facts.finalText);
});

for (const cancel of [false, true]) {
  test("local widget discards " + (cancel ? "cancelled pending" : "rejected") + " review", async () => {
    const pending = deferred();
    const s = localFixture(() => pending.promise);
    s.output({ type: "result", subtype: "success", session_id: "runtime", result: "candidate" });
    const closing = s.close();
    assert.equal(s.state.uiState, "reviewing");
    if (cancel) { s.cancel(); pending.resolve({ text: "candidate", releaseId }); }
    else pending.reject(new Error("not cleared"));
    await closing;
    assert.deepEqual(s.delivered, []);
    assert.equal(s.state.buffer, "");
    if (!cancel) assert.equal(s.state.completion.deliveryReview, "blocked");
  });
}

function remoteFixture(name) {
  const source = fs.readFileSync(path.join(widget, name), "utf8");
  const start = source.lastIndexOf(name === "index.js" ? "if (isRemoteMode && networkClient) {" : "if (networkClient) {");
  const end = source.indexOf(name === "index.js" ? "// ── Process cleanup" : "// ── Cleanup", start);
  const code = source.slice(start, end);
  return new Function("createRemoteDeliveryBuffer", "completionState", `
    let remoteTurnActive = true, resetRemoteDelivery = () => {}, fullBuffer = "", currentSessionId = null, currentDetectedLang = null;
    const isRemoteMode = true, isNarrow = false, T = {}, handlers = {}, conversationHistory = [], rendered = [], audio = [];
    const remoteTtsMode = "local", mobileTtsMode = "mobile";
    const networkClient = { on(name, fn) { handlers[name] = fn; }, onStateChange(fn) { handlers.state = fn; } };
    const element = { appendChild() {}, scrollHeight: 0 };
    const el = () => element;
    const terminal = { getOutputArea: () => element, show() {}, setBadgeState() {}, addMessageCopyIcon() {}, appendCompletionLine() {}, appendErrorLine() {} };
    const markdownRenderer = { createStreamRenderer: () => ({ append(text) { rendered.push(text); }, finalize() {} }) };
    const tts = { isEnabled: true, isMuted: false, speak(text) { audio.push(text); }, enqueueServerAudio(data) { audio.push(data); }, stop() {} };
    const stripMarkdown = text => text, setUIState = () => {}, syncToManager = () => {}, updateSessionIndicator = () => {};
    const cards = {}, recorder = { cancel() {} };
    ${code}
    return { handlers, rendered, audio, conversationHistory, begin() { resetRemoteDelivery(); remoteTurnActive = true; } };
  `)(createRemoteDeliveryBuffer, completionState);
}

for (const name of ["index.js", "mobile.js"]) {
  test(name + " holds text and audio until a matching backend release", () => {
    const s = remoteFixture(name);
    s.handlers.stream_delta({ text: terminal.finalText, releaseId });
    s.handlers.tts_audio({ data: "early audio", releaseId });
    assert.deepEqual(s.rendered, []);
    assert.deepEqual(s.audio, []);
    s.handlers.stream_end({ ...ok, deliveryReview: "cleared", releaseId });
    assert.deepEqual(s.rendered, [terminal.finalText]);
    assert.equal(s.conversationHistory[0].text, terminal.finalText);
    s.handlers.tts_audio({ data: "approved audio", releaseId });
    assert.deepEqual(s.audio, [terminal.finalText, "approved audio"]);
    s.handlers.turn_cancelled();
    s.handlers.tts_audio({ data: "late audio", releaseId });
    assert.equal(s.audio.length, 2);
  });
  for (const mode of ["legacy", "different-release", "process-error", "disconnect", "cancel"]) {
    test(name + " withholds " + mode + " candidates", () => {
      const s = remoteFixture(name);
      s.handlers.stream_delta({ text: "Never deliver", releaseId: mode === "legacy" ? undefined : releaseId });
      if (mode === "disconnect") s.handlers.state("disconnected");
      else if (mode === "cancel") s.handlers.turn_cancelled();
      else s.handlers.stream_end({ ...ok, exitCode: mode === "process-error" ? 7 : 0,
        deliveryReview: "cleared", releaseId: mode === "different-release" ? "b".repeat(64) : releaseId });
      s.handlers.tts_audio({ data: "Never play", releaseId });
      assert.deepEqual(s.rendered, []);
      assert.deepEqual(s.audio, []);
      assert.ok(s.conversationHistory.every(turn => !turn.text));
    });
  }
}

test("network correlation excludes cancelled and superseded response frames", () => {
  const source = fs.readFileSync(path.join(root, "src/services/network-client.js"), "utf8");
  let socket;
  class FakeWebSocket {
    constructor() { socket = this; this.readyState = 1; this.messages = []; }
    send(raw) { this.messages.push(JSON.parse(raw)); }
    close() {}
    receive(message) { this.onmessage({ data: JSON.stringify(message) }); }
  }
  const client = new Function("ctx", "WebSocket", "setTimeout", "clearTimeout", "setInterval", "clearInterval", source)
    ({ config: { network: { token: "fixture-token", autoConnect: false } } }, FakeWebSocket, () => 0, () => {}, () => 0, () => {});
  const seen = [], errors = [];
  client.on("stream_delta", msg => seen.push(msg.text));
  client.on("error", msg => errors.push(msg));
  client.connect(); socket.onopen();
  client.sendTextCommand("first");
  const first = socket.messages.at(-1).turnId;
  client.sendCancel();
  socket.receive({ type: "stream_delta", turnId: first, text: "cancelled" });
  client.sendTextCommand("second");
  const second = socket.messages.at(-1).turnId;
  assert.notEqual(first, second);
  socket.receive({ type: "stream_delta", turnId: first, text: "stale" });
  socket.receive({ type: "stream_delta", turnId: second, text: "current" });
  socket.receive({ type: "stream_delta", text: "legacy" });
  assert.deepEqual(seen, ["current"]);
  assert.equal(errors[0].stage, "review");
  client.cleanup();
});

test("cancelled speech cannot emit late audio under the next reply's release", () => {
  const source = fs.readFileSync(path.join(root, "companion/modules/tts/say-engine.js"), "utf8");
  const first = processFake(), second = processFake(), queue = [first, second], mod = { exports: {} };
  new Function("require", "module", source)(name => name === "child_process"
    ? { spawn: () => queue.shift() } : require("../modules/tts/base-engine"), mod);
  const engine = new mod.exports({});
  const audio = [];
  engine.setCallbacks(chunk => audio.push(chunk), () => {});
  engine.speak("first", "en", 0);
  engine.stop();
  engine.speak("second", "en", 1);
  first.stdout.emit("data", Buffer.from("stale"));
  first.emit("close", 0);
  assert.equal(engine._activeProcess, second);
  second.stdout.emit("data", Buffer.from("current"));
  assert.deepEqual(audio, [Buffer.from("current").toString("base64")]);
});
