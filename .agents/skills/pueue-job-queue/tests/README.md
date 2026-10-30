# Tests for pueue-job-queue

Run the full suite:

```bash
# From repo root
uv run --extra dev pytest skills/local/pueue-job-queue/tests/ -q
bash skills/local/pueue-job-queue/tests/test_contracts.sh
```

The pytest suite spins up an **isolated** `pueued` under a tempdir-scoped
config — your real queue is never touched. Skipped entirely if `pueue` /
`pueued` aren't on `PATH`.

## Layout

- `conftest.py` — `pueue_env` session fixture starts an isolated daemon and
  yields an env dict. The `_clean_between_tests` autouse fixture wipes
  tasks between tests (kill + clean).
- `fixtures/simple-dag.yaml` — 4-task diamond used by `test_dag.py`.
- `test_submit.py` — `submit.sh` paths: minimal, `--after` chain, dependency
  failure propagation, group autocreate, dry-run, error paths.
- `test_dag.py` — `submit-dag.py` paths: dry-run validation, end-to-end submit
  + topo invariant check (each child's `start` ≥ parent's `end`), cycle
  detection, unknown `after`, missing `cmd`, stdin spec.
- `test_wait.py` — `wait.py` paths: success/failure/timeout exit codes
  (0/5/6), label-prefix selector, no-selector arg error.
- `test_contracts.sh` — bash exit-code contract: every script supports
  `--help` (exit 0); arg errors return 1; `check-daemon.sh` against an
  unreachable daemon returns 3.

## Requirements

- `uv` (for `uv run --extra dev pytest`).
- `pueue` and `pueued` on `PATH`. The whole suite is auto-skipped if either
  is missing.
- Tests use ports / sockets only under `tempfile.mkdtemp(prefix="pueue-test-")`;
  no host-wide config is touched.

## Why these levels

The skill manipulates a stateful daemon over a JSON-RPC-ish wire — three
regressions could happen silently and only the tests catch them:

| Regression                                                        | Caught by |
|-------------------------------------------------------------------|-----------|
| `submit.sh` parses non-integer task id (e.g. JSON-wrapped output) | `test_submit.test_submit_minimal` |
| `--group X` doesn't autocreate, future pueue version regresses    | `test_submit.test_submit_autocreates_group` |
| `submit-dag.py` submits even on cycle / unknown `after`           | `test_dag.test_dag_cycle_detected`, `test_dag_unknown_after` |
| `wait.py` mis-classifies `{"Failed": N}` vs `"Failed"` string     | `test_wait.test_wait_failure_returns_5` |
| Status enum gains a new variant (e.g. Pueue 5.x)                  | `test_wait_*` start failing — re-verify schema |
