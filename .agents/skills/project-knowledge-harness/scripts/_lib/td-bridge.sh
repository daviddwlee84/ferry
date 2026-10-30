# shellcheck shell=bash
# Optional bridge to td (https://github.com/marcus/td). Sourced, not executed.
# Compatibility: macOS system Bash 3.2.
#
# Contract (see references/integrating-trackers.md):
#   - TODO.md / backlog/ / pitfalls/ in git stay the source of truth.
#   - td holds execution state only. A td issue points at its TODO item
#     (label `pkh` + identical title); TODO.md never stores td ids.
#   - Every helper is a silent no-op when td is unavailable.
#
# Mode comes from PKH_TD=auto|on|off (default auto). Callers may override it
# by setting pkh_td_mode before calling pkh_td_enabled.

PKH_TD_LABEL="pkh"

# pkh_td_enabled: return 0 when td should be used in the current directory.
# auto: td is installed AND resolves a project from cwd (worktrees and
#       .td-root included). on: same, but an unavailable td is an error (rc 2).
pkh_td_enabled() {
  local mode="${pkh_td_mode:-${PKH_TD:-auto}}"
  case "$mode" in
    off) return 1 ;;
    auto|on) ;;
    *) echo "Error: PKH_TD must be auto, on, or off (got '$mode')" >&2; return 2 ;;
  esac
  if command -v td >/dev/null 2>&1 && td info >/dev/null 2>&1; then
    return 0
  fi
  if [ "$mode" = "on" ]; then
    echo "Error: PKH_TD=on but no td project resolves from $(pwd) (run 'td init')" >&2
    return 2
  fi
  return 1
}

# pkh_td_list_open: print "id<TAB>status<TAB>title" for every non-closed
# issue labelled pkh. Prints nothing (and warns) without jq/python3.
pkh_td_list_open() {
  local json
  json="$(td list --all --json -l "$PKH_TD_LABEL" 2>/dev/null)" || return 0
  [ -n "$json" ] || return 0
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$json" |
      jq -r '.[] | select(.status != "closed") | [.id, .status, .title] | @tsv'
  elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 -I -c '
import json, sys
for i in json.load(sys.stdin):
    if i.get("status") != "closed":
        print("\t".join([i["id"], i["status"], i["title"]]))
'
  else
    echo "Warning: td bridge needs jq or python3 to read td output; skipping" >&2
  fi
}

# pkh_td_find TITLE: print "id<TAB>status" of the first non-closed pkh issue
# whose title equals TITLE exactly. Returns 1 when none.
pkh_td_find() {
  local want="$1" id status title tab
  tab="$(printf '\t')"
  while IFS="$tab" read -r id status title; do
    if [ -n "$id" ] && [ "$title" = "$want" ]; then
      printf '%s\t%s\n' "$id" "$status"
      return 0
    fi
  done < <(pkh_td_list_open)
  return 1
}

# pkh_td_priority P1|P2|P3|P?  ->  td priority
pkh_td_priority() {
  case "$1" in
    P1|P2|P3) printf '%s' "$1" ;;
    *) printf 'P4' ;;
  esac
}

# pkh_td_points S|M|L|XL  ->  Fibonacci story points
pkh_td_points() {
  case "$1" in
    S) printf '1' ;;
    M) printf '3' ;;
    L) printf '8' ;;
    XL) printf '13' ;;
    *) printf '0' ;;
  esac
}
