#!/usr/bin/env bash
set -euo pipefail

# Validate a TODO.md that follows the project-knowledge-harness format and
# render a kanban-style Markdown board to stdout.
#
# Compatibility: macOS system Bash 3.2 (no associative arrays, no readarray).

usage() {
  cat <<'EOF'
Usage: todo-kanban.sh [OPTIONS] [TODO_FILE]

Validate a TODO.md that follows the project-knowledge-harness format and
render a kanban-style Markdown board.

Required structure:
  - First non-empty heading must be `# TODO`
  - Sections must appear in order: ## P1, ## P2, ## P3, ## P?, ## Done
  - Top-level list items inside sections are validated:
      Active (P1/P2/P3): - [ ] **[Effort] Title** — description
      P? items:          - [ ] **[?/Effort] Title** — description
      Done items:        - ✅ [YYYY-MM-DD] [P#/Effort] Title — summary
    where Effort is one of S, M, L, XL.
    Active items may end with: → [research](backlog/<slug>.md)

  Anything that is NOT a top-level `- [ ]` / `- ✅` item — prose paragraphs,
  blockquotes, HTML comments, `---` rules, indented sub-bullets — is
  ignored by the validator. Only top-level items count toward the lane
  totals in the rendered board.

  After the `## Done` section, additional `## ...` headings (e.g. notes,
  prune log) are allowed and skipped without validation.

Options:
  -h, --help           Show this help and exit
  --validate-only      Validate without rendering the board
  --json               Emit a JSON summary instead of the Markdown board
                       (counts per lane + items per lane)
  --td                 Overlay in-flight td issues (label `pkh`, same title):
                       marks active items and lists orphaned issues. Default:
                       on when td resolves a project here (PKH_TD=auto).
  --no-td              Never query td. --validate-only never queries td.

Exit codes:
  0  validation passed (and board rendered if not --validate-only)
  1  validation failed (offending file:line printed to stderr)
  2  usage error
EOF
}

mode_render="markdown"
validate_only=0
todo_file=""
td_flag="auto"

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --validate-only) validate_only=1; shift ;;
    --json) mode_render="json"; shift ;;
    --td) td_flag="on"; shift ;;
    --no-td) td_flag="off"; shift ;;
    --) shift; break ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      if [ -n "$todo_file" ]; then
        echo "Error: only one TODO_FILE positional argument accepted" >&2
        usage >&2
        exit 2
      fi
      todo_file="$1"
      shift
      ;;
  esac
done

todo_file="${todo_file:-TODO.md}"

if [ ! -f "$todo_file" ]; then
  echo "Error: file not found: $todo_file" >&2
  exit 1
fi

error() {
  local line_no="$1"
  local message="$2"
  echo "Syntax error in $todo_file:$line_no: $message" >&2
  exit 1
}

# Active item regex (P1/P2/P3 lanes)
active_re='^- \[ \] \*\*\[(S|M|L|XL)\] [^*]+\*\* — .+$'
# P? lane item regex
peq_re='^- \[ \] \*\*\[\?/(S|M|L|XL)\] [^*]+\*\* — .+$'
# Done item regex
done_re='^- ✅ \[[0-9]{4}-[0-9]{2}-[0-9]{2}\] \[(P1|P2|P3|P\?)/(S|M|L|XL)\] .+ — .+$'

# Parallel arrays indexed by lane (0..4)
lane_names=("P1" "P2" "P3" "P?" "Done")
lane_count=(0 0 0 0 0)
lane_items=("" "" "" "" "")

append_item() {
  local idx="$1"
  local item="$2"
  if [ -n "${lane_items[$idx]}" ]; then
    lane_items[$idx]="${lane_items[$idx]}"$'\n'"$item"
  else
    lane_items[$idx]="$item"
  fi
  lane_count[$idx]=$(( lane_count[$idx] + 1 ))
}

title_seen=0
section_idx=-1            # which lane we're currently inside (-1 = before P1)
expected_next=0           # next expected lane index (0..5; 5 = past Done)
post_done=0               # 1 once we've left the Done lane (extra ## headings ok)
line_no=0

