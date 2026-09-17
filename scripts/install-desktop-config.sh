#!/bin/zsh
# Add the pocketcasts MCP server to Claude Desktop's config.
# Refuses to run while Claude Desktop is open: the app rewrites the file from
# memory and would clobber the edit.
set -eu

CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
PROJECT="$HOME/Developer/active/pocketcasts-mcp"
BIN="$PROJECT/.venv/bin/pocketcasts-mcp"

# Capture first, then test (pipefail + grep -q would otherwise invert the guard).
_procs=$(ps -Ao comm= || true)
if printf '%s\n' "$_procs" | grep -qE '(^|/)Claude$'; then
  echo "Claude Desktop is running. Quit it (Cmd-Q), run this again, then relaunch." >&2
  exit 1
fi
[ -x "$BIN" ] || { echo "Server binary missing at $BIN; run 'uv sync' in $PROJECT first." >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "Config not found at $CONFIG" >&2; exit 1; }

BACKUP="$CONFIG.bak.$(date +%Y%m%d-%H%M%S)"
cat "$CONFIG" > "$BACKUP"

TMP="$(mktemp)"
CONFIG="$CONFIG" BIN="$BIN" python3 - > "$TMP" <<'PY'
import json, os
cfg = json.load(open(os.environ["CONFIG"]))
cfg.setdefault("mcpServers", {})["pocketcasts"] = {"command": os.environ["BIN"], "args": []}
print(json.dumps(cfg, indent=2))
PY
cat "$TMP" > "$CONFIG"
rm -f "$TMP"
echo "Added mcpServers.pocketcasts -> $BIN"
echo "Backup: $BACKUP"
echo "Now relaunch Claude Desktop."
