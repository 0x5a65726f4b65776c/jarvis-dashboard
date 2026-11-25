// Voice Command — Desktop process manager
// Claude CLI process lifecycle: resolve path, spawn, retry, stdin, handlers.

const { nodeFs, nodePath, config } = ctx;
const cmdCfg = config.widgets?.voiceCommand || {};
const interactiveCfg = cmdCfg.interactive || {};
const personalityCfg = cmdCfg.personality || {};
const MAX_EXECUTION_MS = 2 * 60 * 60 * 1000;
const TERMINATION_GRACE_MS = 2000;

function createProcessManager(options) {
  const { storageAdapter, stripAnsi, schedule = setTimeout, cancelTimer = clearTimeout,
    killProcessGroup = (pid, signal) => process.kill(-pid, signal) } = options;
  const executionTimeoutMs = cmdCfg.executionTimeoutMs ?? MAX_EXECUTION_MS;
  if (!Number.isSafeInteger(executionTimeoutMs) || executionTimeoutMs <= 0 || executionTimeoutMs > MAX_EXECUTION_MS) {
    throw new Error("executionTimeoutMs must be a positive integer no greater than two hours");
  }

  // ── Resolve claude binary path ──
  const claudeSearchPaths = [
    nodePath.join(require("os").homedir(), ".local", "bin", "claude"),
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",
  ];
  let claudePath = cmdCfg.terminal?.claudePath || null;
  if (!claudePath) {
    for (const p of claudeSearchPaths) {
      if (nodeFs.existsSync(p)) { claudePath = p; break; }
    }
  }

  let claudeProcess = null;

  function signalRun(run, signal) {
    if (run.closed) return;
    // Native adapters own their process registry and escalation. Their IDs are
    // not operating-system PIDs that the browser's process shim can signal.
    if (typeof run.process.killTree === "function") {
      try { run.process.killTree(signal); } catch {}
      return;
    }
    if (process.platform !== "win32" && Number.isInteger(run.process.pid) && run.process.pid > 0) {
      try { killProcessGroup(run.process.pid, signal); return; } catch {}
    }
    try { run.process.kill(signal); } catch {}
  }

  function stopRun(run) {
    if (run.closed || run.stopRequested) return;
    run.stopRequested = true;
    if (claudeProcess === run.process) claudeProcess = null;
    cancelTimer(run.deadlineTimer);
    signalRun(run, "SIGTERM");
    if (typeof run.process.killTree !== "function") {
      run.killTimer = schedule(() => signalRun(run, "SIGKILL"), TERMINATION_GRACE_MS);
      run.killTimer?.unref?.();
    }
  }

  function stopCurrentProcess() {
    if (!claudeProcess) return;
    // A restored process may belong to the prior widget instance. Only its
    // original owner knows whether it was launched into a private group.
    if (claudeProcess._jarvisCancel) claudeProcess._jarvisCancel();
    else { try { claudeProcess.kill("SIGTERM"); } catch {} }
    claudeProcess = null;
  }

  function spawnOwned(args, cwd) {
    stopCurrentProcess();
    const { spawn } = require("child_process");
    const childEnv = Object.assign({}, process.env, { FORCE_COLOR: "0" });
    delete childEnv.CLAUDECODE;
    delete childEnv.CLAUDE_CODE_ENTRYPOINT;
    delete childEnv.CLAUDE_CODE_MAX_OUTPUT_TOKENS;
    const proc = spawn(claudePath, args, {
      cwd, env: childEnv, stdio: ["pipe", "pipe", "pipe"],
      detached: process.platform !== "win32",
    });
    const run = { process: proc, closed: false, stopRequested: false };
    proc._jarvisCancel = () => stopRun(run);
    proc.on("close", () => {
      if (run.stopRequested && typeof proc.killTree !== "function" && process.platform !== "win32"
        && Number.isInteger(proc.pid) && proc.pid > 0) {
        // The leader can exit before a descendant. Finish the owned group now,
        // then discard its timer instead of signaling a recycled PID later.
        try { killProcessGroup(proc.pid, "SIGKILL"); } catch {}
      }
      run.closed = true;
      cancelTimer(run.killTimer);
      cancelTimer(run.deadlineTimer);
      if (claudeProcess === proc) claudeProcess = null;
    });
    const fail = () => stopRun(run);
    proc.on("error", fail);
    proc.stdin?.on?.("error", fail);
    run.deadlineTimer = schedule(() => {
      if (run.closed || run.stopRequested) return;
      stopRun(run);
      // Both Node and the native adapter deliver this through the existing UI
      // failure handler; a late successful result cannot clear the timed-out turn.
      proc.emit("error", new Error("Execution deadline reached; cancellation requested. Resume from recorded evidence."));
    }, executionTimeoutMs);
    run.deadlineTimer?.unref?.();
    claudeProcess = proc;
    return proc;
  }

  function buildPersonalityPrompt() {
    const template = personalityCfg.prompt;
    if (!template) return null;
    const name = personalityCfg.userName || "sir";
    const assistant = personalityCfg.assistantName || "JARVIS";
    let prompt = template.replace(/\{userName\}/g, name).replace(/\{assistantName\}/g, assistant);
    const langCfg = config.language || {};
    const supported = langCfg.supported;
    if (supported && Object.keys(supported).length > 0) {
      const tpl = personalityCfg.languageInstruction
        || "Always respond in the same language the user speaks. Supported languages: {languages}.";
      const names = Object.values(supported).map(e => e.label).filter(Boolean).join(", ")
        || Object.keys(supported).join(", ");
      prompt += "\n" + tpl.replace(/\{languages\}/g, names);
    }
    return prompt;
  }

  function buildClaudeArgs(currentSessionId) {
    const args = [];
    if (currentSessionId) args.push("--resume", currentSessionId);
    args.push("-p", "--input-format", "stream-json", "--output-format", "stream-json",
      "--replay-user-messages", "--include-partial-messages");
    const autoApprove = interactiveCfg.autoApproveTools || [];
    const alwaysAsk = interactiveCfg.alwaysAskTools || [];
    if (interactiveCfg.interactivePermissions) {
      if (autoApprove.length > 0) args.push("--allowedTools", autoApprove.join(","));
    } else {
      const allAllowed = [...new Set([...autoApprove, ...alwaysAsk])];
      if (allAllowed.length > 0) args.push("--allowedTools", allAllowed.join(","));
    }
    const model = cmdCfg.model || null;
    if (model) args.push("--model", model);
    const personality = buildPersonalityPrompt();
    if (personality) args.push("--append-system-prompt", personality);
    return args;
  }

  function spawnProcess(currentSessionId, cwd) {
    if (!claudePath) return null;
    const args = buildClaudeArgs(currentSessionId);
    const deliveryRun = { projectPath: nodePath.resolve(cwd), startedAt: new Date().toISOString() };
    claudeProcess = spawnOwned(args, cwd);
    claudeProcess._jarvisDelivery = Object.freeze(deliveryRun);
    return claudeProcess;
  }

  function spawnRetryProcess(st, cwd) {
    if (!st || !st.sessionId) {
      console.error("[JARVIS] Cannot retry — no streamState or sessionId");
      return null;
    }
    cwd = st.projectPath || cwd;
    const approvedTools = [...(st.retryAllowedTools || [])];
    console.log("[JARVIS] Starting retry. sessionId:", st.sessionId,
      "approved tools for --allowedTools:", approvedTools);

    st.pendingPermissions.forEach(p => { if (p.status === "approved") p.status = "retrying"; });
    st.resultReceived = false;
    st.resultFailed = null;
    st.exitCode = null;
    st.signal = null;
    st.completion = null;
    st.lineBuf = "";
    st.finalText = null;
    st.runtimeSessionId = null;
    st.deliveryReview = null;
    st.buffer = "";

    const args = buildClaudeArgs(st.sessionId);
    if (approvedTools.length > 0) {
      const idx = args.indexOf("--allowedTools");
      if (idx >= 0 && idx + 1 < args.length) {
        const current = args[idx + 1].split(",");
        const merged = [...new Set([...current, ...approvedTools])];
        args[idx + 1] = merged.join(",");
      } else {
        args.push("--allowedTools", approvedTools.join(","));
      }
    }

    st.projectPath = nodePath.resolve(cwd);
    st.startedAt = new Date().toISOString();
    st.submittedPrompt = "Permission granted. Please retry the file operation.";
    const newProc = spawnOwned(args, cwd);
    st.process = newProc;
    st.uiState = "streaming";

    newProc.stdin.write(JSON.stringify({
      type: "user",
      message: { role: "user", content: [{ type: "text", text: st.submittedPrompt }] },
    }) + "\n");

    return newProc;
  }

  function spawnResumeProcess(st, cwd, answerText) {
    if (!st || !st.sessionId) {
      console.error("[JARVIS] Cannot resume for AskUserQuestion — no streamState or sessionId");
      return null;
    }
    cwd = st.projectPath || cwd;
    st.resultReceived = false;
    st.resultFailed = null;
    st.exitCode = null;
    st.signal = null;
    st.completion = null;
    st.lineBuf = "";
    st.uiState = "streaming";
    st.finalText = null;
    st.runtimeSessionId = null;
    st.deliveryReview = null;
    st.buffer = "";

    const args = buildClaudeArgs(st.sessionId);
    st.projectPath = nodePath.resolve(cwd);
    st.startedAt = new Date().toISOString();
    st.submittedPrompt = answerText;
    const newProc = spawnOwned(args, cwd);
    st.process = newProc;

    newProc.stdin.write(JSON.stringify({
      type: "user",
      message: { role: "user", content: [{ type: "text", text: answerText }] },
    }) + "\n");

    return newProc;
  }

  function kill() {
    const st = typeof window !== "undefined" ? window.__jarvisStreamState : null;
    if (st?.tempPermissions?.length > 0) {
      st.tempPermissions.forEach(entry => storageAdapter.removeSettingsPermission(entry));
      st.tempPermissions = [];
    }
    stopCurrentProcess();
    if (typeof window !== "undefined") window.__jarvisStreamState = null;
  }

  function sendStdinMessage(msg) {
    if (!claudeProcess || !claudeProcess.stdin?.writable) return false;
    try {
      claudeProcess.stdin.write(JSON.stringify(msg) + "\n");
      return true;
    } catch { return false; }
  }

  function closeStdin() {
    if (claudeProcess?.stdin?.writable) claudeProcess.stdin.end();
  }

  function attachHandlers(proc, callbacks) {
    proc.stdout.on("data", (chunk) => {
      if (callbacks.onStdout) callbacks.onStdout(stripAnsi(chunk.toString("utf8")));
    });
    proc.stderr.on("data", (chunk) => {
      if (callbacks.onStderr) callbacks.onStderr(chunk.toString("utf8"));
    });
    proc.on("close", (code) => {
      if (callbacks.onClose) callbacks.onClose(code, proc);
    });
    proc.on("error", (err) => {
      if (callbacks.onError) callbacks.onError(err, proc);
    });
  }

  return {
    get isAvailable() { return !!claudePath; },
    get process() { return claudeProcess; },
    set process(p) { claudeProcess = p; },
    spawnProcess,
    spawnRetryProcess,
    spawnResumeProcess,
    kill,
    sendStdinMessage,
    closeStdin,
    attachHandlers,
    buildClaudeArgs,
    buildPersonalityPrompt,
    interactiveCfg,
    personalityCfg,
  };
}

return { createProcessManager };