while IFS= read -r line || [ -n "$line" ]; do
  line_no=$(( line_no + 1 ))

  # Title
  if [ "$line" = "# TODO" ]; then
    if [ "$title_seen" -eq 1 ]; then
      error "$line_no" "duplicate '# TODO' heading"
    fi
    title_seen=1
    continue
  fi

  # Other top-level # heading is forbidden (keeps the file single-purpose)
  case "$line" in
    "# "*|"#"[!#]*)
      if [ "$line" != "# TODO" ]; then
        error "$line_no" "unexpected top-level '# ...' heading; only '# TODO' is allowed"
      fi
      ;;
  esac

  # Section heading
  case "$line" in
    "## "*)
      heading="${line#"## "}"
      if [ "$post_done" -eq 1 ]; then
        # Allow any extra headings after Done; do not validate further items
        section_idx=-2
        continue
      fi
      if [ "$expected_next" -ge "${#lane_names[@]}" ]; then
        # Should have entered post_done already; defensive
        section_idx=-2
        continue
      fi
      expected="${lane_names[$expected_next]}"
      if [ "$heading" != "$expected" ]; then
        # Allow jumping straight to Done (slim pointer format: active items
        # migrated to GitHub Issues, only ## Done remains).
        if [ "$heading" = "Done" ]; then
          section_idx=4
          expected_next=5
          post_done=1
          continue
        fi
        error "$line_no" "expected section heading '## $expected', got '## $heading'"
      fi
      section_idx=$expected_next
      expected_next=$(( expected_next + 1 ))
      if [ "$expected" = "Done" ]; then
        # Mark that the next ## may legally appear (extra notes section)
        post_done=1
      fi
      continue
      ;;
  esac

  # Past Done section: skip validation
  if [ "$section_idx" -eq -2 ]; then
    continue
  fi

  # Before any section: skip prose/blockquotes freely
  if [ "$section_idx" -eq -1 ]; then
    continue
  fi

  # Indented content (sub-bullets, continuation): skip without validating
  case "$line" in
    " "*|$'\t'*) continue ;;
  esac

  # Top-level list item starting with `- ` — validate
  case "$line" in
    "- "*)
      current_lane="${lane_names[$section_idx]}"
      if [ "$current_lane" = "Done" ]; then
        if ! [[ "$line" =~ $done_re ]]; then
          error "$line_no" "Done item must match: '- ✅ [YYYY-MM-DD] [P#/Effort] Title — summary'"
        fi
      elif [ "$current_lane" = "P?" ]; then
        if ! [[ "$line" =~ $peq_re ]]; then
          error "$line_no" "P? item must match: '- [ ] **[?/Effort] Title** — description'"
        fi
      else
        if ! [[ "$line" =~ $active_re ]]; then
          error "$line_no" "active item must match: '- [ ] **[Effort] Title** — description'"
        fi
      fi
      append_item "$section_idx" "$line"
      continue
      ;;
  esac

  # Anything else (prose, blockquote, ---, HTML comment, blank): skip silently.
done < "$todo_file"

if [ "$title_seen" -eq 0 ]; then
  error 1 "missing '# TODO' heading"
fi

if [ "$expected_next" -ne "${#lane_names[@]}" ] && [ "$post_done" -eq 0 ]; then
  missing="${lane_names[$expected_next]}"
  error "$line_no" "missing section heading '## $missing'"
fi

if [ "$validate_only" -eq 1 ]; then
  echo "OK: $todo_file passes project-knowledge-harness format" >&2
  exit 0
fi

