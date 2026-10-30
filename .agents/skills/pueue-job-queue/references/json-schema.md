# pueue JSON schema (observed on 4.0.2)

Pueue's JSON output is **not formally documented** in the wiki. This page
captures the shape observed on **pueue 4.0.2** (macOS, but the shape is
identical across platforms — only paths differ). If `pueue --version`
reports a different major version, re-verify with the diagnostic command at
the bottom.

## `pueue status --json`

Top-level shape:

```json
{
  "tasks": {
    "<id>": { ...task object... },
    ...
  },
  "groups": {
    "<group_name>": {
      "status": "Running" | "Paused" | "Reset",
      "parallel_tasks": <int>      // 0 = unlimited
    },
    ...
  }
}
```

### Task object fields

| Field | Type | Notes |
|---|---|---|
| `id` | int | Stable while the task exists. Reused only after `pueue clean`/`reset`. |
| `created_at` | string (ISO-8601) | When `pueue add` was called. |
| `original_command` | string | Exactly what was passed to `pueue add`. |
| `command` | string | Post-shell-substitution form (often equal to `original_command`). |
| `path` | string | Working directory at submit time. |
| `envs` | object | Captured env vars at submit time. |
| `group` | string | Group name. `"default"` if not specified. |
| `dependencies` | int[] | Empty `[]` if no `--after`. Order matches the `--after` flags. |
| `priority` | int | 0 if not set. Higher = sooner. |
| `label` | string \| null | The `--label` value. `null` if not given. |
| `status` | object | Tagged enum, see below. |

### `status` (tagged enum)

The outer key is the variant name; the inner object has the variant's data.

#### `Stashed`

```json
{"Stashed": {"enqueue_at": null}}
{"Stashed": {"enqueue_at": "2026-04-27T...+08:00"}}   // delayed
```

#### `Queued`

```json
{"Queued": {"enqueued_at": "2026-04-27T...+08:00"}}
```

#### `Running`

```json
{"Running": {"enqueued_at": "...", "start": "..."}}
```

#### `Paused`

Pauses a Running task; carries the same fields as Running plus a pause time.

#### `Locked`

Rare — used during edit/restart transitions.

#### `Done`

```json
{
  "Done": {
    "enqueued_at": "2026-04-27T11:01:06.893055+08:00",
    "start": "2026-04-27T11:01:07.003813+08:00",
    "end": "2026-04-27T11:01:07.307972+08:00",
    "result": <result>
  }
}
```

`result` takes one of:

| Value | Meaning |
|---|---|
| `"Success"` | exit 0 |
| `"DependencyFailed"` | a parent in `dependencies` failed; this task never ran (`start == end`) |
| `"Killed"` | someone called `pueue kill` |
| `{"Failed": <int>}` | non-zero exit; the int is the exit code |
| `{"FailedToSpawn": "<os error>"}` | OS-level launch failure — the task never ran. **Dict-shaped, not a bare string** (verified on 4.0.2). Most common cause when driving a remote daemon: the recorded working directory doesn't exist on the daemon's host. |

`result` is a tagged enum, so treat **anything terminal that is not `"Success"`
as a failure**. Matching against a list of known-bad variants lets an
unrecognized one (as `FailedToSpawn` once did) silently count as success —
`wait.py` uses the allowlist form for this reason.

### `groups`

```json
{
  "default":   {"status": "Running", "parallel_tasks": 1},
  "ml":        {"status": "Running", "parallel_tasks": 4},
  "frozen":    {"status": "Paused",  "parallel_tasks": 2}
}
```

`parallel_tasks=0` means unlimited.

## `pueue log --json`

Shape:

```json
{
  "<id>": {
    "task": { ...same task object as in status... },
    "output": "<full stdout/stderr stream as a single string>"
  },
  ...
}
```

`pueue log --json` (no `--full`) returns only the last few lines of `output`.
`--full` returns everything (loaded into RAM — beware for huge logs; in that
case, read the file directly from `~/Library/Application Support/pueue/logs/`
on macOS or `~/.local/share/pueue/logs/` on Linux).

