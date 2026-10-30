#!/usr/bin/env bash
set -euo pipefail

# Move a TODO.md item from its active lane to `## Done` with the dated
# project-knowledge-harness "Done" syntax. Re-runs the validator after
# editing so syntax drift is caught immediately.
#
# Compatibility: macOS system Bash 3.2.

usage() {
  cat <<'EOF'
Usage: promote-todo.sh [OPTIONS] --title <SUBSTRING> --summary <SHIPPED SUMMARY>

Move an active TODO item to the `## Done` section, rewriting it as:
  - ✅ [YYYY-MM-DD] [P#/Effort] Title — <summary>

The script edits TODO.md in-place. If validation fails after the edit,
the original file is restored.

td: when td is enabled (PKH_TD=auto|on|off, default auto) and an open issue
labelled `pkh` has the same title, a review/harvest hint is printed to
stderr. The td issue is never closed by this script.

Required:
  --title SUBSTRING     Match an active item whose Title contains SUBSTRING
                        (case-sensitive). Must match exactly one active item.
  --summary TEXT        One-line summary of what shipped.

Options:
  --file FILE           TODO file (default: TODO.md)
  --date YYYY-MM-DD     Override completion date (default: today, UTC)
  --dry-run             Print the new file to stdout, do not modify
  --validator PATH      Path to todo-kanban.sh (default: sibling script,
                        falls back to skills/.../todo-kanban.sh)
  -h, --help            Show this help and exit

Exit codes:
  0  item promoted (and validation passed)
  1  no match / multiple matches / validation failed
  2  usage error
EOF
}

title_substr=""
summary=""
file="TODO.md"
date_override=""
dry_run=0
validator=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --title) title_substr="$2"; shift 2 ;;
    --summary) summary="$2"; shift 2 ;;
    --file) file="$2"; shift 2 ;;
    --date) date_override="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --validator) validator="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$title_substr" ] || [ -z "$summary" ]; then
  echo "Error: --title and --summary are required" >&2
  usage >&2
  exit 2
fi

if [ ! -f "$file" ]; then
  echo "Error: file not found: $file" >&2
  exit 1
fi

# Resolve symlinks (repos often link scripts/promote-todo.sh to the skill) so
# _lib/ is found next to the real file.
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

if [ -z "$validator" ]; then
  if [ -x "$script_dir/todo-kanban.sh" ]; then
    validator="$script_dir/todo-kanban.sh"
  fi
fi

if [ -n "$date_override" ]; then
  if ! [[ "$date_override" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "Error: --date must be YYYY-MM-DD" >&2
    exit 2
  fi
  today="$date_override"
else
  today="$(date -u +%Y-%m-%d)"
fi

# Find the matching active item (shared parser in _lib/todo-match.sh).
pkh_todo_match "$file" "$title_substr"

if [ "$match_count" -eq 0 ]; then
  echo "Error: no active item title contains: $title_substr" >&2
  exit 1
fi
if [ "$match_count" -gt 1 ]; then
  echo "Error: $match_count active items match '$title_substr'; refine the substring" >&2
  exit 1
fi

new_done_line="- ✅ [$today] [$match_priority/$match_effort] $match_title — $summary"

# Build the new file: drop the matched line; insert new_done_line right after `## Done` header.
tmp="$(mktemp -t promote-todo.XXXXXX)"
trap 'rm -f "$tmp"' EXIT

cur_lane=""
inserted=0
ln=0
while IFS= read -r line || [ -n "$line" ]; do
  ln=$(( ln + 1 ))
  if [ "$ln" -eq "$match_line_no" ]; then
    continue   # drop the matched active item
  fi
  printf '%s\n' "$line" >> "$tmp"
  if [ "$inserted" -eq 0 ] && [ "$line" = "## Done" ]; then
    printf '\n%s\n' "$new_done_line" >> "$tmp"
    inserted=1
  fi
done < "$file"

if [ "$inserted" -eq 0 ]; then
  echo "Error: could not find '## Done' heading in $file" >&2
  exit 1
fi

if [ "$dry_run" -eq 1 ]; then
  cat "$tmp"
  exit 0
fi

# Backup, swap, validate.
backup="$file.promote-bak.$$"
cp "$file" "$backup"
mv "$tmp" "$file"
trap - EXIT

if [ -n "$validator" ] && [ -x "$validator" ]; then
  if ! "$validator" --validate-only "$file" >/dev/null 2>&1; then
    echo "Error: validation failed after edit; restoring original $file" >&2
    "$validator" --validate-only "$file" >&2 || true
    mv "$backup" "$file"
    exit 1
  fi
fi

rm -f "$backup"
echo "Promoted: $match_priority/$match_effort '$match_title' → ## Done [$today]" >&2

# Optional td bridge: point at the in-flight td issue, never close it here
# (td's review gate stays in charge). No-op when td is unavailable.
if pkh_td_enabled 2>/dev/null; then
  if td_hit="$(pkh_td_find "$match_title")"; then
    td_id="${td_hit%%	*}"
    td_status="${td_hit#*	}"
    {
      echo "td: $td_id is still $td_status for this item."
      echo "  1. Harvest first: \`td show $td_id\` — move pitfall- or backlog-worthy"
      echo "     findings into pitfalls/ or backlog/ (td logs stay on this machine)."
      echo "  2. Then: td review $td_id   (or td approve $td_id ... per your review policy)"
    } >&2
  fi
fi
