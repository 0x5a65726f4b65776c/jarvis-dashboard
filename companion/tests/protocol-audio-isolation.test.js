const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const protocol = require("../modules/protocol");
const Transcriber = require("../modules/transcriber");
const Pipeline = require("../modules/voice-pipeline");
const { AudioSession } = require("../modules/audio");
const modules = path.resolve(__dirname, "../modules");
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

function child() {
  const proc = new EventEmitter();
  proc.stdout = new EventEmitter(); proc.stderr = new EventEmitter();
  proc.signals = []; proc.kill = signal => proc.signals.push(signal);
  return proc;
}
function load(name, overrides, globals = {}) {
  const mod = { exports: {} };
  const req = key => key in overrides ? overrides[key] : key.startsWith(".") ? require(path.resolve(modules, key)) : require(key);
  new Function("require", "module", ...Object.keys(globals), fs.readFileSync(path.join(modules, name), "utf8"))(req, mod, ...Object.values(globals));
  return mod.exports;
}

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "jarvis-voice-isolation-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  let counter = 0;
  return () => {
    const file = path.join(dir, `${++counter}.wav`); fs.writeFileSync(file, "audio fixture");
    return { file, convertToWav: async () => file, cleanup() { try { fs.unlinkSync(file); } catch {} } };
  };
}

function serverHarness() {
  const servers = [], connections = [], pipelines = [], transcribers = [], processes = [];
  class Server extends EventEmitter { constructor() { super(); servers.push(this); this.clients = new Set(); } }
  class Auth {
    getClientIP() { return "authorized-fixture"; }
    registerConnection() {}
    setupIdleTimeout() { return () => {}; }
    verifyClient() { return { allowed: true }; }
  }
  class Runner {
    constructor() { this.requests = []; this.cancellations = 0; }
    run(text) {
      if (text === "throw synchronously") throw new Error("fixture handler failure");
      this.requests.push(text); return { cancel: () => { this.cancellations++; } };
    }
    clearSession() { this.sessionId = null; }
    setProjectPath(value) { this.projectPath = value; }
  }
  const BaseConnection = load("connection-handler.js", { ws: { OPEN: 1 }, "./claude-runner": Runner });
  class Connection extends BaseConnection { constructor(...args) { super(...args); connections.push(this); } }
  class VoicePipeline extends Pipeline { constructor(...args) { super(...args); pipelines.push(this); } }
  class LocalTranscriber extends Transcriber {
    constructor(config) {
      super({ ...config, whisperPath: process.execPath, whisperModel: process.execPath }, {
        spawnProcess: () => { const proc = child(); processes.push(proc); return proc; },
      });
      transcribers.push(this);
    }
  }
  const http = { createServer: () => new EventEmitter() };
  const { createServer } = load("server-factory.js", {
    http, https: http, ws: { Server }, "./auth": Auth, "./transcriber": LocalTranscriber,
    "./connection-handler": Connection, "./voice-pipeline": VoicePipeline,
  }, { process: { on() {}, exit() { assert.fail("message handling must not exit the server"); } } });
  createServer({ raw: {}, networkConfig: {}, companionConfig: {}, voiceConfig: {}, ttsConfig: {}, langConfig: {}, tlsOptions: {} });
  return { connections, pipelines, transcribers, processes, connect() {
    const ws = new EventEmitter(); ws.readyState = 1; ws.sent = [];
    ws.send = msg => ws.sent.push(JSON.parse(msg));
    ws.command = msg => ws.emit("message", Buffer.from(typeof msg === "string" ? msg : JSON.stringify(msg)), false);
    servers[0].emit("connection", ws, {});
    return ws;
  } };
}

test("inbound schemas reject malformed values and preserve valid client message shapes", () => {
  const invalid = [null, [], true, "text", {}, { type: {} },
    { type: "text_command", text: {} }, { type: "text_command", text: 1 },
    { type: "text_command", text: "ok", projectPath: {} },
    { type: "audio_start", format: "../../outside" }, { type: "audio_start", sampleRate: "16000" },
    { type: "permission_response", requestId: {}, behavior: "allow" },
    { type: "permission_response", requestId: "id", behavior: "arbitrary" },
    { type: "permission_response", requestId: "id", updatedPermissions: {} },
    { type: "question_response", requestId: "id", answer: 12 }, { type: "tts_toggle", muted: "false" },
  ];
  invalid.forEach(value => assert.equal(protocol.parse(JSON.stringify(value)), null, JSON.stringify(value)));
  assert.equal(protocol.parse("{broken"), null);
  for (const value of [
    { type: "text_command", text: " valid request ", sessionId: "s", projectPath: "/work", turnId: "t" },
    { type: "text_command", text: "legacy client", sessionId: null },
    { type: "audio_start", format: "mp4", sampleRate: 16000, turnId: "t" },
    { type: "audio_start" }, { type: "audio_end" }, { type: "cancel", turnId: null },
    { type: "new_session" }, { type: "ping" }, { type: "tts_toggle", muted: false },
    { type: "permission_response", requestId: "id", behavior: "allow", updatedPermissions: [{ type: "addRules", rules: [] }] },
    { type: "permission_response", requestId: "id" },
    { type: "question_response", requestId: "id", answer: { action: "accept", content: { answer: "yes" } } },
  ]) assert.deepEqual(protocol.parse(JSON.stringify(value)), value);
});

