// Final transport authorization uses the same local gate as the agent runtime.
// No network request, shell expansion, or caller-supplied success flag is used.
const { execFile } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const BLOCKED = "Final review did not clear this response. No reply was released.";

function sha256(text) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}

function replyHash(text) {
  return sha256(text.replace(/\r\n/g, "\n").trim());
}

async function verifyDelivery(result, prompt, { execute = execFile } = {}) {
  if (result.exitCode !== 0 || result.signal || result.resultReceived !== true || result.resultFailed !== false) {
    throw new Error(BLOCKED);
  }
  // The final result's session ID is observed in this run. A resumed ID or a
  // guessed transcript filename is not enough to authorize a delivery.
  if (typeof result.runtimeSessionId !== "string" || !result.runtimeSessionId
      || typeof result.finalText !== "string" || !result.finalText.trim()
      || typeof result.projectPath !== "string" || !path.isAbsolute(result.projectPath)
      || typeof result.startedAt !== "string" || !Number.isFinite(Date.parse(result.startedAt))
      || typeof prompt !== "string") {
    throw new Error(BLOCKED);
  }
  const script = path.join(result.projectPath, "scripts", "agent_gate.py");
  if (!fs.existsSync(script)) throw new Error(BLOCKED);
  const finalText = result.finalText;
  const args = [script, "check", "--session", result.runtimeSessionId, "--transport",
    "--reply-sha256", replyHash(finalText), "--prompt-sha256", sha256(prompt),
    "--not-before", result.startedAt];
  const stdout = await new Promise((resolve, reject) => {
    execute("python3", args, {
      cwd: result.projectPath, timeout: 30000, maxBuffer: 1024 * 1024, windowsHide: true,
    }, (error, output) => error ? reject(new Error(BLOCKED)) : resolve(output));
  });
  let receipt;
  try { receipt = JSON.parse(stdout); } catch { throw new Error(BLOCKED); }
  if (!receipt || typeof receipt.cleared !== "string" || !/^[a-f0-9]{64}$/.test(receipt.cleared)) {
    throw new Error(BLOCKED);
  }
  return { releaseId: receipt.cleared, text: finalText };
}

module.exports = { verifyDelivery, replyHash, sha256, BLOCKED };
