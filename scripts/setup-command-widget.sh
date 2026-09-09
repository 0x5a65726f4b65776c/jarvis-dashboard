#!/usr/bin/env bash
# Sets up the Jarvis dashboard inside an Obsidian vault and configures the
# Command widget. Safe to re-run: it never overwrites an existing config, and
# it verifies the GitHub token actually works before telling you it's done.
#
#   ./scripts/setup-command-widget.sh ~/path/to/YourVault [owner/orchestrator-repo]
#
# The token is read interactively, never taken as an argument -- an argument
# lands in shell history and in `ps` output for every process on the machine.

set -euo pipefail

VAULT="${1:-}"
ORCH_REPO="${2:-0x5a65726f4b65776c/-jarvis-orchestrator}"
DASH_REPO="https://github.com/0x5a65726f4b65776c/jarvis-dashboard.git"
DEST_NAME="Jarvis Dashboard"

# Set SKIP_TOKEN_CHECK=1 to configure without a live GitHub call (offline setup).
SKIP_TOKEN_CHECK="${SKIP_TOKEN_CHECK:-0}"

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
step() { printf '\n\033[36m== %s\033[0m\n' "$*"; }

die() { red "FAILED: $*"; exit 1; }

if [ -z "$VAULT" ]; then
  cat <<USAGE
Usage: $0 <vault-path> [owner/orchestrator-repo]

  vault-path             Your Obsidian vault (the folder containing .obsidian/)
  owner/orchestrator-repo  Defaults to $ORCH_REPO

Example:
  $0 ~/Documents/MyVault
USAGE
  exit 2
fi

# ── Prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites"
command -v git >/dev/null 2>&1 || die "git not found."
command -v python3 >/dev/null 2>&1 || die "python3 not found. On macOS: xcode-select --install"
command -v curl >/dev/null 2>&1 || die "curl not found."
grn "git, python3, curl present"

VAULT="${VAULT/#\~/$HOME}"
[ -d "$VAULT" ] || die "Vault not found: $VAULT"
if [ ! -d "$VAULT/.obsidian" ]; then
  ylw "No .obsidian/ in $VAULT -- is that really your vault root?"
  ylw "Continuing anyway; Obsidian creates .obsidian on first open."
fi
DEST="$VAULT/$DEST_NAME"

# ── Clone or update ────────────────────────────────────────────────────────
step "Installing the dashboard into the vault"
if [ -d "$DEST/.git" ]; then
  grn "Already present at: $DEST"
  echo "  Pulling latest..."
  git -C "$DEST" pull --ff-only || ylw "  Pull skipped (local changes or diverged branch) -- not forcing."
elif [ -e "$DEST" ]; then
  die "$DEST exists but is not a git checkout. Move it aside first; refusing to overwrite."
else
  git clone --depth 1 "$DASH_REPO" "$DEST"
  grn "Cloned to: $DEST"
fi

# ── Config files (never clobber) ───────────────────────────────────────────
step "Creating config files"
CFG="$DEST/src/config/config.json"
LCFG="$DEST/src/config/config.local.json"

if [ -f "$CFG" ]; then
  grn "config.json already exists -- left untouched"
else
  cp "$DEST/src/config/config.example.json" "$CFG"
  grn "Created config.json from the example"
fi

if [ -f "$LCFG" ]; then
  grn "config.local.json already exists -- left untouched"
else
  cp "$DEST/src/config/config.local.example.json" "$LCFG"
  grn "Created config.local.json from the example"
fi

# ── Point the widget at the orchestrator repo ──────────────────────────────
step "Pointing the Command widget at $ORCH_REPO"
python3 - "$CFG" "$ORCH_REPO" <<'PY'
import json, sys
path, repo = sys.argv[1], sys.argv[2]
with open(path) as f: cfg = json.load(f)
cfg.setdefault("widgets", {}).setdefault("command", {})
cfg["widgets"]["command"]["repo"] = repo
# Only add the layout entry if it isn't already there -- re-running must not
# stack duplicate tiles.
layout = cfg.setdefault("layout", [])
if not any(e.get("type") == "command" for e in layout if isinstance(e, dict)):
    idx = next((i for i, e in enumerate(layout)
                if isinstance(e, dict) and e.get("type") == "header"), -1)
    layout.insert(idx + 1, {"type": "command"})
    print("  layout: inserted the command tile after the header")
else:
    print("  layout: command tile already present")
with open(path, "w") as f:
    json.dump(cfg, f, indent=2); f.write("\n")
print(f"  widgets.command.repo = {repo}")
PY

# ── Token ──────────────────────────────────────────────────────────────────
step "GitHub token"
EXISTING_TOKEN="$(python3 -c '
import json,sys
try:
    with open(sys.argv[1]) as f: c = json.load(f)
    t = (c.get("command") or {}).get("githubToken","")
    print("" if (not t or t.startswith("YOUR_")) else t)
