// JARVIS Companion — Whisper-cpp Transcriber
// Wraps whisper-cli for server-side audio transcription.
// Mirrors the spawning pattern from src/services/voice-service.js

const { spawn } = require("child_process");
const fs = require("fs");

class Transcriber {
  constructor(config, { spawnProcess = spawn, schedule = setTimeout, cancelTimer = clearTimeout } = {}) {
    this._spawn = spawnProcess;
    this._schedule = schedule;
    this._cancelTimer = cancelTimer;
    this._whisperPath = config.whisperPath || "/opt/homebrew/bin/whisper-cli";
    this._modelPath = config.whisperModel || "/opt/homebrew/share/whisper-cpp/ggml-base.en.bin";
    this._lang = config.whisperLang || "en";
    this._autoDetect = this._lang === "auto";
    this._activeRun = null;

    // Validate: English-only model cannot auto-detect
    if (this._autoDetect && this._modelPath.includes(".en.")) {
      console.warn("[Transcriber] WARNING: Whisper model appears English-only (.en suffix). Auto-detect disabled. Use ggml-base.bin for multilingual.");
      this._autoDetect = false;
      this._lang = "en";
    }
  }

  get isAvailable() {
    return fs.existsSync(this._whisperPath) && fs.existsSync(this._modelPath);
  }

  transcribe(wavPath) {
    this.cancel();
    return new Promise((resolve, reject) => {
      if (!this.isAvailable) {
        reject(new Error(`whisper-cli not found at ${this._whisperPath} or model missing at ${this._modelPath}`));
        return;
      }

      const run = { process: null, closed: false, settled: false, reject };
      let proc;
      try {
        proc = this._spawn(this._whisperPath, [
          "-m", this._modelPath,
          "-f", wavPath,
          "--no-timestamps",
          "-l", this._autoDetect ? "auto" : this._lang,
        ]);
      } catch (err) {
        try { fs.unlinkSync(wavPath); } catch {}
        reject(err);
        return;
      }
      run.process = proc;
      this._activeRun = run;
      const finish = () => {
        run.closed = true;
        this._cancelTimer(run.killTimer);
        if (this._activeRun === run) this._activeRun = null;
        try { fs.unlinkSync(wavPath); } catch {}
      };

      let result = "";
      let errorOutput = "";

      proc.stdout.on("data", (data) => {
        result += data.toString();
      });

      proc.stderr.on("data", (data) => {
        errorOutput += data.toString();
      });

      proc.on("close", (code) => {
        finish();
        if (run.settled) return;
        run.settled = true;

        if (code !== 0) {
          reject(new Error(`whisper-cli exited with code ${code}: ${errorOutput.trim()}`));
          return;
        }

        // Strip whisper timestamp prefixes like [00:00:00.000 --> 00:00:02.000]
        const text = result
          .trim()
          .replace(/^\[.*?\]\s*/gm, "")
          .trim();

        // Parse detected language from stderr when auto-detect is on
        // whisper-cpp outputs: "auto-detected language: en (p = 0.97)"
        let detectedLang = null;
        if (this._autoDetect) {
          const langMatch = errorOutput.match(/auto-detected language:\s*(\w+)/);
          if (langMatch) {
            detectedLang = langMatch[1];
          }
        }

        resolve({ text: text || "", detectedLang });
      });

      proc.on("error", (err) => {
        finish();
        if (run.settled) return;
        run.settled = true;
        reject(new Error(`whisper-cli error: ${err.message}`));
      });
    });
  }

  cancel() {
    const run = this._activeRun;
    if (!run || run.settled) return;
    this._activeRun = null;
    run.settled = true;
    run.reject(new Error("Transcription cancelled"));
    try { run.process.kill("SIGTERM"); } catch {}
    run.killTimer = this._schedule(() => {
      if (!run.closed) { try { run.process.kill("SIGKILL"); } catch {} }
    }, 2000);
    run.killTimer?.unref?.();
  }
}

module.exports = Transcriber;
