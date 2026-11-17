// JARVIS Voice Command Widget — Mobile Orchestrator
// Loads shared sub-modules and wires them for mobile (WebSocket) mode.
// Returns: HTMLElement

const { el, T, config, isNarrow, networkClient, markdownRenderer } = ctx;
const cmdCfg = config.widgets?.voiceCommand || {};
if (cmdCfg.enabled === false) return el("div", {});

// Guard against duplicate renders
if (ctx._mobileVoiceActive) return el("div", {});
ctx._mobileVoiceActive = true;
if (ctx.cleanups) ctx.cleanups.push(() => { ctx._mobileVoiceActive = false; });

// ── Sub-module loader ──
async function loadSub(rel) {
  const base = ctx._srcDir + "widgets/voice-command/";
  const code = await ctx._adapter.readFile(base + rel);
  return new Function("ctx", code)(ctx);
}

// ── Load shared modules ──
const [
  { stripAnsi, stripMarkdown },
  { createStateMachine },
  { createStorageAdapter },
  { createArcReactor },
  { createTextInput },
  { createConnectionBar },
  { createTerminalPanel },
  { createRecorder },
  { createTTSAdapter },
  { createCardRenderer },
  { createReconnectManager },
  { createSessionTabs },
  { createProjectSelector },
  { completionState, historyCompletion },
] = await Promise.all([
  loadSub("core/utilities.js"),
  loadSub("core/state-machine.js"),
  loadSub("adapters/storage-adapter.js"),
  loadSub("core/arc-reactor.js"),
  loadSub("core/text-input.js"),
  loadSub("core/connection-bar.js"),
  loadSub("core/terminal-panel.js"),
  loadSub("adapters/recorder-adapter.js"),
  loadSub("adapters/tts-adapter.js"),
  loadSub("core/interaction-cards.js"),
  loadSub("core/reconnect-manager.js"),
  loadSub("core/session-tabs.js"),
  loadSub("core/project-selector.js"),
  loadSub("core/stream-handler.js"),
]);

// ── Shared state ──
let fullBuffer = "";
let currentSessionId = null;
let currentDetectedLang = null;
let conversationHistory = [];
let remoteTurnActive = false;
let activeJarvisSessionId = null;

// ── Session manager ref ──
const sessionManager = ctx.sessionManager;

function getActiveProjectPath() {
  const session = sessionManager.getActiveSession();
  if (!session) return undefined;
  const proj = sessionManager.getProject(session.projectIndex);
  return proj?.path || undefined;
}

// ── Create adapters ──
const storage = createStorageAdapter();
const reconnect = createReconnectManager({ isDesktop: false });

const recorder = createRecorder({
  mode: "remote",
  voiceService: null,
  networkClient,
  getCurrentSessionId: () => currentSessionId,
  getProjectPath: getActiveProjectPath,
});

const mobileTtsMode = config.network?.mobileTts || "mobile";
const tts = createTTSAdapter({
  mode: "mobile",
  ttsService: null,
  ttsMode: mobileTtsMode,
});

// ── Restore persisted session ──
const restored = storage.loadSession();
activeJarvisSessionId = restored.activeJarvisSessionId;
currentSessionId = restored.currentSessionId;
conversationHistory = restored.conversationHistory || [];
fullBuffer = restored.fullBuffer || "";

function syncFromManager() {
  const state = {};
  storage.syncFromManager(state);
  activeJarvisSessionId = state.activeJarvisSessionId;
  currentSessionId = state.currentSessionId;
  conversationHistory = state.conversationHistory || [];
  fullBuffer = state.fullBuffer || "";
}

function syncToManager() {
  storage.syncToManager({ activeJarvisSessionId, currentSessionId, conversationHistory, fullBuffer });
}

// ── Create UI components ──
const available = true;
const arcReactor = createArcReactor({ available });
const section = arcReactor.el.section;
const textInput = createTextInput();

const terminal = createTerminalPanel({
  onClose: () => {
    if (uiState === "streaming" || uiState === "transcribing") networkClient?.sendCancel();
    tts.stop();
    terminal.hide(!!currentSessionId);
    if (uiState !== "idle") setUIState("idle");
  },
  getFullText: () => fullBuffer,
  onMuteToggle: () => {
    const muted = tts.toggleMute();
    terminal.setMuteState(muted);
    storage.writeTtsPrefs({ muted });
  },
});

// TTS mute restore
if (tts.isEnabled) {
  const ttsPrefs = storage.readTtsPrefs();
  if (ttsPrefs.muted) { tts.mute(); terminal.setMuteState(true); }
  terminal.setMuteVisible(true);
}

