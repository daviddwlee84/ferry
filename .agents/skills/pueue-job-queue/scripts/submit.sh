#!/usr/bin/env bash
# submit.sh — submit ONE pueue task and emit a JSON record on stdout.
#
# Wraps `pueue add --print-task-id` with defensive id parsing, group
# autocreate (pueue 4.x doesn't autocreate), and a clean structured
# response so the agent can pipe the result through `jq -r .task_id`.
#
# Bash 3.2 compatible (works on stock macOS).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: submit.sh [OPTIONS] -- <COMMAND>...

Submit one task to pueued. Emit a JSON record on stdout describing the
task (id, group, label, dependencies, flags). Diagnostics go to stderr.

Options:
  --label TEXT            Human-readable label (saved on the task).
  --group GROUP           Target group. Auto-created if missing (pueue 4.x
                          rejects --group <missing>; this script runs
                          `pueue group add` first).
  --after ID              Add a dependency on task ID. Repeatable.
  --immediate             Start immediately, ignoring queue ordering.
  --stashed               Submit in Stashed state (won't auto-start).
  --delay STR             Defer enqueue (e.g. "10min", "2h", "tomorrow 9am").
  --priority N            Higher = sooner.
  --working-dir PATH      Run the command in PATH (defaults to current cwd).
  --escape                Pass `pueue add --escape` to the underlying call.
  --dry-run               Print the planned `pueue add` invocation without
                          running it. Useful for review.
  --help, -h              Show this help and exit.

After `--`, the rest of the args become the command. Quote the WHOLE
command as a single shell-string when it contains operators or quotes:

  submit.sh --label hi -- 'sleep 60 && echo done'

Pueue joins the COMMAND args back together and re-shells. `bash -c
'sleep 60'` does NOT preserve the inner quotes — see SKILL.md gotchas.

Examples:
  submit.sh --label train-1 --group ml -- python train.py --seed 1
  submit.sh --after 17 --after 18 -- python eval.py
  ID=$(submit.sh -- 'sleep 5' | jq -r .task_id)

Exit codes:
  0  task submitted
  1  invalid arguments
  2  pueue CLI not installed
  3  `pueue add` failed (bad dependency id, etc.)
  4  daemon unreachable
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit "${2:-1}"; }

LABEL=""
GROUP=""
AFTER_IDS=()
IMMEDIATE=0
STASHED=0
DELAY=""
PRIORITY=""
WORKDIR=""
ESCAPE=0
DRY_RUN=0
CMD=()

while [ $# -gt 0 ]; do
  case "$1" in
    --label)        [ $# -ge 2 ] || die "--label requires a value" 1; LABEL="$2"; shift 2 ;;
    --group)        [ $# -ge 2 ] || die "--group requires a value" 1; GROUP="$2"; shift 2 ;;
    --after)        [ $# -ge 2 ] || die "--after requires a value" 1; AFTER_IDS+=("$2"); shift 2 ;;
    --immediate)    IMMEDIATE=1; shift ;;
    --stashed)      STASHED=1; shift ;;
    --delay)        [ $# -ge 2 ] || die "--delay requires a value" 1; DELAY="$2"; shift 2 ;;
    --priority)     [ $# -ge 2 ] || die "--priority requires a value" 1; PRIORITY="$2"; shift 2 ;;
    --working-dir)  [ $# -ge 2 ] || die "--working-dir requires a value" 1; WORKDIR="$2"; shift 2 ;;
    --escape)       ESCAPE=1; shift ;;
    --dry-run)      DRY_RUN=1; shift ;;
    --help|-h)      usage; exit 0 ;;
    --)             shift; while [ $# -gt 0 ]; do CMD+=("$1"); shift; done ;;
    -*)             die "unknown flag: $1 (try --help)" 1 ;;
    *)              CMD+=("$1"); shift ;;
  esac
done

[ "${#CMD[@]}" -gt 0 ] || die "no command given (use -- before the command)" 1

command -v pueue >/dev/null 2>&1 || { log "pueue not installed"; exit 2; }

