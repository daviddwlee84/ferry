#!/usr/bin/env bash
# check-daemon.sh — verify pueued is reachable and emit a JSON health summary.
#
# Bash 3.2 compatible (works on stock macOS).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: check-daemon.sh [OPTIONS]

Probe whether `pueued` is running and the `pueue` client can reach it.
Emit a JSON summary to stdout; prose hints to stderr.

Options:
  --start            Auto-launch `pueued -d` if the daemon is unreachable.
                     Re-checks afterward. No-op if already running.
  --json             Emit JSON to stdout (default; flag kept for symmetry).
  --help, -h         Show this help and exit.

Examples:
  check-daemon.sh
  check-daemon.sh --start | jq -r .daemon_running

Exit codes:
  0  daemon healthy
  2  `pueue` CLI not on PATH
  3  daemon unreachable (and --start was not given, or start failed)
  4  client/daemon version mismatch (rare; restart pueued to fix)
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit "${2:-1}"; }

START=0

while [ $# -gt 0 ]; do
  case "$1" in
    --start) START=1; shift ;;
    --json)  shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown flag: $1 (try --help)" 1 ;;
  esac
done

# --- 1. Is the pueue CLI installed?
if ! command -v pueue >/dev/null 2>&1; then
  log "pueue CLI not found on PATH. Install:"
  log "  macOS:   brew install pueue"
  log "  Linux:   cargo install --locked pueue   (or distro package)"
  log "  Windows: see https://github.com/Nukesor/pueue#installation"
  printf '{"daemon_running":false,"error":"pueue_not_installed"}\n'
  exit 2
fi

CLIENT_VERSION=$(pueue --version 2>/dev/null | awk '{print $2}')

# --- 2. Detect platform + log dir.
PLATFORM=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$PLATFORM" in
  darwin)  LOG_DIR="${HOME}/Library/Application Support/pueue/logs" ;;
  linux)   LOG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pueue/logs" ;;
  *)       LOG_DIR="(unknown for $PLATFORM — check pueued config)" ;;
esac

# --- 3. Probe the daemon. `pueue status --json` returns non-zero if unreachable.
probe_daemon() {
  pueue status --json 2>/dev/null
}

STATUS_JSON=$(probe_daemon || true)

if [ -z "$STATUS_JSON" ]; then
  if [ "$START" = "1" ]; then
    log "daemon unreachable — attempting: pueued -d"
    if ! command -v pueued >/dev/null 2>&1; then
      log "pueued binary not on PATH (pueue CLI was, but daemon binary is separate)"
      printf '{"daemon_running":false,"error":"pueued_not_installed"}\n'
      exit 3
    fi
    # Detach so we don't block. nohup avoids inheriting stop-on-terminal-close.
    nohup pueued -d </dev/null >/dev/null 2>&1 &
    # Give it a moment to bind the socket.
    sleep 1
    STATUS_JSON=$(probe_daemon || true)
    if [ -z "$STATUS_JSON" ]; then
      log "started pueued but it's still unreachable. Check ~/Library/Application Support/pueue/ (macOS) or ~/.local/share/pueue/ (Linux) for stale socket or config errors."
      printf '{"daemon_running":false,"error":"start_failed"}\n'
      exit 3
    fi
    log "pueued started."
  else
    log "pueued not running. Start with: pueued -d   (or rerun with --start)"
    printf '{"daemon_running":false,"error":"daemon_unreachable","platform":"%s","client_version":"%s"}\n' \
      "$PLATFORM" "$CLIENT_VERSION"
    exit 3
  fi
fi

# --- 4. Emit health summary as JSON.
# We use python3 to compose the JSON safely from the status payload.
PUEUE_STATUS_JSON="$STATUS_JSON" \
PUEUE_CLIENT_VERSION="$CLIENT_VERSION" \
PUEUE_PLATFORM="$PLATFORM" \
PUEUE_LOG_DIR="$LOG_DIR" \
python3 <<'PY'
import json, os
status = json.loads(os.environ["PUEUE_STATUS_JSON"])
groups = status.get("groups", {})
default_group = groups.get("default", {})
out = {
    "daemon_running": True,
    "client_version": os.environ["PUEUE_CLIENT_VERSION"],
    "platform": os.environ["PUEUE_PLATFORM"],
    "log_dir": os.environ["PUEUE_LOG_DIR"],
    "default_group": {
        "parallel_tasks": default_group.get("parallel_tasks"),
        "status": default_group.get("status"),
    },
    "groups": {name: {"parallel_tasks": g.get("parallel_tasks"), "status": g.get("status")}
               for name, g in groups.items()},
    "task_count": len(status.get("tasks", {})),
}
print(json.dumps(out))
PY
