# Integrating with td and external trackers

The harness is git-native and works without any other tool. This reference
explains how it coexists with an execution tracker such as
[td](https://github.com/marcus/td), and with hosted trackers like GitHub
Issues or Linear, without creating a second source of truth.

## Layers: one home per kind of information

| Layer | Holds | Store | Survives `git clone` elsewhere |
|---|---|---|---|
| Knowledge | Past traps, research, trade-offs, invariants | `pitfalls/`, `backlog/`, `AGENTS.md` | Yes |
| Intent | Deferred work, priority and effort | `TODO.md` | Yes |
| Execution | In-flight status, logs, handoffs, review | td (`.todos/issues.db`) | No (unless td-sync is set up) |
| External | Bug reports and requests from users or other teams | GitHub Issues / Linear | Yes, but outside the repo |

When two layers disagree, the one higher in the table wins: git beats td.

## The one-way reference rule

The ephemeral layer points at the durable one, never the other way round.

- A td issue for a `TODO.md` item has the label `pkh` and the item's exact
  title. Its body quotes the TODO line and links the backlog doc.
- `TODO.md` never stores td ids. Without td-sync they are local to one
  machine, so another clone would carry dangling references.
- Matching is by exact title. If you rename a `TODO.md` item while it is in
  flight, update the td issue title as well (`td update <id> --title ...`).
  Otherwise `todo-kanban.sh` reports the issue as an orphan.

## td bridge in the scripts

Every helper reads `PKH_TD=auto|on|off` (default `auto`). In `auto` mode the
bridge is active only when `td` is installed and resolves a project from the
current directory. That includes git worktrees and `.td-root`. Without td,
every script behaves exactly as it did before the bridge existed.

| Script | With td enabled |
|---|---|
| `start-todo.sh --title <substr>` | Creates or reuses the matching `pkh` issue (P? maps to P4; S/M/L/XL map to 1/3/8/13 points) and runs `td start`. Prints the id on stdout. Does not touch `TODO.md`. |
| `promote-todo.sh` | After promoting, prints a harvest-then-review hint for the open issue. It never closes the issue, so td's review policy decides. |
| `todo-kanban.sh` | Marks in-flight items with `🔧 td-xxxx (status)` and lists orphans: open `pkh` issues with no active item. `--json` adds `td_inflight` and `td_orphans`. `--validate-only` never queries td, so CI stays hermetic. |
| `init.sh --td auto\|on\|off` | Appends the routing snippet (`assets/td-routing.md.template`) to the agent contract. |
| `add-todo.sh`, `sweep-inbox.sh` | Unchanged. New intent always goes to `TODO.md`. |

The bridge needs `jq` or `python3` to read `td --json`. Without either it
warns and skips the bridge.

## Lifecycle with td

1. Capture: `add-todo.sh` puts the item in `TODO.md`.
2. Start: `start-todo.sh` creates the `pkh` issue in td, which tracks
   `td log` and `td handoff` from then on.
3. Ship: `promote-todo.sh` in the same commit as the change.
4. Harvest: run `td show <id>`, then copy any trap into `pitfalls/` and any
   design reasoning into `backlog/`.
5. Close: `td review` / `td approve`.

## Cross-machine and worktrees

- **Same machine, several worktrees**: td finds the main worktree's
  `.todos/` through the git common dir, so every worktree shares one
  database. `start-todo.sh` reuses the issue instead of creating a duplicate.
  (`td link` / `td files` resolve paths against the main worktree. Check
  them before relying on file links from a secondary worktree.)
- **Several devboxes, td local only**: each machine has its own td state.
  That is fine because the durable layers travel through git. Harvest before
  moving to another machine.
- **Several devboxes, td-sync**: this is an experimental server (the
  `sync_cli` feature flag is off by default). Projects link by a
  server-side id that is not tied to the git remote, so two clones can end up
  linked to different projects. Last-write-wins sync is not a lock, so two
  agents on two machines can still claim the same issue.
- Never commit `.todos/`. A binary SQLite file cannot be merged.

## Exit plan (td turns out not to fit)

1. `td export --all --format md -o td-export.md`, then harvest anything worth
   keeping into `backlog/` or `pitfalls/`.
2. Delete the `project-knowledge-harness:td-routing` block and td's own
   `td-agent-instructions` block from the agent contract.
3. `rm -rf .todos/` (and the `.gitignore` line, if you like). Nothing in git
   depended on it, and the scripts fall back to td-less behaviour.

## Hosted trackers (GitHub Issues, Projects, Linear)

- Use them when people outside the repo need to file or watch work: users
  of a published package, other teams, or a cross-repo roadmap. Keep
  research and pitfalls in the repo anyway, and link to them from the
  issue.
- Use the same one-way rule. A `TODO.md` line may cite `gh#123` because that
  id resolves from any clone. Do not mirror descriptions in both places, and
  do not add a bidirectional sync bot.
- Agents: prefer the CLI (`gh issue list`, `gh issue view`), which is already
  authenticated and cheap in tokens. Use MCP servers where no shell exists.
  Agents won't check a hosted tracker unless they're told to, so name it in
  the agent contract.
