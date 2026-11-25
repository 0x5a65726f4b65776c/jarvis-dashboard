// JARVIS Companion — Voice Pipeline
// Audio → Transcribe → Claude → TTS orchestration for a single connection.

const fs = require("fs");
const protocol = require("./protocol");
const { extractSentences } = require("./text-processing");
const { verifyDelivery, BLOCKED } = require("./delivery-review");

class VoicePipeline {
  constructor(conn, { transcriber, langConfig, reviewDelivery = verifyDelivery }) {
    this._conn = conn;
    this._transcriber = transcriber;
    this._langConfig = langConfig;
    this._reviewDelivery = reviewDelivery;
  }

  async processAudioEnd() {
    const conn = this._conn;

    if (!conn.audioSession) {
      conn.send(protocol.error("audio", "No audio session active"));
      return;
    }

    const session = conn.audioSession;
    const requestTurnId = conn.turnId;
    conn.audioSession = null;
    const job = {
      cancelled: false,
      cancel: () => {
        if (job.cancelled) return;
        job.cancelled = true;
        const ownsTranscription = conn.activeAudio === job;
        if (ownsTranscription) conn.activeAudio = null;
        session.cleanup();
        if (ownsTranscription) this._transcriber.cancel();
      },
    };
    conn.activeAudio = job;
    // Internal identity also protects clients without a turnId, or clients that
    // reuse one. A cancelled conversion cannot start a later transcription.
    const current = () => conn.activeAudio === job && !job.cancelled && conn.turnId === requestTurnId;

    try {
      const wavPath = await session.convertToWav();
      if (!current()) return;
      const wavStat = fs.statSync(wavPath);
      console.log(`[JARVIS] WAV file: ${wavPath} (${wavStat.size} bytes)`);

      const result = await this._transcriber.transcribe(wavPath);
      if (!current()) return;
      const text = typeof result === "string" ? result : result.text;
      let detectedLang = typeof result === "string" ? null : result.detectedLang;
      console.log(`[JARVIS] Transcription: "${text}" (lang: ${detectedLang || "default"})`);

      if (!text) {
        conn.send(protocol.error("transcription", "Empty transcription — please try again"));
        return;
      }

      // Filter detected language against supported list
      const supportedLangs = this._langConfig.supported || {};
      if (detectedLang && Object.keys(supportedLangs).length > 0 && !supportedLangs[detectedLang]) {
        console.log(`[JARVIS] Detected '${detectedLang}' not in supported languages, falling back to '${this._langConfig.fallback || "en"}'`);
        detectedLang = this._langConfig.fallback || "en";
      }

      if (detectedLang) {
        console.log(`[JARVIS] Detected language: ${detectedLang}`);
      }

      if (conn.tts && detectedLang) {
        conn.tts.setLanguage(detectedLang);
      }

      conn.send(protocol.transcription(text, detectedLang));
      this.runClaude(text, detectedLang);
    } catch (err) {
      if (!current()) return;
      conn.send(protocol.error("transcription", err.message));
    } finally {
      session.cleanup();
      if (conn.activeAudio === job) conn.activeAudio = null;
    }
  }

  runClaude(text, detectedLang) {
    const conn = this._conn;
    const langConfig = this._langConfig;

    // Prepend language tag for Claude
    let messageText = text;
    const supportedLangs = langConfig.supported || {};
    if (detectedLang && Object.keys(supportedLangs).length > 0 && supportedLangs[detectedLang]) {
      const langLabel = supportedLangs[detectedLang].label || detectedLang;
      messageText = `[Language: ${langLabel}]\n${text}`;
    }

    conn.speakBuffer = "";
    const turn = { finished: false, validating: false, requestTurnId: conn.turnId };
    this._turn = turn;
    const current = () => this._turn === turn && !turn.finished && conn.turnId === turn.requestTurnId;
    const handle = conn.runner.run(messageText, {
      onDelta: (delta) => {
        if (!current()) return;
        if (conn.resetActivity) conn.resetActivity();
        // Candidate deltas include drafts and Stop-hook continuations. Keep all
        // of them off the socket and TTS; only the terminal result can clear.
      },
      onEnd: async (result) => {
        if (!current() || turn.validating) return;
        const { exitCode, sessionId } = result;
        if (exitCode !== 0 || result.signal || result.resultReceived !== true || result.resultFailed !== false) {
          turn.finished = true;
          conn.activeRunner = null;
          conn.speakBuffer = "";
          conn.send(protocol.streamEnd(sessionId, result));
          return;
        }
        turn.validating = true;
        try {
          const cleared = await this._reviewDelivery(result, messageText);
          // A cancellation or a newer request can occur while Python verifies
          // the release. It invalidates this connection's pending delivery.
          if (!current()) return;
          turn.finished = true;
          conn.activeRunner = null;
          const sent = conn.send(protocol.streamDelta(cleared.text, cleared.releaseId));
          const ended = conn.send(protocol.streamEnd(sessionId, {
            ...result, deliveryReview: "cleared", releaseId: cleared.releaseId,
          }));
          // The client sees the release attestation before any audio frame.
          if (sent && ended && conn.tts?.isEnabled) {
            const { sentences, remainder } = extractSentences(cleared.text);
            sentences.forEach((s) => conn.tts.enqueue(s, detectedLang, cleared.releaseId));
            if (remainder.trim()) conn.tts.enqueue(remainder.trim(), detectedLang, cleared.releaseId);
          }
          conn.speakBuffer = "";
          console.log(`[JARVIS] Reviewed response delivered (session ${sessionId?.slice(0, 7) || "none"})`);
        } catch {
          if (!current()) return;
          turn.finished = true;
          conn.activeRunner = null;
          conn.speakBuffer = "";
          conn.send(protocol.error("review", BLOCKED));
          conn.send(protocol.streamEnd(sessionId, { ...result, deliveryReview: "blocked" }));
        }
      },
      onError: (err) => {
        if (!current()) return;
        turn.finished = true;
        conn.activeRunner = null;
        conn.send(protocol.error("claude", err.message));
      },
      onPermissionRequest: (requestId, request) => {
        if (!current()) return;
        if (conn.resetActivity) conn.resetActivity();
        // Auto-approve tools from config
        const autoApprove = conn._rawConfig.widgets?.voiceCommand?.interactive?.autoApproveTools || [];
        if (autoApprove.includes(request.tool_name)) {
          console.log(`[JARVIS] Auto-approving tool: ${request.tool_name} (${requestId})`);
          conn.runner.sendControlResponse({
            subtype: "success", request_id: requestId, response: { behavior: "allow" },
          });
          return;
        }
        conn.send(protocol.permissionRequest(requestId, request));
        console.log(`[JARVIS] Permission request → client: ${request.tool_name} (${requestId})`);
      },
      onQuestionRequest: (requestId, request) => {
        if (!current()) return;
        if (conn.resetActivity) conn.resetActivity();
        conn.send(protocol.questionRequest(requestId, request));
        console.log(`[JARVIS] Question request → client: ${requestId}`);
      },
    });
    conn.activeRunner = turn.finished ? null : {
      cancel: () => {
        if (!current()) return;
        turn.finished = true;
        conn.speakBuffer = "";
        handle.cancel();
      },
    };
  }
}

module.exports = VoicePipeline;
