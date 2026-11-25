// JARVIS Companion — Message Protocol
// Defines the WebSocket message contract between mobile client and server.
// All messages are JSON strings over text frames. Audio data uses binary frames.

const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
const isString = value => typeof value === "string" && !value.includes("\0");
const optionalString = value => value == null || isString(value);

function validMessage(msg) {
  if (!isObject(msg) || !isString(msg.type) || !optionalString(msg.turnId)) return false;
  switch (msg.type) {
    case "ping": case "cancel": case "new_session": case "audio_end":
      return true;
    case "text_command":
      return isString(msg.text) && !!msg.text.trim()
        && optionalString(msg.sessionId) && optionalString(msg.projectPath);
    case "audio_start":
      return (msg.format == null || ["mp4", "webm", "wav", "ogg", "m4a", "mp3", "aac"].includes(msg.format))
        && (msg.sampleRate == null || (Number.isFinite(msg.sampleRate) && msg.sampleRate > 0))
        && optionalString(msg.sessionId) && optionalString(msg.projectPath);
    case "tts_toggle":
      return typeof msg.muted === "boolean";
    case "permission_response":
      return isString(msg.requestId) && !!msg.requestId
        && (msg.behavior == null || ["allow", "deny"].includes(msg.behavior))
        && (msg.updatedPermissions == null || (Array.isArray(msg.updatedPermissions) && msg.updatedPermissions.every(isObject)));
    case "question_response":
      return isString(msg.requestId) && !!msg.requestId && (isString(msg.answer) || isObject(msg.answer));
    default:
      return false;
  }
}

const protocol = {
  // ── Server → Client message builders ──

  connected: (version = "1.0") =>
    JSON.stringify({ type: "connected", version }),

  transcription: (text, detectedLang) =>
    JSON.stringify({ type: "transcription", text, ...(detectedLang ? { detectedLang } : {}) }),

  streamDelta: (text, releaseId) =>
    JSON.stringify({ type: "stream_delta", text, releaseId: releaseId || null }),

  streamEnd: (sessionId, result = {}) =>
    JSON.stringify({
      type: "stream_end", sessionId: sessionId ?? null,
      exitCode: Number.isInteger(result.exitCode) ? result.exitCode : null,
      signal: result.signal || null,
      resultReceived: result.resultReceived === true,
      resultFailed: typeof result.resultFailed === "boolean" ? result.resultFailed : null,
      deliveryReview: ["cleared", "blocked"].includes(result.deliveryReview) ? result.deliveryReview : null,
      releaseId: result.deliveryReview === "cleared" ? result.releaseId || null : null,
      taskVerified: false,
    }),

  ttsAudio: (base64Pcm, sampleRate = 22050, releaseId) =>
    JSON.stringify({ type: "tts_audio", data: base64Pcm, sampleRate, releaseId: releaseId || null }),

  ttsEnd: () =>
    JSON.stringify({ type: "tts_end" }),

  error: (stage, message) =>
    JSON.stringify({ type: "error", stage, message }),

  pong: () =>
    JSON.stringify({ type: "pong" }),

  // ── Interactive control messages (permissions & questions) ──

  permissionRequest: (requestId, request) =>
    JSON.stringify({ type: "permission_request", requestId, request }),

  questionRequest: (requestId, request) =>
    JSON.stringify({ type: "question_request", requestId, request }),

  // ── Parse inbound message — returns null on malformed input ──
  parse: (raw) => {
    try {
      const msg = JSON.parse(raw);
      return validMessage(msg) ? msg : null;
    } catch {
      return null;
    }
  },
};

module.exports = protocol;
