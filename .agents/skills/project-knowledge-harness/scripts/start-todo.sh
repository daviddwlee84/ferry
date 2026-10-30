#!/usr/bin/env bash
set -euo pipefail

# Begin work on an active TODO.md item by opening (or reusing) a td issue
# that points back at it. TODO.md is never modified: git stays the source of
# truth for intent, td only holds execution state. No-op without td.
#
# Compatibility: macOS system Bash 3.2.

usage() {
  cat <<'EOF'
Usage: start-todo.sh [OPTIONS] --title <SUBSTRING>

Start work on one active TODO.md item. When td is enabled, create a td issue
labelled `pkh` with the item's exact title (or reuse an open one), then
`td start` it. The issue id is printed on stdout.

The link is one-way: the td issue names its TODO item; TODO.md never stores
td ids (they are machine-local unless td-sync is set up). Without td this
script only confirms the match and exits 0.

Required:
  --title SUBSTRING     Match an active item whose Title contains SUBSTRING
                        (case-sensitive). Must match exactly one active item.

Options:
  --file FILE           TODO file (default: TODO.md)
  --no-start            Create/reuse the td issue but do not `td start` it
  --dry-run             Print the td commands instead of running them
  -h, --help            Show this help and exit

Environment:
  PKH_TD=auto|on|off    auto (default): use td if a td project resolves from
                        the current directory. on: fail if it does not.
                        off: never touch td.

Exit codes:
  0  item matched (td issue created/reused/started, or td not enabled)
  1  no match / multiple matches / td command failed
  2  usage error, or PKH_TD=on without a td project
EOF
}

title_substr=""
file="TODO.md"
do_start=1
dry_run=0

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --title) title_substr="$2"; shift 2 ;;
    --file) file="$2"; shift 2 ;;
    --no-start) do_start=0; shift ;;
    --dry-run) dry_run=1; shift ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$title_substr" ]; then
  echo "Error: --title is required" >&2
  usage >&2
  exit 2
fi

if [ ! -f "$file" ]; then
  echo "Error: file not found: $file" >&2
  exit 1
fi

src="$0"
while [ -L "$src" ]; do
  link="$(readlink "$src")"
  case "$link" in /*) src="$link" ;; *) src="$(dirname "$src")/$link" ;; esac
done
script_dir="$(cd "$(dirname "$src")" && pwd)"
# shellcheck source=_lib/todo-match.sh
. "$script_dir/_lib/todo-match.sh"
# shellcheck source=_lib/td-bridge.sh
. "$script_dir/_lib/td-bridge.sh"

pkh_todo_match "$file" "$title_substr"

if [ "$match_count" -eq 0 ]; then
  echo "Error: no active item title contains: $title_substr" >&2
  exit 1
fi
if [ "$match_count" -gt 1 ]; then
  echo "Error: $match_count active items match '$title_substr'; refine the substring" >&2
  exit 1
fi

rc=0
pkh_td_enabled || rc=$?
if [ "$rc" -eq 2 ]; then
  exit 2
fi
if [ "$rc" -ne 0 ]; then
  echo "Matched: $match_priority/$match_effort '$match_title'" >&2
  echo "td not enabled; nothing to do (TODO.md is unchanged)." >&2
  exit 0
fi

if hit="$(pkh_td_find "$match_title")"; then
  td_id="${hit%%	*}"
  td_status="${hit#*	}"
  echo "Reusing td issue $td_id ($td_status) for '$match_title'" >&2
else
  # Body: the canonical TODO line plus where it lives, so the issue stays
  # resolvable after TODO.md moves the item to ## Done.
  body="Source: $file ($match_priority/$match_effort)

$match_line"
  case "$match_description" in
    *"→ [research]("*)
      research="${match_description##*→ \[research\](}"
      research="${research%%)*}"
      body="$body

Research: $research"
      ;;
  esac
  body="$body

Managed by project-knowledge-harness: TODO.md is the source of truth; when
shipping, run promote-todo.sh in the same commit, then review this issue."

  td_priority="$(pkh_td_priority "$match_priority")"
  td_points="$(pkh_td_points "$match_effort")"

  if [ "$dry_run" -eq 1 ]; then
    echo "td create \"$match_title\" --labels $PKH_TD_LABEL --priority $td_priority --points $td_points --description-file -"
    printf '%s\n' "$body" | sed 's/^/  | /'
    [ "$do_start" -eq 1 ] && echo "td start <new-id>"
    exit 0
  fi

  created="$(printf '%s\n' "$body" |
    td create "$match_title" --labels "$PKH_TD_LABEL" \
      --priority "$td_priority" --points "$td_points" \
      --description-file - --json 2>/dev/null)" || {
    echo "Error: td create failed" >&2
    exit 1
  }
  td_id="$(printf '%s\n' "$created" |
    sed -n 's/^  "id": "\(td-[0-9a-zA-Z]*\)",*$/\1/p' | head -n 1)"
  if [ -z "$td_id" ]; then
    echo "Error: could not read the new issue id from td output" >&2
    exit 1
  fi
  td_status="open"
  echo "Created td issue $td_id for '$match_title'" >&2
fi

if [ "$do_start" -eq 1 ] && [ "$td_status" != "in_progress" ]; then
  if [ "$dry_run" -eq 1 ]; then
    echo "td start $td_id"
  else
    td start "$td_id" >&2 || { echo "Error: td start $td_id failed" >&2; exit 1; }
  fi
fi

printf '%s\n' "$td_id"
