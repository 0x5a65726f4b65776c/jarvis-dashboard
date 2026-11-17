// Voice Command — Stream handler
// Parses NDJSON stream events and dispatches typed callbacks.
// Used by both local (stdout) and remote (networkClient events) paths.

// Process completion is separate from acceptance of the requested task.
// Missing evidence (including legacy stream_end messages) stays unverified.
function completionState(result = {}) {
  const exitCode = Number.isInteger(result?.exitCode) ? result.exitCode : null;
  const signal = result?.signal || null;
  const resultReceived = result?.resultReceived === true;
  const resultFailed = typeof result?.resultFailed === "boolean" ? result.resultFailed : null;
  const deliveryReview = ["cleared", "blocked"].includes(result?.deliveryReview) ? result.deliveryReview : null;
  const failed = (exitCode !== null && exitCode !== 0) || !!signal || resultFailed === true || deliveryReview === "blocked";
  const completed = !failed && exitCode === 0 && resultReceived && resultFailed === false;
  return {
    exitCode, signal, resultReceived, resultFailed, deliveryReview, taskVerified: false,
    status: failed ? "failed" : completed ? "completed" : "unknown",
    uiState: failed ? "error" : completed ? "done" : "idle",
    badgeState: failed ? "error" : completed ? "success" : "idle",
    label: deliveryReview === "blocked" ? "Response withheld — final review did not clear"
      : failed ? (signal ? `Process interrupted (${signal})` : exitCode ? `Process exited with code ${exitCode}` : "Response failed")
      : completed ? "Response finished" : "Response status unverified — reconcile before retrying",
  };
}

function historyCompletion(history) {
  const last = [...history].reverse().find(turn => turn.role === "assistant" || turn.role === "user");
  return completionState(last?.role === "assistant" ? last.completion : {});
}

// Remote clients cannot run the project gate. They hold candidate content until
// the authenticated companion attests the matching release and clean exit.
function createRemoteDeliveryBuffer() {
  let candidate = "";
  let candidateRelease = null;
  let clearedRelease = null;
  let rejected = false;
  const validId = (id) => typeof id === "string" && /^[a-f0-9]{64}$/.test(id);
  function reset() {
    candidate = "";
    candidateRelease = null;
    clearedRelease = null;
    rejected = false;
  }
  return {
    reset,
    append(msg) {
      if (rejected) return;
      if (typeof msg.text !== "string" || !validId(msg.releaseId)
          || (candidateRelease && candidateRelease !== msg.releaseId)) {
        rejected = true;
        candidate = "";
        return;
      }
      candidateRelease = msg.releaseId;
      candidate += msg.text;
    },
    finish(msg) {
      const facts = completionState(msg);
      const cleared = !rejected && candidate.trim() && facts.status === "completed"
        && msg.deliveryReview === "cleared" && validId(msg.releaseId) && msg.releaseId === candidateRelease;
      const text = cleared ? candidate : "";
      clearedRelease = cleared ? msg.releaseId : null;
      candidate = "";
      candidateRelease = null;
      return {
        text,
        completion: completionState({ ...msg, deliveryReview: cleared ? "cleared" : "blocked" }),
      };
    },
    acceptsAudio(msg) {
      return !!clearedRelease && msg.releaseId === clearedRelease;
    },
  };
}

