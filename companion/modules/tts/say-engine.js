// JARVIS Companion — macOS Say TTS Engine
// Synthesizes speech via macOS `say` command, outputs PCM float32 at 22050 Hz.

const { spawn } = require("child_process");
const BaseTTSEngine = require("./base-engine");

class SayEngine extends BaseTTSEngine {
  constructor(config) {
    super();
    this._voice = config.voice || "Samantha";
    this._rate = config.rate || 185;
  }

  speak(text, lang, epoch) {
    const proc = spawn("say", [
      "-v", this._voice,
      "-r", String(this._rate),
      "-o", "-",
      "--data-format=LEF32@22050",
      text,
    ]);
    this._activeProcess = proc;
    const current = () => this._activeProcess === proc && this._epoch === epoch;

    proc.stdout.on("data", (chunk) => {
      if (current() && this._onAudioChunk) {
        this._onAudioChunk(chunk.toString("base64"), 22050);
      }
    });

    proc.on("close", () => {
      if (!current()) return;
      this._activeProcess = null;
      if (this._onComplete) this._onComplete({ success: true });
    });

    proc.on("error", () => {
      if (!current()) return;
      this._activeProcess = null;
      if (this._onComplete) this._onComplete({ success: true });
    });
  }
}

module.exports = SayEngine;