function sendControlResponse(requestId, response) {
  if (networkClient) {
    if (response.subtype === "success" && response.response?.behavior) {
      networkClient.sendPermissionResponse(requestId, response.response.behavior, response.updated_permissions);
    } else if (response.subtype === "elicitation_complete") {
      networkClient.sendQuestionResponse(requestId, response.response);
    }
  }
}

const cards = createCardRenderer({
  sendControlResponse,
  onHistoryPush: (entry) => { conversationHistory.push(entry); syncToManager(); },
  syncToManager,
  ttsService: tts,
  showStatusLabels: terminal.showStatusLabels,
});

// ── Create project selector and session tabs ──
const projSelector = createProjectSelector({
  onSelect: (idx) => {
    if (guardRemoteTurn()) return;
    const current = sessionManager.getActiveSession();
    if (current) { current.projectIndex = idx; sessionManager.saveImmediate(); }
    updateProjectTag();
    projSelector.update();
  },
  isDisabled: () => remoteTurnActive || uiState === "streaming" || uiState === "recording" || uiState === "transcribing",
});

const tabs = createSessionTabs({
  onSwitch: (id) => switchToSession(id),
  onClose: (id) => closeSession(id),
  onCreate: (idx) => createNewSession(idx),
});

// ── Assemble DOM ──
section.appendChild(projSelector.el.selector);
section.appendChild(textInput.el.row);
section.appendChild(terminal.el.panel);
terminal.el.panel.appendChild(tabs.el.tabBar);

// Decorative line
section.appendChild(el("div", {
  width: isNarrow ? "60%" : "30%", height: "1px",
  background: `linear-gradient(90deg, transparent, ${T.accent}44, transparent)`,
  marginTop: isNarrow ? "16px" : "20px",
}));

// ── UI state ──
let uiState = "idle";

function setUIState(newState) {
  uiState = newState;
  const hasHistory = currentSessionId || conversationHistory.length > 0;
  arcReactor.updateVisualState(newState, hasHistory);
  terminal.setBadgeState(
    newState === "streaming" ? "running" : newState === "done" ? "success" : newState === "error" ? "error" : "idle"
  );
  const inputBusy = (newState === "recording" || newState === "transcribing" || newState === "launching");
  textInput.setDisabled(inputBusy || remoteTurnActive);
  const selectorBusy = remoteTurnActive || newState === "streaming" || newState === "recording" || newState === "transcribing";
  projSelector.setEnabled(!selectorBusy);
  updateSessionIndicator();
}

// ── Restore persisted session UI ──
const hasSessions = sessionManager.getAllSessions().length > 0;
if (conversationHistory.length > 0) {
  replayTerminalForActiveSession();
  setUIState(historyCompletion(conversationHistory).uiState);
} else if (hasSessions) {
  // Show terminal even if active session has no messages (user can switch tabs)
  terminal.show();
  updateSessionIndicator();
}

function replayTerminalForActiveSession() {
  const outputArea = terminal.getOutputArea();
  outputArea.innerHTML = "";
  if (conversationHistory.length === 0) return;

  let _replayThinkingShown = false;
  for (let i = 0; i < conversationHistory.length; i++) {
    const turn = conversationHistory[i];
    if (turn.role === "user") {
      _replayThinkingShown = false;
      if (i > 0) terminal.appendTurnSeparator();
      const echoLine = terminal.appendEchoLine(turn.text, false, null);
      terminal.addMessageCopyIcon(echoLine, () => turn.text);
    } else if (turn.role === "tool") {
      if (!terminal.showToolUseLabels) continue;
      const skipTools = { Skill: 1, Agent: 1, WebSearch: 1, WebFetch: 1 };
      if (skipTools[turn.text]) continue;
      terminal.appendToolUseLabel(turn.text);
    } else if (turn.role === "permission") {
      cards.renderCompletedInteractionCard({
        type: "permission", tool: turn.tool, input: turn.input,
        decision: turn.decision, requestId: turn.requestId,
      }, outputArea, outputArea);
    } else if (turn.role === "question") {
      cards.renderCompletedInteractionCard({
        type: "question", message: turn.message, options: turn.options,
        answer: turn.answer, requestId: turn.requestId,
      }, outputArea, outputArea);
    } else if (turn.role === "status") {
      if (turn.type === "thinking") {
        if (!_replayThinkingShown) { _replayThinkingShown = true; cards.renderStatusLabel(turn.type, turn.label, outputArea, outputArea, { replay: true }); }
        continue;
      }
      cards.renderStatusLabel(turn.type, turn.label, outputArea, outputArea, { replay: true });
    } else if (turn.role === "assistant") {
      const assistantDiv = el("div", { color: T.text, position: "relative" });
      assistantDiv.appendChild(markdownRenderer.renderMarkdown(turn.text));
      const txt = turn.text;
      terminal.addMessageCopyIcon(assistantDiv, () => txt);
      outputArea.appendChild(assistantDiv);
      terminal.appendCompletionLine(completionState(turn.completion));
    }
  }
  if (conversationHistory.length > 0) {
    terminal.show();
    terminal.setBadgeState(historyCompletion(conversationHistory).badgeState);
  }
}