function createStreamHandler(callbacks) {
  const {
    onTextDelta, onToolUseStart, onToolUseComplete,
    onControlRequest, onResult, onAssistantMessage, onThinking,
    onError, onSessionId,
  } = callbacks;

  let lineBuf = "";
  let activeToolBlock = null;
  let toolEvents = [];
  let buffer = "";
  let resultReceived = false;
  const remoteDelivery = createRemoteDeliveryBuffer();
  let _lastStatusLabel = null;
  let _thinkingShown = false;

  function processNdjsonLine(line) {
    if (!line.trim()) return;
    try {
      const evt = JSON.parse(line);

      // Raw stdout is a candidate stream. The local orchestrator must verify
      // the terminal result with the project gate before presenting any text.

      // Tool-use start
      if (evt.type === "stream_event" &&
          evt.event?.type === "content_block_start" &&
          evt.event?.content_block?.type === "tool_use") {
        const block = evt.event.content_block;
        activeToolBlock = {
          name: block.name,
          id: block.id,
          index: evt.event.index,
          inputJsonChunks: [],
          controlRequestFired: false,
        };
        toolEvents.push(block.name);
        if (onToolUseStart) onToolUseStart(block.name, block.id);
      }

      // Accumulate tool input JSON
      if (evt.type === "stream_event" &&
          evt.event?.type === "content_block_delta" &&
          evt.event?.delta?.type === "input_json_delta") {
        if (activeToolBlock) {
          activeToolBlock.inputJsonChunks.push(evt.event.delta.partial_json);
        }
      }

      // Tool-use complete
      if (evt.type === "stream_event" &&
          evt.event?.type === "content_block_stop" &&
          activeToolBlock) {
        const block = activeToolBlock;
        activeToolBlock = null;
        let parsedInput = {};
        try { parsedInput = JSON.parse(block.inputJsonChunks.join("")); } catch {}
        if (onToolUseComplete) onToolUseComplete(block.name, block.id, parsedInput);
      }

      // Control request
      if (evt.type === "control_request") {
        if (activeToolBlock && activeToolBlock.name === evt.request?.tool_name) {
          activeToolBlock.controlRequestFired = true;
        }
        if (onControlRequest) onControlRequest(evt.request_id, evt.request || {});
      }

      // Session ID
      if (evt.session_id && onSessionId) onSessionId(evt.session_id);

      // Result
      if (evt.type === "result") {
        resultReceived = true;
        if (evt.session_id && onSessionId) onSessionId(evt.session_id);
        if (onResult) onResult(evt);
      }

      // Assistant message (contains complete tool_use blocks)
      if (evt.type === "assistant" && evt.message?.content) {
        let hasThinking = false;
        for (const block of evt.message.content) {
          if (block.type === "thinking" && !hasThinking) {
            hasThinking = true;
            if (!_thinkingShown) {
              _thinkingShown = true;
              if (onThinking) onThinking();
            }
          }
          if (block.type === "tool_use") {
            if (onAssistantMessage) onAssistantMessage(block);
          }
        }
      }

    } catch (e) {
      console.warn("[JARVIS] Ignored malformed process event");
      if (onError) onError(e);
    }
  }

  function processNdjsonChunk(chunk) {
    lineBuf += chunk;
    const lines = lineBuf.split("\n");
    lineBuf = lines.pop();
    for (const line of lines) {
      processNdjsonLine(line);
    }
  }

  // For network events (already parsed)
  function handleNetworkEvent(type, msg) {
    if (type === "stream_delta") {
      remoteDelivery.append(msg);
    } else if (type === "stream_end") {
      const released = remoteDelivery.finish(msg);
      if (released.text) {
        buffer += released.text;
        _lastStatusLabel = null;
        if (onTextDelta) onTextDelta(released.text);
      }
      if (onResult) onResult({ ...msg, ...released.completion, type: "stream_end" });
    } else if (type === "transcription") {
      // Handled by orchestrator
    } else if (type === "permission_request") {
      if (onControlRequest) onControlRequest(msg.requestId, {
        ...msg.request,
        subtype: "can_use_tool",
      });
    } else if (type === "question_request") {
      if (onControlRequest) onControlRequest(msg.requestId, {
        ...msg.request,
        subtype: "elicitation",
      });
    } else if (type === "tts_audio") {
      // Handled by TTS adapter
    } else if (type === "error") {
      remoteDelivery.reset();
      if (onError) onError(new Error(msg.message || "Unknown error"));
    }
  }

  function getState() {
    return {
      buffer,
      lineBuf,
      toolEvents,
      activeToolBlock,
      resultReceived,
      _lastStatusLabel,
      _thinkingShown,
    };
  }

  function resetForNewTurn() {
    remoteDelivery.reset();
    buffer = "";
    lineBuf = "";
    toolEvents = [];
    activeToolBlock = null;
    resultReceived = false;
    _lastStatusLabel = null;
    _thinkingShown = false;
  }

  function cleanup() {
    remoteDelivery.reset();
    lineBuf = "";
    activeToolBlock = null;
  }

  return {
    processNdjsonChunk,
    processNdjsonLine,
    handleNetworkEvent,
    getState,
    resetForNewTurn,
    cleanup,
    get buffer() { return buffer; },
    get resultReceived() { return resultReceived; },
    get lastStatusLabel() { return _lastStatusLabel; },
    set lastStatusLabel(v) { _lastStatusLabel = v; },
    get thinkingShown() { return _thinkingShown; },
  };
}

return { createStreamHandler, completionState, historyCompletion, createRemoteDeliveryBuffer };