except Exception:
    print("")
' "$LCFG")"

if [ -n "$EXISTING_TOKEN" ]; then
  grn "A token is already configured -- leaving it alone"
  TOKEN="$EXISTING_TOKEN"
else
  echo "  Needs a PAT with read-only access to repo contents on $ORCH_REPO."
  echo "  Create at: https://github.com/settings/personal-access-tokens"
  printf '  Paste token (input hidden): '
  read -rs TOKEN
  echo
  [ -n "$TOKEN" ] || die "No token entered."
  python3 - "$LCFG" "$TOKEN" <<'PY'
import json, sys
path, tok = sys.argv[1], sys.argv[2]
with open(path) as f: cfg = json.load(f)
cfg.setdefault("command", {})["githubToken"] = tok
with open(path, "w") as f:
    json.dump(cfg, f, indent=2); f.write("\n")
PY
  grn "Token written to config.local.json"
fi

# config.local.json is gitignored, but permissions are the belt to that braces.
chmod 600 "$LCFG" 2>/dev/null || true

# ── Verify the token actually works ────────────────────────────────────────
# The whole point: fail here with a clear message rather than in the widget
# hours later with "GitHub rejected the token".
step "Verifying the token against $ORCH_REPO"
if [ "$SKIP_TOKEN_CHECK" = "1" ]; then
  ylw "SKIP_TOKEN_CHECK=1 -- skipping the live check"
else
  URL="https://api.github.com/repos/$ORCH_REPO/contents/facts/command.json?ref=main"
  CODE="$(curl -sS -o /tmp/jarvis-cmd-probe.json -w '%{http_code}' \
    -H 'Accept: application/vnd.github.raw+json' \
    -H "Authorization: Bearer $TOKEN" \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "$URL" || echo "000")"

  # config.json was written before this check ran, so a failure here leaves it
  # pointing at a repo that didn't work. Say so rather than leaving the user to
  # discover it -- re-running with the right value fixes it.
  verify_die() {
    red "FAILED: $*"
    ylw "config.json now points at $ORCH_REPO. Re-run with the correct repo to correct it:"
    ylw "  $0 \"$VAULT\" <owner>/<repo>"
    exit 1
  }

  case "$CODE" in
    200)
      # No f-strings here on purpose: an f-string expression cannot contain a
      # backslash before Python 3.12, and quoting through -c makes that easy to
      # hit by accident.
      ITEMS="$(python3 -c '
import json
try:
    d = json.load(open("/tmp/jarvis-cmd-probe.json"))
    n = len(d.get("items", []))
    print(str(n) + " item(s), generated_at " + str(d.get("generated_at", "?")))
except Exception as e:
    print("unparseable: " + str(e))
')"
      grn "OK -- command.json reachable: $ITEMS"
      ;;
    401|403) verify_die "GitHub returned $CODE. The token is invalid, expired, or lacks repo-contents read on $ORCH_REPO." ;;
    404)     verify_die "GitHub returned 404. Either $ORCH_REPO/facts/command.json doesn't exist, or the token can't see this private repo." ;;
    000)     verify_die "Could not reach GitHub. Check your network." ;;
    *)       verify_die "GitHub returned $CODE." ;;
  esac
  rm -f /tmp/jarvis-cmd-probe.json
fi

# ── Dataview ───────────────────────────────────────────────────────────────
step "Checking the Dataview plugin"
DV_DIR="$VAULT/.obsidian/plugins/dataview"
if [ ! -d "$DV_DIR" ]; then
  ylw "Dataview is not installed."
  ylw "  Obsidian -> Settings -> Community plugins -> Browse -> 'Dataview' -> Install + Enable"
elif [ -f "$DV_DIR/data.json" ] \
     && python3 -c '
import json,sys
d=json.load(open(sys.argv[1]))
sys.exit(0 if d.get("enableDataviewJs") else 1)
' "$DV_DIR/data.json" 2>/dev/null; then
  grn "Dataview installed, JavaScript Queries enabled"
else
  ylw "Dataview installed, but 'Enable JavaScript Queries' looks OFF."
  ylw "  Obsidian -> Settings -> Dataview -> turn ON 'Enable JavaScript Queries'"
  ylw "  Without it the dashboard renders as a plain code block."
fi

# ── Done ───────────────────────────────────────────────────────────────────
step "Done"
cat <<DONE
Open this note in Obsidian:

  $DEST_NAME/Jarvis Dashboard.md

Command sits directly under the header. On mobile it appears above the voice
widget, automatically, because widgets.command.repo is now set.

Still worth setting by hand in config.json if you want the session widgets:
  projects.mode / projects.rootPath   (defaults to ~/.claude/projects/)
DONE
