#!/bin/bash
#
# Adds the pocketcasts MCP server to Claude Desktop's config.
#
# Refuses to run while Claude Desktop is open: the app rewrites the file from
# memory and would clobber the edit. Keeps a timestamped backup of the config
# next to it.

set -eu

readonly CLAUDE_DIR="${HOME}/Library/Application Support/Claude"
readonly CONFIG="${CLAUDE_DIR}/claude_desktop_config.json"
readonly PROJECT="${HOME}/Developer/active/pocketcasts-mcp"
readonly BIN="${PROJECT}/.venv/bin/pocketcasts-mcp"
# The checkout this script lives in, for the version line; PROJECT is where
# the installed server is expected, which is the same tree outside tests.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO
readonly VERSION_PY="${REPO}/src/pocketcasts_mcp/version.py"

#######################################
# Prints an error message to stderr.
# Arguments:
#   Message words, joined with spaces.
# Outputs:
#   Writes the message to stderr.
#######################################
err() {
  echo "$*" >&2
}

#######################################
# Prints the version of the checkout, the same string the server logs.
# Globals:
#   VERSION_PY
#   REPO_VERSION - when set, printed as is (tests pin it to a fixed string).
# Outputs:
#   One line: the git describe string, or "unknown".
#######################################
repo_version() {
  local v
  v="$(python3 -I "${VERSION_PY}" 2> /dev/null)" && [[ -n "${v}" ]] \
    || v="unknown"
  printf '%s\n' "${v}"
}

#######################################
# Checks whether the Claude Desktop app is running.
# Returns:
#   0 if a process named Claude is running, non-zero otherwise.
#######################################
claude_desktop_running() {
  local procs
  # Capture first, then test (pipefail + grep -q would otherwise invert the
  # guard).
  procs="$(ps -Ao comm= || true)"
  printf '%s\n' "${procs}" | grep -qE '(^|/)Claude$'
}

#######################################
# Writes the config with mcpServers.pocketcasts set, as JSON, to stdout.
# Arguments:
#   Path of the current config.
#   Path of the server binary.
# Outputs:
#   Writes the updated config to stdout.
#######################################
render_config() {
  python3 - "$1" "$2" << 'PY'
import json
import sys

config_path, binary = sys.argv[1], sys.argv[2]
with open(config_path) as f:
    cfg = json.load(f)
cfg.setdefault("mcpServers", {})["pocketcasts"] = {
    "command": binary,
    "args": [],
}
print(json.dumps(cfg, indent=2))
PY
}

main() {
  err "pocketcasts-mcp $(repo_version)"
  if claude_desktop_running; then
    err "Claude Desktop is running. Quit it (Cmd-Q), run this again," \
      "then relaunch."
    exit 1
  fi
  if [[ ! -x "${BIN}" ]]; then
    err "Server binary missing at ${BIN}; run 'uv sync' in ${PROJECT} first."
    exit 1
  fi
  if [[ ! -f "${CONFIG}" ]]; then
    err "Config not found at ${CONFIG}"
    exit 1
  fi

  local backup
  backup="${CONFIG}.bak.$(date +%Y%m%d-%H%M%S)"
  cat "${CONFIG}" > "${backup}"

  local tmp
  tmp="$(mktemp)"
  render_config "${CONFIG}" "${BIN}" > "${tmp}"
  cat "${tmp}" > "${CONFIG}"
  rm -f "${tmp}"
  echo "Added mcpServers.pocketcasts -> ${BIN}"
  echo "Backup: ${backup}"
  echo "Now relaunch Claude Desktop."
}

main "$@"