// ── Session management ──
function guardRemoteTurn() {
  if (!remoteTurnActive) return false;
  new Notice("Wait for the current response to finish or stop before changing sessions or starting another request.", 3500);
  return true;
}

function switchToSession(id) {
  if (guardRemoteTurn()) return;
  syncToManager();
  sessionManager.setActiveSession(id);
  syncFromManager();
  replayTerminalForActiveSession();
  updateSessionIndicator();
  if (conversationHistory.length > 0) {
    setUIState(historyCompletion(conversationHistory).uiState);
  } else {
    setUIState("idle");
  }
}

function createNewSession(projectIndex) {
  if (guardRemoteTurn()) return;
  syncToManager();
  sessionManager.createSession(projectIndex);
  syncFromManager();
  terminal.getOutputArea().innerHTML = "";
  // Keep terminal visible so user can switch between sessions via tabs
  if (terminal.isVisible()) {
    // just clear output, don't hide
  } else {
    terminal.show();
  }
  updateSessionIndicator();
  setUIState("idle");
}

function closeSession(id) {
  if (guardRemoteTurn()) return;
  if (id === activeJarvisSessionId && (uiState === "streaming" || uiState === "transcribing")) {
    networkClient?.sendCancel();
    tts.stop();
  }
  sessionManager.removeSession(id);
  if (sessionManager.getAllSessions().length === 0) {
    const defaultIdx = config.projects?.defaultProjectIndex || 0;
    sessionManager.createSession(defaultIdx);
  }
  syncFromManager();
  replayTerminalForActiveSession();
  updateSessionIndicator();
  if (conversationHistory.length > 0) {
    setUIState(historyCompletion(conversationHistory).uiState);
  } else {
    terminal.getOutputArea().innerHTML = "";
    terminal.hide(false);
    setUIState("idle");
  }
}

function updateProjectTag() {
  const session = sessionManager.getActiveSession();
  if (session) {
    const color = session.sessionColor || session.projectColor || sessionManager.getProjectColor(session.projectIndex);
    const icon = session.projectIcon || sessionManager.getProjectIcon(session.projectIndex);
    const label = session.customName || session.projectLabel;
    terminal.setProjectTag(icon, label, color);
  }
}

function updateSessionIndicator() {
  projSelector.update();
  updateProjectTag();
  tabs.render();
}

// ── Recording ──
function beginRecording() {
  if (guardRemoteTurn()) return;
  remoteTurnActive = true;
  tts.stop();
  recorder.start().then(() => {
    if (terminal.getOutputArea().textContent.trim()) terminal.show();
    setUIState("recording");
    arcReactor.startTimer();
  }).catch(err => {
    remoteTurnActive = false;
    new Notice("Microphone error: " + err.message, 5000);
    setUIState("error");
  });
}

function finishRecording() {
  arcReactor.stopTimer();
  recorder.stop();
  if (conversationHistory.length > 0) terminal.appendTurnSeparator();
  setUIState("transcribing");
}

// ── Pointer events (tap toggle) ──
// ── Tap and long-press (hold-to-record) support ──
let _longPressTimer = null;
let _isLongPress = false;
let _touchStartTime = 0;

arcReactor.el.btnContainer.addEventListener("touchstart", (e) => {
  _touchStartTime = Date.now();
  _isLongPress = false;
  if (!networkClient?.isConnected) return;
  if (uiState === "idle" || uiState === "done" || uiState === "error") {
    _longPressTimer = setTimeout(() => {
      _isLongPress = true;
      tts.resumeAudioContext();
      beginRecording();
    }, 400);
  }
}, { passive: true });

arcReactor.el.btnContainer.addEventListener("touchend", (e) => {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
  if (_isLongPress) {
    // Release after long press → send recording
    _isLongPress = false;
    if (uiState === "recording") finishRecording();
    return;
  }
  _isLongPress = false;
  // Short tap — ignore if handled by click
}, { passive: true });

