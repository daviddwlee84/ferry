#!/usr/bin/env bash
# cleanup.sh — prune pueue task history, empty groups, and old log files.
#
# Pueue's task list grows unbounded — `pueue status --json` walks the whole
# table and gets slower over time. Empty groups left behind from
# `--isolated-group` runs accumulate too. This script wraps the safe
# subset of cleanup operations and emits a JSON report of what it touched.
#
# Bash 3.2 compatible (works on stock macOS).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: cleanup.sh [OPTIONS]

Prune pueue state. Safe by default — only `pueue clean` (which leaves
running/queued tasks untouched) runs unless other flags are given.

Options:
  --successful-only      Only clean tasks where result==Success. Failures
                         stay around for inspection. Recommended.
  --group GROUP          Limit `pueue clean` to one group.
  --remove-empty-groups  After cleaning, drop groups with zero tasks.
                         Skips `default` (cannot be removed). Skips groups
                         currently holding non-Done tasks.
  --logs-older-than N    Delete log files older than N days from the
                         platform log dir (~/Library/Application Support/
                         pueue/logs on macOS, ~/.local/share/pueue/logs on
                         Linux). Files for ids still in `pueue status` are
                         preserved.
  --dry-run              Print what would be done; touch nothing.
  --json                 Emit JSON to stdout (default; flag kept for symmetry).
  --help, -h             Show this help and exit.

Examples:
  cleanup.sh --successful-only --remove-empty-groups
  cleanup.sh --group dag-abc12345 --logs-older-than 30
  cleanup.sh --dry-run --successful-only --logs-older-than 7

Exit codes:
  0  success (or dry-run)
  1  invalid arguments
  2  pueue CLI not installed
  3  pueued unreachable
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit "${2:-1}"; }

SUCCESS_ONLY=0
GROUP=""
REMOVE_EMPTY=0
LOGS_DAYS=""
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --successful-only)     SUCCESS_ONLY=1; shift ;;
    --group)               [ $# -ge 2 ] || die "--group requires a value" 1
                           GROUP="$2"; shift 2 ;;
    --remove-empty-groups) REMOVE_EMPTY=1; shift ;;
    --logs-older-than)     [ $# -ge 2 ] || die "--logs-older-than requires N" 1
                           LOGS_DAYS="$2"; shift 2 ;;
    --dry-run)             DRY_RUN=1; shift ;;
    --json)                shift ;;
    --help|-h)             usage; exit 0 ;;
    *)                     die "unknown flag: $1 (try --help)" 1 ;;
  esac
done

command -v pueue >/dev/null 2>&1 || { log "pueue not installed"; exit 2; }

if ! pueue status --json >/dev/null 2>&1; then
  log "pueued unreachable. Start with: pueued -d"
  exit 3
fi

# --- Detect platform log dir.
PLATFORM=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$PLATFORM" in
  darwin) LOG_DIR="${HOME}/Library/Application Support/pueue/logs" ;;
  linux)  LOG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pueue/logs" ;;
  *)      LOG_DIR="" ;;
esac

# --- Snapshot pre-cleanup state.
PRE_STATUS=$(pueue status --json)

