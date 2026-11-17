# JARVIS Companion Server

Secure WebSocket server that enables JARVIS voice commands from Obsidian Mobile (iPhone/iPad). Runs on your Mac and handles whisper-cpp transcription, Claude CLI, and TTS. Replies are released after the owning orchestrator and Hermes complete final review.

## Prerequisites

- **Node.js** 18+ (`brew install node`)
- **ffmpeg** (`brew install ffmpeg`) — converts mobile audio (MP4/AAC) to WAV for whisper
- **OpenSSL** (`brew install openssl`) — generates TLS certificates
- **whisper-cpp** (`brew install whisper-cpp`) — optional, for voice commands
- **Claude CLI** — installed and authenticated
- **Python 3** — runs the selected project's delivery gate

## Reviewed delivery

The companion and desktop local-process widget hold candidate text off the UI and TTS. Permission requests, clarification requests, and process status can still appear while work continues. After Claude exits successfully, the transport checks the exact terminal result against `scripts/agent_gate.py` in the actual process working directory. A resumed session ID, a success exit code, or a caller-supplied approval flag cannot clear a reply.

The check requires the runtime's terminal session ID, both review receipts, a successful Stop gate, the normalized reply hash, the exact submitted prompt hash, and a prompt receipt created after this process launched. Changed evidence, a different reply or prompt, an old review, missing hooks, or a failed reviewer withhold the reply. The fixed review-failure notice is a status message, not a cleared substantive answer. Retries and answer-resumes bind to a fresh launch and their own exact submitted prompt.

Remote desktop and mobile clients require the authenticated companion's matching release ID and clean completion facts before rendering text, saving assistant text to history, or playing audio. Per-request IDs exclude late events from canceled or superseded requests. Server audio carries the same release ID and begins only after the release event. Legacy companion messages without these fields are withheld; update the companion and client together. The local desktop adapter is packaged separately at `src/widgets/voice-command/desktop/delivery-review.js`; an offline test keeps it byte-identical to `companion/modules/delivery-review.js`.

Every selected project must have the current delivery scripts, runtime hooks, Hermes reviewer definition, and valid fidelity manifest. Pointing the widget at another directory does not borrow this repository's review receipts. Updating this repository alone does not restart an installed companion or refresh an already-loaded Obsidian widget. Activation requires deploying the updated companion and widget/service files, restarting or reloading them, and using an updated project context. No credentials, tool approvals, or platform permissions are expanded by this mechanism.

Run `npm test` inside `companion` for offline regression checks. These execute the real Python gate against synthetic hook/review fixtures and exercise buffering, stale receipts, cancellation, local rendering, remote/mobile delivery, and delayed audio. They do not make a live Claude request or play audio. A cleared reply remains separate from proof that the underlying business task is complete; the transport continues to report `taskVerified: false`.

## Quick Start

```bash
# 1. Run setup (generates certs, token, installs deps)
cd companion
bash setup.sh

# 2. Start the server
npm start
```

The server starts on `wss://0.0.0.0:7777` (configurable via `src/config/config.json`).

## Setup Details

The `setup.sh` script handles everything:

1. **TLS Certificates** — Generates a local CA and server certificate in `certs/`
2. **Auth Token** — 64-character hex token saved to `.env`
3. **Local Config** — Creates `src/config/config.local.json` with your hostname and token
4. **npm Dependencies** — Installs the `ws` package
5. **LaunchAgent** — Generates `com.jarvis.companion.plist` for auto-start

### Tailscale Support

For remote access via Tailscale VPN:

```bash
# If Tailscale is installed, the IP is auto-detected
bash setup.sh

# Or provide it manually
bash setup.sh --tailscale-ip 100.x.y.z
```

## iOS Certificate Setup (One-time)

To connect from Obsidian Mobile, iOS must trust the server's TLS certificate:

1. **AirDrop** `certs/jarvis-ca.pem` to your iPhone
2. **Settings** → General → VPN & Device Management → Install the profile
3. **Settings** → General → About → Certificate Trust Settings → Enable "JARVIS Local CA"

## Auto-Start (LaunchAgent)

Run the companion server automatically on login:

```bash
cp com.jarvis.companion.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jarvis.companion.plist
```

**Manage:**
```bash
# Check status
launchctl list | grep jarvis

# Stop
launchctl unload ~/Library/LaunchAgents/com.jarvis.companion.plist

# View logs
tail -f /tmp/jarvis-companion.log
tail -f /tmp/jarvis-companion.err
```

The LaunchAgent restarts the server automatically if it crashes (min 10s between restarts).

## Configuration

All settings are in `src/config/config.json` (committed) and `src/config/config.local.json` (gitignored).

### Network Settings (`config.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `network.host` | `"your-mac.local"` | Mac hostname for mobile to connect |
| `network.port` | `7777` | WebSocket server port |
| `network.mobileTts` | `"local"` | TTS mode: `"local"` (mobile handles) or `"server"` (Mac streams audio) |
| `network.audioSizeLimit` | `10485760` | Max audio upload size (10MB) |
| `network.heartbeatInterval` | `30000` | Ping interval in ms |

### Companion Settings (`config.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `companion.ffmpegPath` | `/opt/homebrew/bin/ffmpeg` | Path to ffmpeg binary |
| `companion.whisperPath` | `/opt/homebrew/bin/whisper-cli` | Path to whisper-cli binary |
| `companion.whisperModel` | (ggml-base.en.bin) | Path to whisper model file |
| `companion.claudePath` | `null` (auto-detect) | Path to claude CLI binary |
| `companion.maxConnections` | `2` | Max concurrent WebSocket connections |
| `companion.rateLimitPerMinute` | `10` | Max connection attempts per minute per IP |

### Local Overrides (`config.local.json`)

| Key | Description |
|-----|-------------|
| `network.host` | Your Mac's actual hostname |
| `network.token` | Auth token (generated by setup.sh) |
| `network.tailscaleHost` | Tailscale IP for remote access |

## Security

- **TLS encryption** — All WebSocket traffic is encrypted (WSS)
- **Pre-shared token** — 64-char hex token validated before WebSocket handshake
- **Timing-safe comparison** — Token validation uses `crypto.timingSafeEqual`
- **Connection limits** — Max 2 concurrent connections, 10 attempts/min rate limit
- **Audio size limit** — 10MB max to prevent abuse
- **Idle timeout** — Connections closed after 5 minutes of inactivity
- **Tailscale VPN** — For remote access, no public ports exposed

## Troubleshooting

**Port already in use:**
Change `network.port` in `config.json` and restart.

**whisper-cli not found:**
Install: `brew install whisper-cpp`. Text commands still work without it.

**Certificate not trusted on iOS:**
Re-do the iOS certificate setup steps above. Make sure to enable trust in Certificate Trust Settings (step 3).

**Connection fails from mobile:**
1. Verify server is running: `launchctl list | grep jarvis`
2. Check both devices are on the same WiFi (or Tailscale)
3. Verify the hostname resolves: `ping your-mac.local`
4. Check logs: `tail -f /tmp/jarvis-companion.log`