## Pueue's built-in QUERY DSL (prefer over jq for simple filters)

`pueue status [QUERY]` accepts a SQL-ish filter string that the daemon
applies *before* serialization — faster than jq on a big task table, and
shorter to type. Works with `--json`. Documented under `pueue status --help`.

Grammar:

```
[columns=[col,...]]?  [filter ...]*  [order_by col asc|desc]?  [first|last N]?

cols accepted in columns=[]:    id, status, command, label, path,
                                enqueue_at, dependencies, start, end
cols accepted in filters:       status, command, label, start, end, enqueue_at
filter ops:                     =   !=   <   >   %=   (%= = "contains")
```

Recipes:

```bash
# Last 10 failed tasks across all groups, with end time
pueue status --json 'status=Failed order_by end desc first 10'

# Substring match on label (great for label-prefix workflows)
pueue status --json 'label %= sweep-'

# Currently running tasks (no enum value for Running here — use jq for state),
# but for any Done variant (Success, Failed, Killed, DependencyFailed) the DSL works:
pueue status --json 'status=Success last 20'

# Compose: failed in the last hour, project columns
pueue status --json 'status=Failed end > 2026-04-27T00:00:00Z columns=[id,label,end]'

# Order by start, take the most recent 5
pueue status --json 'order_by start desc first 5'
```

The DSL covers ~80% of common queries with one-line incantations. For
status-enum *variant* matching that the DSL doesn't expose (e.g.
`Running`, `Stashed`, `DependencyFailed` specifically) or complex
projections, fall back to `jq` (next section).

## Useful `jq` recipes

```bash
# All currently-running task ids
pueue status --json | jq '[.tasks | to_entries[] | select(.value.status | has("Running")) | .key | tonumber]'

# All failed tasks (label + exit code)
pueue status --json | jq '
  .tasks | to_entries[] | select(.value.status.Done.result | type == "object" and has("Failed"))
  | {id: .key, label: .value.label, exit_code: .value.status.Done.result.Failed}
'

# All DependencyFailed tasks
pueue status --json | jq '
  .tasks | to_entries[]
  | select(.value.status.Done.result == "DependencyFailed")
  | {id: .key, label: .value.label, deps: .value.dependencies}
'

# Pending count per group
pueue status --json | jq '
  .tasks | to_entries
  | map(select(.value.status | has("Queued")))
  | group_by(.value.group) | map({group: .[0].value.group, pending: length})
'

# Direct dependents of task 17
pueue status --json | jq '[.tasks | to_entries[] | select(.value.dependencies | index(17)) | .key | tonumber]'

# Tasks finished in the last hour, sorted by end time desc
pueue status --json | jq --arg cutoff "$(date -u -v-1H +%FT%TZ 2>/dev/null || date -u -d '-1 hour' +%FT%TZ)" '
  [.tasks | to_entries[]
   | select(.value.status.Done? and (.value.status.Done.end >= $cutoff))]
  | sort_by(.value.status.Done.end) | reverse
  | map({id: .key, label: .value.label, result: .value.status.Done.result})
'
```

## Verify on your version

If you're on a different major version, run this once and diff against the
shapes above:

```bash
# Submit a quick probe task, check raw shape
pueue add --print-task-id --label probe-schema -- 'true'
sleep 1
pueue status --json | jq '.tasks | to_entries[] | select(.value.label == "probe-schema") | .value | keys'
pueue status --json | jq '.tasks | to_entries[] | select(.value.label == "probe-schema") | .value.status'
# cleanup
ID=$(pueue status --json | jq -r '.tasks | to_entries[] | select(.value.label == "probe-schema") | .key' | tail -n1)
pueue remove "$ID"
```

If the keys or status shape differ, update `wait.py` and `submit.sh`
accordingly — both already handle the documented variants but a new variant
(e.g. a hypothetical `Suspended`) will fall into the "Unknown" bucket of the
summary.
