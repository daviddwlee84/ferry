#!/usr/bin/env bash
# test_contracts.sh — exit-code contracts for pueue-job-queue scripts.
#
# Tests the pure CLI contract (exit codes, --help availability) without a
# running daemon. Some checks REQUIRE a daemon; we shut down our isolated
# fixture between paths.
#
# Run:
#   bash skills/local/pueue-job-queue/tests/test_contracts.sh
# Skipped automatically if `pueue` is not on PATH.

set -uo pipefail

SKILL=$(cd "$(dirname "$0")/.." && pwd)
SCRIPTS="$SKILL/scripts"

if ! command -v pueue >/dev/null 2>&1; then
  echo "skip: pueue CLI not on PATH"
  exit 0
fi

FAIL=0

assert_exit() {
  local label="$1" expected="$2" actual="$3"
  if [ "$actual" -eq "$expected" ]; then
    printf '  ok: %-50s exit=%d\n' "$label" "$actual"
  else
    printf '  FAIL: %-50s expected=%d actual=%d\n' "$label" "$expected" "$actual" >&2
    FAIL=1
  fi
}

echo "== --help on every script returns 0 =="

for s in check-daemon.sh submit.sh wait.py submit-dag.py cleanup.sh; do
  bash "$SCRIPTS/$s" --help >/dev/null 2>&1
  rc=$?
  if [ "$s" = "wait.py" ] || [ "$s" = "submit-dag.py" ]; then
    # python scripts (uv run) — argparse exits 0 for --help
    "$SCRIPTS/$s" --help >/dev/null 2>&1
    rc=$?
  fi
  assert_exit "$s --help" 0 "$rc"
done

echo
echo "== submit.sh with no command returns 1 =="
bash "$SCRIPTS/submit.sh" >/dev/null 2>&1
assert_exit "submit.sh (no args)" 1 "$?"

echo
echo "== wait.py with no selectors returns 1 =="
"$SCRIPTS/wait.py" >/dev/null 2>&1
assert_exit "wait.py (no selectors)" 1 "$?"

echo
echo "== submit-dag.py with missing spec returns 1 =="
"$SCRIPTS/submit-dag.py" /tmp/no-such-file-x9.yaml >/dev/null 2>&1
assert_exit "submit-dag.py (missing file)" 1 "$?"

echo
echo "== cleanup.sh bad flag returns 1 =="
bash "$SCRIPTS/cleanup.sh" --bogus-flag >/dev/null 2>&1
assert_exit "cleanup.sh (bad flag)" 1 "$?"

echo
echo "== check-daemon.sh w/ unreachable daemon returns 3 =="
# Point at a config with a path nothing is listening on.
TMPCFG=$(mktemp -d)
cat > "$TMPCFG/pueue.yml" <<EOF
shared:
  pueue_directory: $TMPCFG/state
  use_unix_socket: true
EOF
mkdir -p "$TMPCFG/state"
PUEUE_CONFIG_PATH="$TMPCFG/pueue.yml" bash "$SCRIPTS/check-daemon.sh" >/dev/null 2>&1
assert_exit "check-daemon (no daemon)" 3 "$?"
rm -rf "$TMPCFG"

if [ "$FAIL" -eq 0 ]; then
  echo
  echo "all contract tests passed"
fi
exit "$FAIL"