# In dry-run mode, do NOT touch the daemon — the agent expects this to be
# a pure plan rendering. Skip both the reachability probe and the group
# probe/create.
if [ "$DRY_RUN" = "0" ]; then
  # Probe daemon. `pueue status --json` returns non-zero when unreachable.
  if ! pueue status --json >/dev/null 2>&1; then
    log "pueued unreachable. Start with: pueued -d   (or use scripts/check-daemon.sh --start)"
    exit 4
  fi

  # Auto-create group if requested and missing.
  if [ -n "$GROUP" ]; then
    GROUPS_JSON=$(pueue group --json)
    HAS_GROUP=$(PUEUE_GROUP_NAME="$GROUP" python3 -c '
import json, os, sys
d = json.loads(sys.stdin.read())
print("yes" if os.environ["PUEUE_GROUP_NAME"] in d else "no")
' <<<"$GROUPS_JSON")
    if [ "$HAS_GROUP" = "no" ]; then
      log "group '$GROUP' missing — creating."
      pueue group add "$GROUP" >&2 || { log "failed to create group $GROUP"; exit 3; }
    fi
  fi
fi

# Build pueue add invocation.
PUEUE_ARGS=("add" "--print-task-id")
[ -n "$LABEL" ]     && PUEUE_ARGS+=("--label" "$LABEL")
[ -n "$GROUP" ]     && PUEUE_ARGS+=("--group" "$GROUP")
[ -n "$DELAY" ]     && PUEUE_ARGS+=("--delay" "$DELAY")
[ -n "$PRIORITY" ]  && PUEUE_ARGS+=("--priority" "$PRIORITY")
[ -n "$WORKDIR" ]   && PUEUE_ARGS+=("--working-directory" "$WORKDIR")
[ "$IMMEDIATE" = "1" ] && PUEUE_ARGS+=("--immediate")
[ "$STASHED" = "1" ]   && PUEUE_ARGS+=("--stashed")
[ "$ESCAPE" = "1" ]    && PUEUE_ARGS+=("--escape")
for a in "${AFTER_IDS[@]+"${AFTER_IDS[@]}"}"; do
  PUEUE_ARGS+=("--after" "$a")
done
PUEUE_ARGS+=("--" "${CMD[@]}")

if [ "$DRY_RUN" = "1" ]; then
  log "[dry-run] pueue ${PUEUE_ARGS[*]}"
  AFTER_JSON=$(printf '%s\n' "${AFTER_IDS[@]+"${AFTER_IDS[@]}"}" | python3 -c "import json,sys;print(json.dumps([int(x) for x in sys.stdin.read().split() if x.strip()]))")
  python3 - "$LABEL" "$GROUP" "$AFTER_JSON" "$IMMEDIATE" "$STASHED" <<'PY'
import json, sys
label, group, after_json, immediate, stashed = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
print(json.dumps({
    "task_id": None,
    "label": label or None,
    "group": group or "default",
    "after": json.loads(after_json),
    "immediate": immediate == "1",
    "stashed": stashed == "1",
    "dry_run": True,
}))
PY
  exit 0
fi

# Run pueue add. --print-task-id sends the bare integer to stdout on success;
# on failure, pueue exits non-zero and writes a diagnostic to stdout (yes,
# stdout, not stderr — pueue 4.0.2 quirk).
ADD_OUT=""
set +e
ADD_OUT=$(pueue "${PUEUE_ARGS[@]}" 2>&1)
ADD_EXIT=$?
set -e

if [ $ADD_EXIT -ne 0 ]; then
  log "pueue add failed: $ADD_OUT"
  exit 3
fi

# Parse the trailing integer (defensive against future format changes).
TASK_ID=$(printf '%s' "$ADD_OUT" | tr -dc '0-9\n' | tail -n 1 | head -n 1)
if ! printf '%s' "$TASK_ID" | grep -qE '^[0-9]+$'; then
  # Fallback: query by label.
  if [ -n "$LABEL" ]; then
    log "couldn't parse task id from pueue output ('$ADD_OUT'); falling back to label lookup"
    TASK_ID=$(pueue status --json | python3 -c "
import json,sys,os
d=json.load(sys.stdin)
label=os.environ['LABEL']
matches=sorted(int(k) for k,v in d['tasks'].items() if v.get('label')==label)
print(matches[-1] if matches else '')
" LABEL="$LABEL")
  fi
  if ! printf '%s' "$TASK_ID" | grep -qE '^[0-9]+$'; then
    log "could not determine task id from pueue add output"
    exit 3
  fi
fi

GROUP_OUT="${GROUP:-default}"
AFTER_JSON=$(printf '%s\n' "${AFTER_IDS[@]+"${AFTER_IDS[@]}"}" | python3 -c "import json,sys;print(json.dumps([int(x) for x in sys.stdin.read().split() if x.strip()]))")

log "submitted task $TASK_ID (group=$GROUP_OUT${LABEL:+, label=$LABEL}${AFTER_IDS[*]+, after=[${AFTER_IDS[*]}]})"

python3 - "$TASK_ID" "$LABEL" "$GROUP_OUT" "$AFTER_JSON" "$IMMEDIATE" "$STASHED" <<'PY'
import json, sys
task_id, label, group, after_json, immediate, stashed = sys.argv[1:7]
print(json.dumps({
    "task_id": int(task_id),
    "label": label or None,
    "group": group,
    "after": json.loads(after_json),
    "immediate": immediate == "1",
    "stashed": stashed == "1",
}))
PY
