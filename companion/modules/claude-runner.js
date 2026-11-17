// JARVIS Companion — Claude CLI Runner
// Spawns claude CLI, streams output via stream-json format, manages session continuity.
// Mirrors the logic from src/widgets/jarvis-voice-command.js

const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { expandPath, stripAnsi } = require("./utils");

class ClaudeRunner {
  constructor(config, { spawnProcess = spawn } = {}) {
    this._spawn = spawnProcess;
    this._activeRun = null;
    this._claudePath = config.claudePath || null;
    this._projectPath = config.projectPath || null;
    this._model = config.model || null;
    this._personality = config.personality || null;
    this._allowedTools = config.allowedTools || [];
    this._interactivePermissions = config.interactivePermissions || false;
    this._supportedLangs = config.supportedLangs || {};
    this._activeProcess = null;
    this._sessionId = null;

    // Resolve claude binary
    if (!this._claudePath) {
      const searchPaths = [
        path.join(os.homedir(), ".local", "bin", "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
      ];
      for (const p of searchPaths) {
        if (fs.existsSync(p)) { this._claudePath = p; break; }
      }
    }
  }

  get sessionId() { return this._sessionId; }
  set sessionId(id) { this._sessionId = id; }

  get isAvailable() { return !!this._claudePath; }

  setProjectPath(projectPath) {
    if (projectPath) this._projectPath = projectPath;
  }

  _getProjectSessionDir() {
    const cwd = expandPath(this._projectPath, os.homedir());
    const escaped = cwd.replace(/^\//, "").replace(/\//g, "-");
    return path.join(os.homedir(), ".claude", "projects", escaped);
  }

  _snapshotJsonlFiles() {
    try {
      const dir = this._getProjectSessionDir();
      if (!fs.existsSync(dir)) return new Set();
      return new Set(fs.readdirSync(dir).filter((f) => f.endsWith(".jsonl")));
    } catch {
      return new Set();
    }
  }

  _detectNewSession(beforeSet) {
    try {
      const dir = this._getProjectSessionDir();
      if (!fs.existsSync(dir)) return null;
      const afterFiles = fs.readdirSync(dir).filter((f) => f.endsWith(".jsonl"));
      const newFiles = afterFiles.filter((f) => !beforeSet.has(f));
      if (newFiles.length === 1) return newFiles[0].replace(".jsonl", "");
      if (newFiles.length > 1) {
        let best = null;
        let bestMtime = 0;
        for (const f of newFiles) {
          try {
            const mt = fs.statSync(path.join(dir, f)).mtimeMs;
            if (mt > bestMtime) { bestMtime = mt; best = f; }
          } catch {}
        }
        return best ? best.replace(".jsonl", "") : null;
      }
      return null;
    } catch {
      return null;
    }
  }

  _buildPersonalityPrompt() {
    if (!this._personality?.prompt) return null;
    const template = this._personality.prompt;
    const name = this._personality.userName || "sir";
    const assistant = this._personality.assistantName || "JARVIS";
    let prompt = template.replace(/\{userName\}/g, name).replace(/\{assistantName\}/g, assistant);

    // Inject language instruction from supported languages
    const supported = this._supportedLangs;
    if (supported && Object.keys(supported).length > 0) {
      const tpl = this._personality.languageInstruction
        || "Always respond in the same language the user speaks. Supported languages: {languages}.";
      const names = Object.values(supported).map(e => e.label).filter(Boolean).join(", ")
        || Object.keys(supported).join(", ");
      prompt += "\n" + tpl.replace(/\{languages\}/g, names);
    }

    return prompt;
  }

  _buildArgs() {
    const args = [];
    if (this._sessionId) args.push("--resume", this._sessionId);
    args.push(
      "-p",
      "--input-format", "stream-json",
      "--output-format", "stream-json",
      "--replay-user-messages",
      "--include-partial-messages"
    );

    // Pre-approve tools via --allowedTools
    // When interactivePermissions is true, only autoApproveTools are passed (alwaysAskTools trigger control_request)
    if (this._allowedTools.length > 0) {
      args.push("--allowedTools", this._allowedTools.join(","));
    }

    if (this._model) args.push("--model", this._model);
    const personality = this._buildPersonalityPrompt();
    if (personality) args.push("--append-system-prompt", personality);
    return args;
  }

  /**
   * Run claude with text input, streaming output.
   * Keeps stdin open for bidirectional communication (permissions & questions).
   * @param {string} text - User's message
   * @param {object} callbacks - { onDelta, onEnd, onError, onPermissionRequest, onQuestionRequest }
   * @returns {{ cancel: function }}
   */
  run(text, { onDelta, onEnd, onError, onPermissionRequest, onQuestionRequest }) {
    if (!this._claudePath) {
      onError(new Error("claude CLI not found"));
      return { cancel: () => {} };
    }

    const cwd = expandPath(this._projectPath, os.homedir());
    const args = this._buildArgs();
    const snapshot = this._snapshotJsonlFiles();

    // Clean environment (mirrors voice-command widget)
    const childEnv = { ...process.env, FORCE_COLOR: "0" };
    delete childEnv.CLAUDECODE;
    delete childEnv.CLAUDE_CODE_ENTRYPOINT;
    delete childEnv.CLAUDE_CODE_MAX_OUTPUT_TOKENS;

    const run = { settled: false, resultReceived: false, resultFailed: null };
    this._activeRun = run;
    let proc;
    try {
      proc = this._spawn(this._claudePath, args, {
        cwd, env: childEnv, stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (err) {
      run.settled = true;
      this._activeProcess = null;
      onError(err);
      return { cancel: () => {} };
    }
    this._activeProcess = proc;
    const current = () => this._activeRun === run && !run.settled;
    const handleMessage = (msg) => {
      if (msg.type === "result") {
        run.resultReceived = true;
        if (msg.is_error === true || (typeof msg.subtype === "string" && msg.subtype !== "success")) {
          run.resultFailed = true;
        } else if (run.resultFailed !== true && (msg.is_error === false || msg.subtype === "success")) {
          run.resultFailed = false;
        }
      }
      this._handleStreamMessage(msg, onDelta, onPermissionRequest, onQuestionRequest);
    };

    let textBuffer = "";

    proc.stdout.on("data", (chunk) => {
      if (!current()) return;
      textBuffer += stripAnsi(chunk.toString("utf8"));

      // Parse newline-delimited JSON (stream-json format)
      const lines = textBuffer.split("\n");
      textBuffer = lines.pop() || ""; // Keep incomplete line

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const msg = JSON.parse(trimmed);
          handleMessage(msg);
        } catch {
          // Non-JSON output — send as raw text
          if (trimmed) onDelta(trimmed);
        }
      }
    });

    proc.stderr.on("data", (chunk) => {
      if (!current()) return;
      const text = stripAnsi(chunk.toString("utf8")).trim();
      if (text) onDelta(`[stderr] ${text}`);
    });

    proc.on("close", (exitCode, signal) => {
      if (!current()) return;

      // Process remaining buffer
      if (textBuffer.trim()) {
        try {
          const msg = JSON.parse(textBuffer.trim());
          handleMessage(msg);
        } catch {
          if (textBuffer.trim()) onDelta(textBuffer.trim());
        }
      }

      // Detect session ID
      const newId = this._sessionId ?? this._detectNewSession(snapshot);
      if (newId) this._sessionId = newId;

      run.settled = true;
      this._activeProcess = null;
      onEnd({ exitCode: exitCode ?? null, signal: signal || null, sessionId: this._sessionId,
        resultReceived: run.resultReceived, resultFailed: run.resultFailed });
    });

    const fail = (err) => {
      if (!current()) return;
      run.settled = true;
      this._activeProcess = null;
      try { proc.kill("SIGTERM"); } catch {}
      onError(new Error(`claude error: ${err.message}`));
    };
    proc.on("error", fail);
    proc.stdin.on("error", fail);
    // Install error handlers before writing; a broken pipe is a failed attempt.
    try {
      proc.stdin.write(JSON.stringify({
        type: "user",
        message: { role: "user", content: [{ type: "text", text }] },
      }) + "\n");
    } catch (err) { fail(err); }

    return {
      cancel: () => { if (current()) this.cancel(); },
    };
  }

  _handleStreamMessage(msg, onDelta, onPermissionRequest, onQuestionRequest) {
    // Extract session_id from any message that has it
    if (msg.session_id && !this._sessionId) {
      this._sessionId = msg.session_id;
    }

    // Handle interactive control requests (permissions & questions)
    if (msg.type === "control_request") {
      console.log("[ClaudeRunner] control_request:", msg.request?.subtype, msg.request_id);
      const req = msg.request || {};
      if (req.subtype === "can_use_tool" && onPermissionRequest) {
        onPermissionRequest(msg.request_id, req);
      } else if (req.subtype === "elicitation" && onQuestionRequest) {
        onQuestionRequest(msg.request_id, req);
      }
      return;
    }

    // Handle stream-json format from Claude CLI
    // Events are wrapped: { type: "stream_event", event: { type: "content_block_delta", ... } }
    if (msg.type === "stream_event" && msg.event) {
      const evt = msg.event;
      if (evt.type === "content_block_delta" && evt.delta?.type === "text_delta") {
        onDelta(evt.delta.text);
      }
    } else if (msg.type === "content_block_delta" && msg.delta?.type === "text_delta") {
      // Direct format (without stream_event wrapper)
      onDelta(msg.delta.text);
    } else if (msg.type === "assistant" && msg.message?.content) {
      // Full message format — skip, we already got deltas
    } else if (msg.type === "result") {
      // Close stdin so the process exits cleanly
      if (this._activeProcess?.stdin?.writable) {
        this._activeProcess.stdin.end();
      }
    }
  }

  /**
   * Send a control_response to Claude's stdin (permission decision or question answer).
   * @param {object} response - The control_response payload
   * @returns {boolean} - true if written, false if no active process
   */
  sendControlResponse(response) {
    if (!this._activeProcess || !this._activeProcess.stdin.writable) return false;
    try {
      this._activeProcess.stdin.write(JSON.stringify({
        type: "control_response",
        response,
      }) + "\n");
      return true;
    } catch {
      return false;
    }
  }

  cancel() {
    if (this._activeProcess) {
      try { this._activeProcess.kill("SIGTERM"); } catch {}
      this._activeProcess = null;
    }
  }

  clearSession() {
    this._sessionId = null;
  }
}

module.exports = ClaudeRunner;