arcReactor.el.btnContainer.addEventListener("touchcancel", () => {
  if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
  _isLongPress = false;
}, { passive: true });

arcReactor.el.btnContainer.addEventListener("click", async () => {
  // Skip click if long press was handled
  if (_isLongPress) return;
  if (!networkClient?.isConnected) { networkClient?.connect(); return; }
  if (uiState === "idle" || uiState === "done" || uiState === "error") {
    beginRecording();
  } else if (uiState === "recording") {
    finishRecording();
  } else if (uiState === "streaming" || uiState === "transcribing") {
    recorder.cancel();
    tts.stop();
    networkClient.sendCancel();
    terminal.appendCancelLine();
    setUIState(historyCompletion(conversationHistory).uiState);
  }
  tts.resumeAudioContext();
});

// ── Text input handler ──
textInput.onSend((text) => {
  if (!text) return;
  if (guardRemoteTurn()) return;
  if (uiState === "streaming") {
    networkClient?.sendCancel();
    tts.stop();
    terminal.appendCancelLine();
  } else if (uiState === "transcribing" || uiState === "launching" || uiState === "recording") {
    return;
  }
  textInput.clear();
  if (!networkClient?.isConnected) return;
  terminal.show();
  if (conversationHistory.length > 0) terminal.appendTurnSeparator();
  terminal.appendEchoLine(text, false, null);
  conversationHistory.push({ role: "user", text, timestamp: Date.now() });
  syncToManager();
  remoteTurnActive = true;
  setUIState("streaming");
  terminal.setBadgeState("running");
  networkClient.sendTextCommand(text, currentSessionId, getActiveProjectPath());
});

// ═══════════════════════════════════════════
// ── Server message handlers ──
// ═══════════════════════════════════════════

