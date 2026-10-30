# pueue CLI cheatsheet

The skill's wrapper scripts cover **submit / DAG / wait**. Everything else is
fine to call directly. This page lists the un-wrapped commands the agent
should reach for, with one-line "when to use" notes.

Always run `pueue <subcmd> --help` first if anything below looks ambiguous —
flags occasionally change between releases.

## Inspecting state

| Command | When to use |
|---|---|
| `pueue status` | Quick human-readable view. Pretty table with deps, group, label, start/end. |
| `pueue status --json` | Machine-readable. The agent's primary status surface. See `references/json-schema.md`. |
| `pueue status [QUERY]` | SQL-ish filter: `pueue status 'status=Failed'`, `pueue status 'columns=[id,label,end] order_by end desc'`. Run `pueue status --help` for grammar. Usable with `--json`. |
| `pueue group` | List groups with their parallelism settings (table form). |
| `pueue group --json` | Same, JSON. Shape: `{"<name>": {"status": "Running", "parallel_tasks": N}}`. |
| `pueue log <id>` | Last few lines of stdout/stderr. `--lines N` for more. `--full` for everything. |
| `pueue log --json --full <id>` | Full log payload as JSON. The output field is the captured stdout/stderr. |
| `pueue follow <id>` | `tail -f` against a running task. Blocks. Ctrl-C to detach. |
| `pueue follow --stderr <id>` | Same but stderr only (rarely useful — pueue captures both into one stream by default). |

## Lifecycle

| Command | When to use |
|---|---|
| `pueue start [<ids>]` | Resume one or more paused tasks (or `--group G` to resume a whole group). |
| `pueue pause [<ids>]` | Pause tasks (or `--group G` for a whole group). The task keeps running its current step until SIGSTOP-able point. |
| `pueue kill <ids>` | Send SIGKILL. Status becomes `Done.Killed`. |
| `pueue kill --group G` | Kill everything in a group. |
| `pueue remove <ids>` | Drop terminated tasks from the visible list. **Each id must be a separate arg** — bash arrays, not space-joined strings. |
| `pueue clean` | Remove ALL finished tasks from the visible list. Includes failures. Be deliberate. |
| `pueue clean --successful-only` | Keep failures around for inspection; drop successes. |
| `pueue reset` | Nuclear option: kill everything, clear the list, reset state. Survives daemon restart. |
| `pueue shutdown` | Stop `pueued` cleanly. State is persisted to disk. |

## Retrying and editing

| Command | When to use |
|---|---|
| `pueue restart <ids>` | Re-enqueue with a NEW task id. The old id stays in history with its old result. |
| `pueue restart --in-place <ids>` | Reuse the same id. Overwrites the old log. Most agents want this for "retry this". |
| `pueue restart --all-failed` | Restart every failed task (across all groups). Pair with `--in-place` if you want to reuse ids. |
| `pueue restart --failed-in-group G` | Like `--all-failed` but scoped to one group. |
| `pueue restart --start-immediately <ids>` (alias `-k`) | Bypass queue + dependencies; start now. |
| `pueue restart --stashed <ids>` | Restart but in Stashed state (won't auto-run; you `pueue enqueue` later). |
| `pueue edit <id>` | Open `$EDITOR` to modify the command, path, label, etc. of an enqueued task. |
| `pueue env <id> <add\|remove\|list>` | Adjust a task's environment variables before it runs. |
| `pueue stash <ids>` | Move tasks to Stashed state (won't auto-start). |
| `pueue enqueue <ids>` | Move stashed tasks back to Queued. Optional `--delay STR`. |
| `pueue switch <id1> <id2>` | Swap queue positions (rarely needed; priority is usually cleaner). |

## Groups and parallelism

| Command | When to use |
|---|---|
| `pueue group add <name>` | Create a new group. `default` already exists. |
| `pueue group remove <name>` | Delete. **Tasks in the group move to `default`** — they don't disappear. |
| `pueue parallel <N>` | Set parallelism for the default group. `0` = unlimited. |
| `pueue parallel <N> --group G` | Set parallelism for a specific group. |

## Sending input to running tasks

| Command | When to use |
|---|---|
| `pueue send <id> "y\n"` | Pipe a string to the task's stdin. Useful for confirmations on interactive scripts. |

## Daemon

| Command | When to use |
|---|---|
| `pueued -d` | Start daemon (forks). |
| `pueued -d --config PATH` | Start with a non-default config (e.g. a test fixture). |
| `pueued -v -d` | Verbose stderr — debug startup issues. |
| `PUEUE_CONFIG_PATH=... pueue ...` | Point the client at a non-default config. Pair with `pueued -d --config PATH` for isolated test daemons. |

## Completions

`pueue completions <shell> <out-dir>` — generate shell completions. Run
once on setup if your shell isn't autocompleting.

## What this skill DOES wrap

- `pueue add` (`scripts/submit.sh` — JSON-output wrapper, group autocreate, defensive id parse)
- `pueue add` for DAGs (`scripts/submit-dag.py` — declarative spec, topo sort, validation)
- `pueue status --json` polling for "block until terminal" semantics (`scripts/wait.py`)
- Daemon health probe (`scripts/check-daemon.sh`)

Everything else, call directly.