# Optional td overlay (render modes only, so CI validation stays hermetic).
td_open=""
td_orphans=""
td_used=0
if [ "$td_flag" != "off" ]; then
  src="$0"
  while [ -L "$src" ]; do
    link="$(readlink "$src")"
    case "$link" in /*) src="$link" ;; *) src="$(dirname "$src")/$link" ;; esac
  done
  lib_dir="$(cd "$(dirname "$src")" && pwd)/_lib"
  if [ -f "$lib_dir/td-bridge.sh" ]; then
    # shellcheck source=_lib/td-bridge.sh
    . "$lib_dir/td-bridge.sh"
    [ "$td_flag" = "auto" ] || pkh_td_mode="$td_flag"
    td_rc=0
    pkh_td_enabled || td_rc=$?
    if [ "$td_rc" -eq 0 ]; then
      td_used=1
      td_open="$(pkh_td_list_open)"
    elif [ "$td_rc" -eq 2 ]; then
      exit 2
    fi
  fi
fi

# item_title LINE -> prints the Title of an active/P? item (empty otherwise)
item_title() {
  if [[ "$1" =~ ^-\ \[\ \]\ \*\*\[(\?/)?(S|M|L|XL)\]\ (.+)\*\*\ —\ .+$ ]]; then
    printf '%s' "${BASH_REMATCH[3]}"
  fi
}

# td_lookup TITLE -> prints "id<TAB>status" from td_open
td_lookup() {
  local id status title tab
  tab="$(printf '\t')"
  while IFS="$tab" read -r id status title; do
    if [ -n "$id" ] && [ "$title" = "$1" ]; then
      printf '%s\t%s' "$id" "$status"
      return 0
    fi
  done <<TD_EOF
$td_open
TD_EOF
  return 1
}

if [ "$td_used" -eq 1 ] && [ -n "$td_open" ]; then
  active_titles=""
  i=0
  while [ $i -lt 4 ]; do
    if [ "${lane_count[$i]}" -gt 0 ]; then
      while IFS= read -r itm; do
        t="$(item_title "$itm")"
        active_titles="$active_titles$t"$'\n'
      done <<TD_EOF
${lane_items[$i]}
TD_EOF
    fi
    i=$(( i + 1 ))
  done
  tab="$(printf '\t')"
  while IFS="$tab" read -r id status title; do
    [ -n "$id" ] || continue
    case $'\n'"$active_titles" in
      *$'\n'"$title"$'\n'*) ;;
      *)
        td_orphans="$td_orphans$id$tab$status$tab$title"$'\n'
        echo "Warning: td $id ($status) '$title' has no active TODO.md item (shipped, renamed, or removed?)" >&2
        ;;
    esac
  done <<TD_EOF
$td_open
TD_EOF
fi

if [ "$mode_render" = "json" ]; then
  printf '{\n'
  printf '  "source": "%s",\n' "$todo_file"
  printf '  "lanes": [\n'
  i=0
  while [ $i -lt ${#lane_names[@]} ]; do
    printf '    {"name": "%s", "count": %d, "items": [' "${lane_names[$i]}" "${lane_count[$i]}"
    if [ "${lane_count[$i]}" -gt 0 ]; then
      printf '\n'
      first=1
      # Iterate items separated by newline
      while IFS= read -r itm; do
        # JSON-escape backslash and double-quote
        esc="${itm//\\/\\\\}"
        esc="${esc//\"/\\\"}"
        if [ $first -eq 1 ]; then
          printf '      "%s"' "$esc"
          first=0
        else
          printf ',\n      "%s"' "$esc"
        fi
      done <<EOF
${lane_items[$i]}
EOF
      printf '\n    '
    fi
    if [ $i -lt $(( ${#lane_names[@]} - 1 )) ]; then
      printf ']},\n'
    else
      printf ']}\n'
    fi
    i=$(( i + 1 ))
  done
  if [ "$td_used" -eq 1 ]; then
    printf '  ],\n'
    json_td_list() {
      local first=1 id status title tab
      tab="$(printf '\t')"
      while IFS="$tab" read -r id status title; do
        [ -n "$id" ] || continue
        title="${title//\\/\\\\}"
        title="${title//\"/\\\"}"
        [ $first -eq 1 ] || printf ','
        printf '\n    {"id": "%s", "status": "%s", "title": "%s"}' "$id" "$status" "$title"
        first=0
      done
      [ $first -eq 1 ] || printf '\n  '
    }
    printf '  "td_inflight": ['
    json_td_list <<TD_EOF
$td_open
TD_EOF
    printf '],\n  "td_orphans": ['
    json_td_list <<TD_EOF
$td_orphans
TD_EOF
    printf ']\n}\n'
  else
    printf '  ]\n}\n'
  fi
  exit 0
fi

# Markdown render
printf '# TODO Kanban\n\n'
printf '_Source: `%s`_\n\n' "$todo_file"
printf '| Lane | Count |\n'
printf '|---|---:|\n'
i=0
while [ $i -lt ${#lane_names[@]} ]; do
  printf '| `%s` | %s |\n' "${lane_names[$i]}" "${lane_count[$i]}"
  i=$(( i + 1 ))
done
printf '\n'

i=0
while [ $i -lt ${#lane_names[@]} ]; do
  printf '## %s (%s)\n\n' "${lane_names[$i]}" "${lane_count[$i]}"
  if [ "${lane_count[$i]}" -eq 0 ]; then
    printf '_No items._\n\n'
  elif [ "$td_used" -eq 1 ] && [ $i -lt 4 ] && [ -n "$td_open" ]; then
    while IFS= read -r itm; do
      t="$(item_title "$itm")"
      if [ -n "$t" ] && hit="$(td_lookup "$t")"; then
        printf '%s 🔧 `%s` (%s)\n' "$itm" "${hit%%$'\t'*}" "${hit#*$'\t'}"
      else
        printf '%s\n' "$itm"
      fi
    done <<TD_EOF
${lane_items[$i]}
TD_EOF
    printf '\n'
  else
    printf '%s\n\n' "${lane_items[$i]}"
  fi
  i=$(( i + 1 ))
done

if [ -n "$td_orphans" ]; then
  printf '## td issues without an active TODO item\n\n'
  printf '_Labelled `pkh` and still open, but no active TODO.md title matches. Review or approve them, or fix the title._\n\n'
  while IFS="$(printf '\t')" read -r id status title; do
    [ -n "$id" ] || continue
    printf -- '- `%s` (%s) %s\n' "$id" "$status" "$title"
  done <<TD_EOF
$td_orphans
TD_EOF
  printf '\n'
fi
