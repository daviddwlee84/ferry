# shellcheck shell=bash disable=SC2034  # match_* globals are read by the caller
# Shared active-item matcher for project-knowledge-harness scripts.
# Sourced, not executed. Compatibility: macOS system Bash 3.2.
#
# pkh_todo_match FILE SUBSTRING
#   Scans the active lanes (## P1/P2/P3/P?) of FILE for items whose Title
#   contains SUBSTRING (case-sensitive) and sets, for the LAST match:
#     match_count, match_line_no, match_priority, match_effort,
#     match_title, match_description, match_line
#   Callers decide what 0 or >1 matches mean.

pkh_todo_match() {
  local file="$1" substr="$2"
  local current_lane="" line_no=0 line eff ttl desc

  match_count=0
  match_line_no=0
  match_priority=""
  match_effort=""
  match_title=""
  match_description=""
  match_line=""

  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$(( line_no + 1 ))
    case "$line" in
      "## P1") current_lane="P1"; continue ;;
      "## P2") current_lane="P2"; continue ;;
      "## P3") current_lane="P3"; continue ;;
      "## P?") current_lane="P?"; continue ;;
      "## Done") current_lane="Done"; continue ;;
      "## "*) current_lane="post-done"; continue ;;
    esac

    case "$current_lane" in
      P1|P2|P3)
        [[ "$line" =~ ^-\ \[\ \]\ \*\*\[(S|M|L|XL)\]\ (.+)\*\*\ —\ (.+)$ ]] || continue
        ;;
      "P?")
        [[ "$line" =~ ^-\ \[\ \]\ \*\*\[\?/(S|M|L|XL)\]\ (.+)\*\*\ —\ (.+)$ ]] || continue
        ;;
      *) continue ;;
    esac

    eff="${BASH_REMATCH[1]}"
    ttl="${BASH_REMATCH[2]}"
    desc="${BASH_REMATCH[3]}"
    case "$ttl" in
      *"$substr"*)
        match_count=$(( match_count + 1 ))
        match_line_no=$line_no
        match_priority="$current_lane"
        match_effort="$eff"
        match_title="$ttl"
        match_description="$desc"
        match_line="$line"
        ;;
    esac
  done < "$file"
}

# pkh_todo_active_titles FILE
#   Print the Title of every active item (P1/P2/P3/P?), one per line.
pkh_todo_active_titles() {
  local file="$1" current_lane="" line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "## P1"|"## P2"|"## P3"|"## P?") current_lane="active"; continue ;;
      "## "*) current_lane=""; continue ;;
    esac
    [ "$current_lane" = "active" ] || continue
    if [[ "$line" =~ ^-\ \[\ \]\ \*\*\[(\?/)?(S|M|L|XL)\]\ (.+)\*\*\ —\ .+$ ]]; then
      printf '%s\n' "${BASH_REMATCH[3]}"
    fi
  done < "$file"
}
