# DAG patterns with `submit-dag.py`

`scripts/submit-dag.py` reads a YAML or JSON spec and submits all tasks with
their `--after` deps wired up. Pueue's `--after` is **AND-only** and
**success-only**: a task runs only after **every** parent finishes with exit
0. If any parent fails, the dependent's status becomes `DependencyFailed`
and it never runs.

These are the shapes that map cleanly onto `--after`. For anything more
complex, see "When to escalate" at the bottom.

## Linear chain (each task waits for the prior)

```yaml
tasks:
  download:  { cmd: ./download.sh }
  unpack:    { cmd: ./unpack.sh,  after: [download] }
  validate:  { cmd: ./validate.sh, after: [unpack] }
  upload:    { cmd: ./upload.sh,   after: [validate] }
```

Effective parallelism = 1 (one task running at any time). Set the group's
`parallel_tasks` ≥ 1 — anything more doesn't help, since the chain is
serial.

## Fan-out

```yaml
tasks:
  prepare:   { cmd: ./prepare.sh }
  shard_a:   { cmd: ./run.sh --shard a, after: [prepare], group: workers }
  shard_b:   { cmd: ./run.sh --shard b, after: [prepare], group: workers }
  shard_c:   { cmd: ./run.sh --shard c, after: [prepare], group: workers }
  shard_d:   { cmd: ./run.sh --shard d, after: [prepare], group: workers }
```

Set `pueue parallel 4 --group workers` for true 4-way parallelism. Without
that, pueue runs them one at a time despite the dependency graph allowing
parallelism.

## Fan-in

```yaml
tasks:
  collect_a: { cmd: ./collect.sh --src a }
  collect_b: { cmd: ./collect.sh --src b }
  collect_c: { cmd: ./collect.sh --src c }
  merge:     { cmd: ./merge.sh, after: [collect_a, collect_b, collect_c] }
```

`merge` runs only after **all three** collectors succeed. Any collector
failing → `merge` becomes `DependencyFailed`.

## Diamond (fan-out then fan-in)

```yaml
tasks:
  fetch:     { cmd: ./fetch.sh }
  process_a: { cmd: ./proc_a.sh, after: [fetch] }
  process_b: { cmd: ./proc_b.sh, after: [fetch] }
  combine:   { cmd: ./combine.sh, after: [process_a, process_b] }
```

This is the shape `assets/dag.example.yaml` ships (with one extra layer).
The diamond is the most common shape in practice — a "do these N things in
parallel after step X, then merge".

## Mixed sequential + parallel

```yaml
tasks:
  setup:         { cmd: ./setup.sh }

  # Three independent train jobs after setup
  train_seed_1:  { cmd: ./train.sh --seed 1, after: [setup], group: gpu }
  train_seed_2:  { cmd: ./train.sh --seed 2, after: [setup], group: gpu }
  train_seed_3:  { cmd: ./train.sh --seed 3, after: [setup], group: gpu }

  # Eval needs all three trains
  evaluate:      { cmd: ./evaluate.sh, after: [train_seed_1, train_seed_2, train_seed_3] }

  # Cleanup runs even if eval fails — but pueue can't express that with --after.
  # See "When to escalate" below.
  upload:        { cmd: ./upload_results.sh, after: [evaluate] }
```

If `gpu` group has `parallel_tasks: 1`, the three trains run sequentially
despite the DAG shape allowing parallelism. Bump it to 3 for fan-out
behavior.

## Cross-group dependencies are fine

A task in group `cpu` can depend on a task in group `gpu`. The dependency
is enforced regardless of group membership; groups only control parallelism
slots.

```yaml
tasks:
  build_index:  { cmd: ./index.sh,    group: cpu }
  serve_query:  { cmd: ./query.sh,    group: gpu, after: [build_index] }
```

## What you CAN'T express in `--after`

These shapes are not supported by pueue's dependency model:

- **OR dependencies** — "run when *any* of these parents succeeds." Pueue
  is AND-only.
- **Conditional / if-then** — "run B if A succeeds; run C if A fails."
  The "if A fails" branch can't be wired with `--after` (which is
  success-only).
- **Run-on-failure / cleanup tasks** — "always run cleanup, even if the
  pipeline failed." Pueue has no equivalent of `try/finally`.
- **Retry-with-backoff** — `--after` chains don't retry; you'd have to
  manually `pueue restart --in-place <failed_id>` and the dependents would
  remain stuck in `DependencyFailed` (you'd also have to restart them).
- **Dynamic / runtime-generated tasks** — the spec must be fully known at
  submit time. You can't decide "submit task X only if task A's output has
  property Y."
- **Parameter sweeps with input mapping** — pueue doesn't pass outputs
  between tasks. Each task's command is a fixed string.

## When to escalate to a real orchestrator

| You need | Use |
|---|---|
| OR-deps, conditional branching, run-on-failure | **Prefect**, **Dagster**, **Airflow** |
| Distributed scheduling (jobs land on N hosts) | **Slurm**, **Kubernetes Jobs**, **Ray** |
| Typed task IO + cached intermediates | **DVC** (`dvc exp run --queue`), **Snakemake**, **Nextflow**, **Prefect** |
| ML hyperparameter sweeps with metric tracking | **DVC + queue**, **Optuna**, **Ray Tune**, **MLflow** |
| Long-running services / always-on | **systemd**, **launchd**, **supervisord** |
| Cron-style recurring schedules | **systemd timers**, **cron**, **Prefect schedules** |

If you're hitting two or three of those, switch tools — pueue is great for
flexible shell-job batching but it's not pretending to be Airflow.

## The `--label-prefix` convention

Pass `--label-prefix nightly-` (for example) to `submit-dag.py` and every
task's pueue label becomes `nightly-<name>`. Then `wait.py --label-prefix
nightly-` blocks on the whole DAG, and `pueue clean` can be filtered by
label later. This is the recommended workflow for repeatable DAGs that
might be submitted multiple times.

## Spec validation

`submit-dag.py` validates the spec **before any `pueue add` runs**:

- top-level must have `tasks:` (non-empty mapping)
- each task name matches `[A-Za-z0-9._-]+`
- each task has a non-empty string `cmd`
- `after:` is a list of strings (or absent)
- every name in `after:` exists in `tasks`
- no self-dependency, no cycle

Validation failure → exit 1, no tasks submitted. **Mid-run pueue failures**
(e.g. a transient daemon hiccup) → exit 3, but the partial name→id map is
still printed to stdout so you can clean up:

```bash
PARTIAL=$(submit-dag.py dag.yaml || cat)
echo "$PARTIAL" | jq -r '.tasks | values[]' | xargs -n1 pueue remove
```