if (networkClient) {
  let remoteFullBuffer = "";
  let remoteSpeakBuffer = "";
  let activeOutputContent = null;
  let activeCursorEl = null;
  let activeStreamRenderer = null;
  const outputArea = terminal.getOutputArea();

  networkClient.on("transcription", (msg) => {
    if (!remoteTurnActive) return;
    currentDetectedLang = msg.detectedLang || null;
    terminal.show();
    const echoLine = terminal.appendEchoLine(msg.text, false, null);
    terminal.addMessageCopyIcon(echoLine, () => msg.text);
    activeOutputContent = el("div", { color: T.text });
    outputArea.appendChild(activeOutputContent);
    activeStreamRenderer = markdownRenderer.createStreamRenderer(activeOutputContent);
    activeCursorEl = el("span", {
      display: "inline-block", width: "8px", height: isNarrow ? "14px" : "16px",
      background: T.accent, animation: "jarvisCursorBlink 0.8s step-end infinite",
      verticalAlign: "middle", marginLeft: "2px",
    });
    outputArea.appendChild(activeCursorEl);
    conversationHistory.push({ role: "user", text: msg.text, timestamp: Date.now() });
    setUIState("streaming");
    terminal.setBadgeState("running");
    remoteFullBuffer = "";
  });

  networkClient.on("stream_delta", (msg) => {
    if (!remoteTurnActive) return;
    remoteFullBuffer += msg.text;
    fullBuffer += msg.text;
    if (!activeOutputContent) {
      terminal.show();
      activeOutputContent = el("div", { color: T.text });
      outputArea.appendChild(activeOutputContent);
      activeStreamRenderer = markdownRenderer.createStreamRenderer(activeOutputContent);
      activeCursorEl = el("span", {
        display: "inline-block", width: "8px", height: isNarrow ? "14px" : "16px",
        background: T.accent, animation: "jarvisCursorBlink 0.8s step-end infinite",
        verticalAlign: "middle", marginLeft: "2px",
      });
      outputArea.appendChild(activeCursorEl);
      terminal.setBadgeState("running");
    }
    activeStreamRenderer.append(msg.text);
    outputArea.scrollTop = outputArea.scrollHeight;
    // TTS — only do client-side sentence speak when NOT in server TTS mode
    // (server mode pushes audio via tts_audio events instead)
    if (mobileTtsMode !== "server" && tts.isEnabled && !tts.isMuted) {
      remoteSpeakBuffer += msg.text;
      const sentenceEnd = /^([\s\S]*?[.!?])(\s+|\n\n)/;
      let match;
      while ((match = sentenceEnd.exec(remoteSpeakBuffer)) !== null) {
        const sentence = match[1].trim();
        if (sentence) tts.speak(stripMarkdown(sentence), currentDetectedLang);
        remoteSpeakBuffer = remoteSpeakBuffer.slice(match[0].length);
      }
    }
  });

  networkClient.on("stream_end", (msg) => {
    if (!remoteTurnActive) return;
    remoteTurnActive = false;
    const completion = completionState(msg);
    if (activeCursorEl?.parentNode) activeCursorEl.parentNode.removeChild(activeCursorEl);
    activeCursorEl = null;
    if (msg.sessionId) currentSessionId = msg.sessionId;
    if (activeStreamRenderer) activeStreamRenderer.finalize();
    if (activeOutputContent && remoteFullBuffer) {
      const bufCopy = remoteFullBuffer;
      terminal.addMessageCopyIcon(activeOutputContent, () => bufCopy);
    }
    conversationHistory.push({ role: "assistant", text: remoteFullBuffer, completion, timestamp: Date.now() });
    syncToManager();
    if (mobileTtsMode !== "server" && tts.isEnabled && !tts.isMuted && remoteSpeakBuffer.trim()) {
      tts.speak(stripMarkdown(remoteSpeakBuffer.trim()), currentDetectedLang);
    }
    remoteSpeakBuffer = "";
    terminal.appendCompletionLine(completion);
    outputArea.scrollTop = outputArea.scrollHeight;
    remoteFullBuffer = "";
    activeOutputContent = null;
    activeStreamRenderer = null;
    setUIState(completion.uiState);
    terminal.setBadgeState(completion.badgeState);
  });

  networkClient.on("tts_audio", (msg) => { tts.enqueueServerAudio(msg.data, msg.sampleRate); });
  networkClient.on("tts_end", () => {});

  networkClient.on("permission_request", (msg) => {
    if (!remoteTurnActive) return;
    if (!activeOutputContent) {
      terminal.show();
      activeOutputContent = el("div", { color: T.text });
      outputArea.appendChild(activeOutputContent);
    }
    cards.renderPermissionCard(msg.requestId, msg.request, outputArea, outputArea);
  });

  networkClient.on("question_request", (msg) => {
    if (!remoteTurnActive) return;
    if (!activeOutputContent) {
      terminal.show();
      activeOutputContent = el("div", { color: T.text });
      outputArea.appendChild(activeOutputContent);
    }
    cards.renderQuestionCard(msg.requestId, msg.request, outputArea, outputArea);
  });

  networkClient.on("error", (msg) => {
    if (!remoteTurnActive) return;
    remoteTurnActive = false;
    const completion = completionState({ resultFailed: true });
    conversationHistory.push({ role: "assistant", text: remoteFullBuffer, completion, timestamp: Date.now() });
    syncToManager();
    if (activeStreamRenderer) activeStreamRenderer.finalize();
    activeStreamRenderer = null;
    if (activeCursorEl?.parentNode) activeCursorEl.parentNode.removeChild(activeCursorEl);
    activeCursorEl = null;
    activeOutputContent = null;
    terminal.appendErrorLine(msg.stage, msg.message);
    setUIState("error");
    remoteSpeakBuffer = "";
    remoteFullBuffer = "";
    terminal.setBadgeState("error");
  });

  networkClient.onStateChange((state) => {
    if (state === "connected" || !remoteTurnActive) return;
    remoteTurnActive = false;
    const completion = completionState({});
    conversationHistory.push({ role: "assistant", text: remoteFullBuffer, completion, timestamp: Date.now() });
    syncToManager();
    if (activeStreamRenderer) activeStreamRenderer.finalize();
    if (activeCursorEl?.parentNode) activeCursorEl.parentNode.removeChild(activeCursorEl);
    activeStreamRenderer = null;
    activeOutputContent = null;
    activeCursorEl = null;
    remoteFullBuffer = "";
    remoteSpeakBuffer = "";
    recorder.cancel();
    tts.stop();
    terminal.appendCompletionLine(completion);
    setUIState(completion.uiState);
  });
}

// ── Cleanup ──
ctx.cleanups.push(() => {
  syncToManager();
  tabs.cleanup();
  projSelector.cleanup();
  if (recorder.isRecording) recorder.cancel();
  tts.stop();
});

// ── Safety-net cleanup ──
const cleanupMs = 5000;
const cleanupId = setInterval(() => {
  if (!document.contains(section)) {
    arcReactor.stopTimer();
    if (recorder.isRecording) recorder.cancel();
    tts.stop();
    clearInterval(cleanupId);
  }
}, cleanupMs);
if (ctx.intervals) ctx.intervals.push(cleanupId);

return section;
