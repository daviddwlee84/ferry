---
name: pueue-job-queue
description: Drive Nukesor/pueue (https://github.com/Nukesor/pueue) for queued, parallel, scheduled, and lightly-DAG'd shell jobs — wraps `pueue add --after`, `pueue status --json`, `pueue log --json`, group-level parallelism, and `pueued` daemon health. Use when the user wants to background long-running shell commands across reboots, queue dozens of jobs with capped parallelism, run a fan-out / fan-in pipeline of shell steps, says "pueue", "pueued", "pueue add", "pueue queue", "pueue group", "task queue for shell", "background this job", or asks how to schedule/parallelize CLI work without a real orchestrator (Airflow/Prefect/Dagster). Good fit for ML sweeps, long-running data pipelines, batched evaluations, scheduled `--delay` jobs, "wait for X then run Y" sequences.
---

# pueue job queue

Pueue is a daemon-backed shell job queue. The daemon (`pueued`) accepts tasks
via the `pueue` CLI, runs them across parallelism-capped **groups**, persists
state across reboots, and exposes status / logs as JSON. This skill teaches
the agent to drive pueue end-to-end: submit single tasks or whole DAGs, cap
parallelism per group, block until completion, retrieve logs, and retry.

The skill is a **CLI bridge**, not a scheduler. Pueue's `--after` is **AND-only**
and **success-only** — that maps cleanly to declarative DAGs and not much
beyond. For OR-deps, conditional branching, retry-with-backoff, or distributed
scheduling, escalate to a real orchestrator (see "When NOT to use").

## When to use

- "Run these 30 commands, max 4 at a time" → `pueue group add ml && pueue parallel 4 --group ml` then loop `submit.sh --group ml`.
- "Kick off a long training job and let me close my laptop" → `submit.sh -- ./train.sh` (pueue persists across reboots).
- "Run task B only after task A finishes successfully" → `submit.sh --after $A_ID -- ./b.sh`.
- "Fan out 4 trainings, then evaluate" → `submit-dag.py dag.yaml`.
- "Schedule this for tonight" → `submit.sh --delay 6h -- ./nightly.sh`.
- "Block until task 17 is done, then tell me what happened" → `wait.py --ids 17`.
- The user mentions pueue, pueued, the pueue CLI, or asks about a "task queue for shell jobs".

## When NOT to use

- **One short shell command** the user wants to run right now. Just run it. Pueue adds daemon overhead for nothing.
- **Cross-host *scheduling*** (jobs distributed across N machines) → Airflow / Dagster / Prefect / Slurm. Note this is not the same as driving a *single* remote daemon, which pueue does support — see `references/remote-daemon.md`.
- **Conditional / OR / retry-with-backoff dependencies** → real orchestrator. Pueue's `--after` is AND-only and success-only.
- **Typed task IO and artifact tracking** (data lineage, cached intermediate outputs) → DVC (`dvc exp run --queue`), Prefect, Airflow.
- **Long-running services** (web servers, daemons) → systemd, launchd, supervisord. Pueue is for finite tasks.

## Authoritative sources

- Repo: https://github.com/Nukesor/pueue
- Wiki: https://github.com/Nukesor/pueue/wiki
- `pueue --help`, `pueue <subcommand> --help`, `man pueue`

## Mental model

| Concept | What it is | How to interact |
|---|---|---|
| **Daemon** (`pueued`) | Long-running process holding queue state. Required. | `pueued -d` to start. `pueue shutdown` to stop. |
| **Task** | One shell command, identified by integer id. | `pueue add ...`, `pueue status`, `pueue kill`, `pueue remove`. |
| **Group** | Named queue with its own parallelism limit. `default` always exists. | `pueue group add <name>`, `pueue parallel N --group <name>`. |
| **Dependency** | Task waits for one or more parents to **succeed** before running. | `pueue add --after <id> --after <id> -- <cmd>`. AND-only, success-only. |
| **Status** | Tagged enum: `Queued`, `Running`, `Stashed`, `Paused`, `Locked`, `Done`. `Done` carries a `result` (`Success`, `Killed`, `DependencyFailed`, or `{"Failed": <exit_code>}`). | `pueue status --json`. |

## Setup quickstart

```bash
# 1. Verify daemon is healthy. Auto-start if missing.
bash skills/local/pueue-job-queue/scripts/check-daemon.sh --start --json | jq

# 2. (Optional) Create a group with 4 parallel slots.
pueue group add ml
pueue parallel 4 --group ml
```

For per-OS daemon paths and launchd/systemd setup, read
`references/daemon-and-config.md`.

## Always pass a label

`pueue status` shows the **label** column before the command. In a busy
queue, scanning labels is the only way to tell `train.py` runs apart at a
glance. **Always pass `--label`** when submitting, and bias toward names
that distinguish *this* task from its siblings.

Convention:

- Shape: `<verb>-<subject>-<key>` (≤30 chars). `verb` = action,
  `subject` = artifact, `key` = the variable that distinguishes this run.
- Encode the discriminator: seed, dataset slice, model variant, date, host.
- Don't restate the command — `train.py --seed 1` becomes label
  `train-baseline-seed1`, not `python-train-py-seed-1`.
- For DAGs, `submit-dag.py --label-prefix <run>-` makes every task labeled
  `<run>-<task_name>` so `wait.py --label-prefix <run>-` selects the whole
  graph and `pueue clean` can be filtered later.

| Good | Bad |
|---|---|
| `train-baseline-seed1` | `task1` (no metadata) |
| `eval-prod-2026q1` | `python eval.py --quarter 2026q1` (restates cmd) |
| `fetch-prices-AAPL` | `fetch-data` (multiple identical labels) |
| `nightly-featurize` (DAG) | `step-2-of-5` (rename a step → label rots) |

## Workflow A — submit one task

```bash
bash skills/local/pueue-job-queue/scripts/submit.sh \
  --label train-baseline --group ml \
  -- python train.py --seed 1
# stdout: {"task_id":17,"label":"train-baseline","group":"ml","after":[],...}
# stderr: submitted task 17 (group=ml, label=train-baseline)
```

Capture the id with `jq -r .task_id`. The script wraps `pueue add
--print-task-id` and falls back to a status-by-label lookup if the id parse
fails (defensive against future format changes).

## Workflow B — batched runs with capped parallelism

```bash
# One-time: create + size the group
pueue group add sweep
pueue parallel 4 --group sweep

# Loop: submit 30 tasks; pueue runs at most 4 at a time
for SEED in $(seq 1 30); do
  bash skills/local/pueue-job-queue/scripts/submit.sh \
    --label "sweep-$SEED" --group sweep \
    -- python train.py --seed "$SEED"
done

# Block until all sweep-* tasks are done
skills/local/pueue-job-queue/scripts/wait.py \
  --label-prefix sweep- --group sweep
```

Group parallelism is the parallelism primitive in pueue — there is no
per-task pool. Set it once, submit freely.

## Workflow C — fan-out / fan-in DAG

For multi-task pipelines with success-only dependencies, write a YAML spec
and submit it declaratively. **Prefer `--isolated-group`** — it creates a
fresh group sized to the DAG's fan-out width and leaves your other groups
(especially `default`) untouched:

```bash
skills/local/pueue-job-queue/scripts/submit-dag.py \
  skills/local/pueue-job-queue/assets/dag.example.yaml \
  --label-prefix nightly- --isolated-group
# stderr: isolated-group: created 'dag-dcda9347' with parallel_tasks=2
# stdout: {"tasks":{"fetch":17,...},
#          "topo_order":[...],
#          "width_per_group":{"dag-dcda9347":2},
#          "isolated_group":"dag-dcda9347"}
```

Each task gets the label `<--label-prefix><task_name>` so you can
`wait.py --label-prefix nightly-` later. After the run, clean up with
`pueue group remove <name>` (or run `cleanup.sh --remove-empty-groups`).

The submitter topo-sorts, validates the graph (cycle detection, unknown
references, missing `cmd:`), computes the **DAG width per group** (the
minimum `parallel_tasks` needed for fan-out to actually run in parallel),
then submits in order — wiring `--after` from the in-flight name→id map.
**No partial submits**: validation fails loud before any `pueue add` runs.

Three parallelism modes, in order of recommendation:

1. **`--isolated-group [NAME]`** (recommended). Fresh group, sized to width,
   no side effects on other groups. NAME auto-generates from spec hash if
   omitted. Mutually exclusive with `--default-group`.
2. **`--auto-parallel`**. Mutates `pueue parallel` on the target group in
   place. Use this when the DAG is meant to live in a long-lived shared
   group (e.g. `ml`) and that group's parallelism should permanently grow.
3. **No flag**. Only warns (in stderr + JSON's `parallelism_warnings`).
   Useful in `--dry-run` planning.

See `assets/dag.example.yaml` for a 5-task fan-out/fan-in template and
`references/dag-patterns.md` for more shapes.

## Workflow D — block until tasks reach terminal state

```bash
# Wait for specific ids
skills/local/pueue-job-queue/scripts/wait.py --ids 17,18,19 --timeout-seconds 300

# Wait for everything in a group
skills/local/pueue-job-queue/scripts/wait.py --group ml

# Wait for a label prefix (e.g. all tasks the DAG submitter created)
skills/local/pueue-job-queue/scripts/wait.py --label-prefix nightly-
```

Output is a JSON summary on stdout:

```json
{"summary": {"total": 4, "success": 3, "failed": 1, "killed": 0, "dependency_failed": 0},
 "tasks": [{"id": 17, "status": "Done", "result": "Success", "exit_code": 0,
            "label": "nightly-fetch", "group": "ml",
            "start": "...", "end": "..."}],
 "elapsed_seconds": 42.3}
```

Exit codes: `0` all succeeded, `5` ≥1 task failed/killed/dependency-failed,
`6` timeout. The agent can branch on exit code instead of parsing JSON.

## Workflow E — logs, retry, kill

One-line `pueue` calls (full list in `references/cli-cheatsheet.md`):

```bash
pueue log --json --full 17        # full stdout/stderr as JSON
pueue restart --in-place 17       # retry in-place (overwrites old log)
pueue kill 17 && pueue remove 17  # kill + drop from queue
```

For ad-hoc filtering, prefer pueue's built-in QUERY DSL over piping
through `jq` — it's faster (server-side filter) and shorter:

```bash
pueue status --json 'status=Failed order_by end desc first 10'
pueue status --json 'label %= sweep-'   # substring match
```

See `references/json-schema.md` for the QUERY grammar + jq fallbacks.

## Workflow F — periodic cleanup

Pueue's task list grows unbounded. `pueue status --json` walks the whole
table, so a queue with thousands of completed tasks slows down everything
that touches status. Run `cleanup.sh` weekly:

```bash
skills/local/pueue-job-queue/scripts/cleanup.sh \
  --successful-only --remove-empty-groups --logs-older-than 30
# stdout: {"cleaned_tasks":[...], "removed_groups":["dag-abc12345"],
#          "deleted_logs":[...], "kept_running":[...], ...}
```

`--successful-only` keeps failures around for inspection; drop the flag
for a full sweep. Pair with the agent's existing weekly maintenance habit
(or `/schedule` an agent to run it).

## Available scripts

- **`scripts/check-daemon.sh`** — Single-shot daemon health + version + log-dir check, with optional auto-start. Outputs JSON to stdout.
  - Flags: `--start`, `--json` (default on), `--help`.
  - Exit: `0` healthy, `2` pueue not installed, `3` daemon unreachable, `4` client/daemon version mismatch.
- **`scripts/submit.sh`** — Submit ONE task, return clean JSON `{task_id, group, label, after, ...}`. Wraps `pueue add --print-task-id` with defensive id parsing. Auto-creates the group if missing.
  - Flags: `--label`, `--group`, `--after` (repeatable), `--immediate`, `--stashed`, `--delay`, `--priority`, `--working-dir`, `--escape`, `--dry-run`, `--help`.
  - Exit: `0` ok, `1` bad args, `2` pueue not installed, `3` `pueue add` failed, `4` daemon unreachable.
- **`scripts/wait.py`** — Block until selected tasks reach terminal status; emit a JSON summary. Selectors: `--ids`, `--label`, `--label-prefix`, `--group`. Quiet by default — emits only one initial line + state-change events on stderr; pass `--verbose` for per-tick output.
  - Flags: `--ids`, `--label` (repeatable), `--label-prefix`, `--group`, `--poll-seconds`, `--timeout-seconds`, `--fail-fast`, `--verbose`, `--help`.
  - Exit: `0` all succeeded, `1` arg error, `4` daemon unreachable, `5` ≥1 failed/killed/dependency-failed, `6` timeout.
- **`scripts/submit-dag.py`** — Declarative DAG submitter. Reads YAML or JSON (file path or `-` for stdin), topo-sorts, validates, submits with wired `--after`, returns `{name → id, topo_order, width_per_group, isolated_group?}`. Computes DAG fan-out width per group; with `--isolated-group [NAME]` creates a fresh dedicated group sized to the width (preferred — leaves user's other groups untouched). With `--auto-parallel` mutates target groups in place. Without either, only warns.
  - Flags: `--format`, `--default-group`, `--label-prefix`, `--dry-run`, `--print-graph`, `--auto-parallel`, `--isolated-group [NAME]`, `--help`.
  - Exit: `0` all submitted, `1` schema/cycle/unknown-ref/conflicting flags, `2` pueue not installed, `3` mid-run pueue failure (stdout still emits IDs that DID submit, for cleanup).
- **`scripts/cleanup.sh`** — Prune pueue task history, empty groups, and old log files. Wraps `pueue clean` + `pueue group remove` for empty non-default groups + optional log-file mtime pruning. Emits a JSON report. Run weekly to keep `pueue status --json` fast.
  - Flags: `--successful-only`, `--group GROUP`, `--remove-empty-groups`, `--logs-older-than N`, `--dry-run`, `--help`.
  - Exit: `0` ok (or dry-run), `1` bad args, `2` pueue not installed, `3` daemon unreachable.

## Bundled assets

- `assets/dag.example.yaml` — 5-task fan-out/fan-in (`fetch → featurize → {train_a, train_b} → evaluate`). Pass it to `submit-dag.py` with `--dry-run` first to see the topo order.
- `assets/pueue.yml.example` — Config snippet showing `pause_group_on_failure: true`, `default_parallel_tasks`, and a sample `groups:` block. The header comment names the platform-specific destination paths.

## Reference files

- `references/cli-cheatsheet.md` — Read **when** the task needs `pueue follow`, `pueue log`, `pueue restart`, `pueue kill`, `pueue clean`, `pueue group/parallel`, `pueue pause/start`, `pueue reset`, `pueue edit`, `pueue env`, or any other un-wrapped `pueue` subcommand. Has a one-line "when to use" for each.
- `references/json-schema.md` — Read **when** writing custom `jq` queries against `pueue status --json` or `pueue log --json`, or when a script's JSON parsing surprises you. Documents the observed schema on pueue 4.0.2 with concrete examples for each status variant.
- `references/dag-patterns.md` — Read **when** the user asks for shapes beyond fan-out/fan-in (mixed sequential+parallel, diamond, etc.) or hits the AND-only / success-only limitation. Has examples and a "when to escalate to a real orchestrator" decision table.
- `references/daemon-and-config.md` — Read **when** setting up `pueued` for the first time, configuring per-OS paths, picking config knobs (`pause_group_on_failure`, `default_parallel_tasks`), or wiring up launchd / systemd-user.
- `references/remote-daemon.md` — Read **when** the daemon is on another machine: SSH unix-socket forwarding (recommended, needs no server change), direct TCP + TLS, and the working-directory trap that makes cross-platform remote *submission* impractical. Short version: `ssh host 'pueue add ...'` to submit; a forwarded client to read/control.

## See also

- `long-running-jobs` — pueue is one *implementation* of "let the queue own the
  chain" (`--after`) and "block once" (`wait.py`). That skill owns the prior
  question: **which** of those an agent should reach for, when a scheduled
  check-in is ever justified, and how to record completion durably so a run
  survives losing the session. Read it if you are tempted to poll
  `pueue status` on a timer.
- `slurm-hpc` — the cluster-side equivalent when jobs leave this machine.

## Gotchas

- **DAG fan-out is gated by `parallel_tasks` per group.** Even when two siblings both depend only on `A` and the DAG allows them to run in parallel, they will *serialize* if their group's `parallel_tasks=1`. Pueue's parallelism primitive is the group, not the dependency graph. `submit-dag.py` warns when DAG width exceeds the group's slots and suggests the exact `pueue parallel N --group G` command; pass `--auto-parallel` to apply it.
- **`pueue add -- bash -c 'sleep 60'` does not preserve quoting.** Pueue joins all `<COMMAND>` args and re-shells. The single-quoted `'sleep 60'` is unwrapped by *your* shell before pueue sees it, then pueue re-splits. Quote the **whole** command as one arg: `pueue add 'sleep 60'` or `submit.sh -- 'bash -c "sleep 60 && echo done"'`.
- **`--after` is AND-only and success-only.** Failed parent → dependent's `status.Done.result` becomes `"DependencyFailed"` and it never runs. There is no OR, no run-on-failure, no retry-on-failure. If you need that, you need a real orchestrator.
- **Pueue does NOT auto-create groups.** `pueue add --group new_name` exits 1 with `"Group new_name doesn't exists. Use one of these: [...]"`. `submit.sh` calls `pueue group add` first if the group is missing.
- **`pueue restart` (default) creates a NEW task id.** The old id stays in history with its result. To retry in-place (reusing the id, overwriting the log), pass `--in-place`. Choose deliberately — agent users often want `--in-place` for "retry this".
- **`pause_group_on_failure` is config-only, no CLI flag.** Set it in `pueue.yml`; reload with `pueue reset` or restart `pueued`.
- **Logs live in platform-specific dirs**, not your CWD. macOS: `~/Library/Application Support/pueue/logs/`. Linux: `~/.local/share/pueue/logs/`. Windows: `%APPDATA%\pueue\logs\`. `pueue log --json --full <id>` reads them for you.
- **`pueue wait` returns 0 even if the waited-on tasks failed.** It blocks until terminal, period. To know success/failure, query `pueue status --json` afterward (or use `wait.py`, which does this for you and returns a non-zero exit on failures).
- **`pueue remove` requires each id as a separate positional arg** (not a space-joined string from a subshell). Use bash arrays: `IDS=($(...)) && pueue remove "${IDS[@]}"`.
- **`pueue clean --successful-only` is a flag, not the default.** Plain `pueue clean` removes ALL finished tasks (including failures, which you may want to keep for debugging). Be deliberate.
- **`pueue add --print-task-id` writes the bare integer to stdout.** No JSON, no prose. `submit.sh` parses with a regex that tolerates future format changes; if you bypass `submit.sh`, capture with `ID=$(pueue add --print-task-id ... )` directly.
- **A terminal result that isn't `Success` is a failure — don't enumerate the bad ones.** `result` is a tagged enum, and `{"FailedToSpawn": "<os error>"}` is dict-shaped, not the bare string older notes implied. Matching a known-bad list let it fall through as success, so `wait.py` returned `0` for a task that never started. It now allowlists `Success` and fails closed on anything else.
- **Driving a remote daemon: the working directory is resolved on the CLIENT.** `pueue add` records and canonicalizes the cwd locally, so submitting to a remote daemon fails three different ways — local cwd sent (task ends `FailedToSpawn`), remote-only path rejected outright (`Failed to canonicalize given working directory path`), or a macOS symlink silently rewritten (`/tmp` → `/private/tmp`). Use `ssh host 'pueue add ...'` to submit; a forwarded client is for reading and control. See `references/remote-daemon.md`.