test("actual server routing contains malformed input and synchronous/asynchronous failures, then accepts valid work", async () => {
  const h = serverHarness(), ws = h.connect(), other = h.connect();
  other.command({ type: "text_command", text: "other active turn", turnId: "other" });
  ws.command({ type: "text_command", text: { unexpected: "object" } });
  ws.command("null"); ws.command("{broken");
  await flush();
  assert.equal(ws.sent.filter(x => x.type === "error").length, 3);
  ws.command({ type: "text_command", text: "throw synchronously" });
  await flush();
  h.pipelines[0].processAudioEnd = async () => { throw new Error("fixture asynchronous failure"); };
  ws.command({ type: "audio_end" });
  await flush();
  assert.equal(ws.sent.filter(x => x.message === "Message could not be processed").length, 2);
  ws.command({ type: "text_command", text: " valid counterpart " });
  await flush();
  assert.deepEqual(h.connections[0].runner.requests, ["valid counterpart"]);
  assert.deepEqual(h.connections[1].runner.requests, ["other active turn"]);
  assert.equal(h.connections[1].runner.cancellations, 0);
  h.connections.forEach(conn => conn.cleanup());
});

test("two connections transcribe concurrently; disconnecting A only cancels A and B still completes", async t => {
  const audio = fixture(t), h = serverHarness(), a = h.connect(), b = h.connect();
  h.connections[0].audioSession = audio(); h.connections[0].turnId = "A";
  h.connections[1].audioSession = audio(); h.connections[1].turnId = "B";
  a.command({ type: "audio_end" }); b.command({ type: "audio_end" });
  await flush();
  assert.equal(h.processes.length, 2);
  a.emit("close", 1000, "fixture disconnect");
  assert.deepEqual(h.processes[0].signals, ["SIGTERM"]);
  assert.deepEqual(h.processes[1].signals, []);
  h.processes[0].emit("close", 0);
  h.processes[1].stdout.emit("data", "connection B transcription"); h.processes[1].emit("close", 0);
  await flush();
  assert.deepEqual(h.connections[0].runner.requests, []);
  assert.deepEqual(h.connections[1].runner.requests, ["connection B transcription"]);
  assert.ok(b.sent.some(x => x.type === "transcription" && x.turnId === "B"));
  h.connections.forEach(conn => conn.cleanup());
});

test("a superseded transcription cannot clear the next turn's cancellation handle", async t => {
  const audio = fixture(t), processes = [];
  const transcriber = new Transcriber({ whisperPath: process.execPath, whisperModel: process.execPath }, {
    spawnProcess: () => { const proc = child(); processes.push(proc); return proc; },
  });
  const first = transcriber.transcribe(audio().file);
  const firstRejected = assert.rejects(first, /cancelled/);
  const second = transcriber.transcribe(audio().file);
  const secondRejected = assert.rejects(second, /cancelled/);
  processes[0].emit("close", 0);
  transcriber.cancel();
  assert.deepEqual(processes[0].signals, ["SIGTERM"]);
  assert.deepEqual(processes[1].signals, ["SIGTERM"]);
  processes[1].emit("close", 0);
  await Promise.all([firstRejected, secondRejected]);
});

test("cancellation during conversion fences a legacy null-ID turn before transcription can launch", async t => {
  const audio = fixture(t), h = serverHarness(), ws = h.connect(), conn = h.connections[0];
  const old = audio(), conversion = deferred(); old.convertToWav = () => conversion.promise;
  conn.audioSession = old;
  ws.command({ type: "audio_end" });
  const oldJob = conn.activeAudio;
  ws.command({ type: "cancel" });
  conn.audioSession = audio();
  ws.command({ type: "audio_end" });
  await flush();
  assert.equal(h.processes.length, 1, "only the replacement may launch transcription");
  oldJob.cancel(); conversion.resolve(old.file); await flush();
  assert.equal(h.processes.length, 1);
  assert.deepEqual(h.processes[0].signals, []);
  h.processes[0].stdout.emit("data", "replacement result"); h.processes[0].emit("close", 0);
  await flush();
  assert.deepEqual(conn.runner.requests, ["replacement result"]);
  conn.cleanup();
});

test("audio conversion cancellation rejects promptly, terminates its own process and removes late output", async () => {
  const proc = child(), session = new AudioSession("mp4", "/unused", 1024, { spawnProcess: () => proc });
  session.appendChunk(Buffer.from("fixture"));
  const converted = session.convertToWav(), rejected = assert.rejects(converted, /cancelled/);
  session.cleanup();
  assert.deepEqual(proc.signals, ["SIGTERM"]);
  await rejected;
  fs.writeFileSync(session._outputPath, "late cancelled output");
  proc.emit("close", 0);
  assert.equal(fs.existsSync(session._inputPath), false);
  assert.equal(fs.existsSync(session._outputPath), false);
});
