// JARVIS Companion — Audio Processing
// Temp file management for incoming audio + MP4/WebM → WAV conversion via ffmpeg.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const crypto = require("crypto");

class AudioSession {
  constructor(format, ffmpegPath, sizeLimit, { spawnProcess = spawn, schedule = setTimeout, cancelTimer = clearTimeout } = {}) {
    this._spawn = spawnProcess;
    this._schedule = schedule;
    this._cancelTimer = cancelTimer;
    this._conversion = null;
    this._cancelled = false;
    this._id = crypto.randomUUID();
    this._format = format || "mp4";
    this._ffmpegPath = ffmpegPath;
    this._sizeLimit = sizeLimit;
    this._totalSize = 0;
    this._inputPath = path.join(os.tmpdir(), `jarvis-audio-${this._id}.${this._format}`);
    this._outputPath = path.join(os.tmpdir(), `jarvis-audio-${this._id}.wav`);
    this._fd = fs.openSync(this._inputPath, "w");
    this._closed = false;
  }

  appendChunk(buffer) {
    if (this._closed) return { ok: false, error: "Session closed" };
    this._totalSize += buffer.length;
    if (this._totalSize > this._sizeLimit) {
      this.cleanup();
      return { ok: false, error: `Audio size limit exceeded (${Math.round(this._sizeLimit / 1048576)}MB max)` };
    }
    fs.writeSync(this._fd, buffer);
    return { ok: true };
  }

  async convertToWav() {
    if (this._closed) throw new Error("Session closed");
    fs.closeSync(this._fd);
    this._closed = true;

    // Check if the file has content
    const stat = fs.statSync(this._inputPath);
    if (stat.size === 0) {
      this.cleanup();
      throw new Error("Empty audio file");
    }

    return new Promise((resolve, reject) => {
      const proc = this._spawn(this._ffmpegPath, [
        "-i", this._inputPath,
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        this._outputPath,
        "-y",
        "-loglevel", "error",
      ]);
      const conversion = { process: proc, closed: false, settled: false, reject };
      this._conversion = conversion;
      const finish = () => {
        conversion.closed = true;
        this._cancelTimer(conversion.killTimer);
        if (this._conversion === conversion) this._conversion = null;
        try { fs.unlinkSync(this._inputPath); } catch {}
      };

      let stderr = "";
      proc.stderr.on("data", (d) => { stderr += d.toString(); });

      proc.on("close", (code) => {
        finish();
        if (this._cancelled || conversion.settled) {
          try { fs.unlinkSync(this._outputPath); } catch {}
          return;
        }
        conversion.settled = true;

        if (code !== 0) {
          try { fs.unlinkSync(this._outputPath); } catch {}
          reject(new Error(`ffmpeg exited with code ${code}: ${stderr.trim()}`));
          return;
        }
        resolve(this._outputPath);
      });

      proc.on("error", (err) => {
        finish();
        try { fs.unlinkSync(this._outputPath); } catch {}
        if (conversion.settled) return;
        conversion.settled = true;
        reject(new Error(`ffmpeg not found: ${err.message}`));
      });
    });
  }

  cleanup() {
    this._cancelled = true;
    const conversion = this._conversion;
    if (conversion && !conversion.settled) {
      conversion.settled = true;
      conversion.reject(new Error("Audio conversion cancelled"));
      try { conversion.process.kill("SIGTERM"); } catch {}
      conversion.killTimer = this._schedule(() => {
        if (!conversion.closed) { try { conversion.process.kill("SIGKILL"); } catch {} }
      }, 2000);
      conversion.killTimer?.unref?.();
    }
    if (!this._closed) {
      try { fs.closeSync(this._fd); } catch {}
      this._closed = true;
    }
    try { fs.unlinkSync(this._inputPath); } catch {}
    try { fs.unlinkSync(this._outputPath); } catch {}
  }
}

module.exports = { AudioSession };