# --- 1. Clean tasks (pueue handles its own log files for cleaned tasks).
CLEANED_IDS_JSON='[]'
if [ "$SUCCESS_ONLY" = "1" ] || [ -n "$GROUP" ] || [ "$DRY_RUN" = "0" ]; then
  CLEAN_ARGS=("clean")
  [ "$SUCCESS_ONLY" = "1" ] && CLEAN_ARGS+=("--successful-only")
  [ -n "$GROUP" ]           && CLEAN_ARGS+=("--group" "$GROUP")

  # Compute which ids would be cleaned (pueue clean has no --dry-run).
  CLEANED_IDS_JSON=$(printf '%s' "$PRE_STATUS" | \
    SUCC="$SUCCESS_ONLY" GRP="$GROUP" python3 -c '
import json, os, sys
d = json.loads(sys.stdin.read())
succ_only = os.environ["SUCC"] == "1"
group = os.environ["GRP"]
ids = []
for tid, t in d.get("tasks", {}).items():
    status = t.get("status") or {}
    if "Done" not in status: continue
    if group and t.get("group") != group: continue
    if succ_only and status["Done"].get("result") != "Success": continue
    ids.append(int(tid))
print(json.dumps(sorted(ids)))
')
  if [ "$DRY_RUN" = "0" ]; then
    pueue "${CLEAN_ARGS[@]}" >/dev/null 2>&1 || warn "pueue clean failed"
  fi
fi

# --- 2. Remove empty non-default groups.
REMOVED_GROUPS_JSON='[]'
if [ "$REMOVE_EMPTY" = "1" ]; then
  POST_STATUS=$(pueue status --json)
  REMOVED_GROUPS_JSON=$(printf '%s' "$POST_STATUS" | DRY="$DRY_RUN" python3 -c '
import json, os, subprocess, sys
d = json.loads(sys.stdin.read())
dry = os.environ["DRY"] == "1"
removed = []
groups = d.get("groups", {})
tasks = d.get("tasks", {})
# Count tasks per group
counts = {}
for t in tasks.values():
    counts[t.get("group", "default")] = counts.get(t.get("group", "default"), 0) + 1
for g in groups:
    if g == "default": continue
    if counts.get(g, 0) > 0: continue
    if not dry:
        r = subprocess.run(["pueue", "group", "remove", g],
                           capture_output=True, text=True)
        if r.returncode == 0:
            removed.append(g)
    else:
        removed.append(g)
print(json.dumps(sorted(removed)))
')
fi

# --- 3. Delete old log files (only those whose id is no longer in status).
DELETED_LOGS_JSON='[]'
if [ -n "$LOGS_DAYS" ] && [ -n "$LOG_DIR" ] && [ -d "$LOG_DIR" ]; then
  POST_STATUS=$(pueue status --json)
  DELETED_LOGS_JSON=$(LD="$LOG_DIR" DAYS="$LOGS_DAYS" DRY="$DRY_RUN" \
    POST="$POST_STATUS" python3 -c '
import json, os, sys, time
import pathlib
log_dir = pathlib.Path(os.environ["LD"])
days = int(os.environ["DAYS"])
dry = os.environ["DRY"] == "1"
status = json.loads(os.environ["POST"])
live_ids = set(status.get("tasks", {}).keys())
cutoff = time.time() - days * 86400
deleted = []
for f in log_dir.glob("*.log"):
    stem = f.stem
    if stem in live_ids:
        continue  # log file for a still-tracked task; skip
    try:
        if f.stat().st_mtime > cutoff:
            continue
    except OSError:
        continue
    if not dry:
        try: f.unlink()
        except OSError: continue
    deleted.append(str(f))
print(json.dumps(sorted(deleted)))
')
fi

# --- 4. Emit report.
PRE_STATUS_FOR_RUNNING="$PRE_STATUS" \
CLEANED="$CLEANED_IDS_JSON" \
REMOVED="$REMOVED_GROUPS_JSON" \
DELETED="$DELETED_LOGS_JSON" \
DRY="$DRY_RUN" \
LOG_DIR_OUT="$LOG_DIR" \
python3 <<'PY'
import json, os
pre = json.loads(os.environ["PRE_STATUS_FOR_RUNNING"])
running = sorted(int(tid) for tid, t in pre.get("tasks", {}).items()
                 if isinstance(t.get("status"), dict) and "Done" not in t["status"])
print(json.dumps({
    "cleaned_tasks": json.loads(os.environ["CLEANED"]),
    "removed_groups": json.loads(os.environ["REMOVED"]),
    "deleted_logs": json.loads(os.environ["DELETED"]),
    "kept_running": running,
    "log_dir": os.environ["LOG_DIR_OUT"],
    "dry_run": os.environ["DRY"] == "1",
}))
PY
