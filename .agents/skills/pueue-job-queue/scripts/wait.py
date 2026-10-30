#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""wait.py — block until pueue tasks reach terminal state; emit JSON summary.

Selectors (one or more required):
  --ids 1,2,3
  --label LABEL          (repeatable)
  --label-prefix STR
  --group GROUP

The script polls `pueue status --json` every --poll-seconds and exits when
all selected tasks are in a terminal state (Done.*) or --timeout-seconds
elapses, whichever first. Output on stdout is a JSON summary; stderr is
quiet by default (one initial line + state-change events). `--verbose`
restores per-tick stderr.

Exit codes:
  0  all selected tasks finished with result==Success
  1  invalid arguments
  4  daemon unreachable
  5  >= 1 task ended Failed/Killed/DependencyFailed
  6  timeout
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from typing import Any

TERMINAL_RESULTS_GOOD = {"Success"}
# Anything terminal that is not Success is a failure. Listing the bad variants
# explicitly used to mean an unlisted variant silently counted as success --
# `FailedToSpawn` (dict-shaped: {"FailedToSpawn": "<os error>"}) hit exactly
# that path, so a task that never even started reported exit 0. Treat the GOOD
# set as the allowlist instead, so future pueue variants fail closed.
TERMINAL_RESULTS_BAD = {"DependencyFailed", "Killed", "FailedToSpawn"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="wait.py",
        description="Block until pueue tasks reach terminal state; emit JSON summary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  wait.py --ids 17,18,19 --timeout-seconds 300
  wait.py --label-prefix sweep- --group ml --fail-fast
  wait.py --group default --quiet
""",
    )
    p.add_argument("--ids", help="Comma-separated task ids (e.g. 1,2,3).")
    p.add_argument("--label", action="append", default=[], help="Exact label match. Repeatable.")
    p.add_argument("--label-prefix", help="Match all tasks whose label starts with STR.")
    p.add_argument("--group", help="Match all tasks in GROUP.")
    p.add_argument("--poll-seconds", type=float, default=2.0, help="Poll cadence (default 2.0).")
    p.add_argument("--timeout-seconds", type=float, default=0.0,
                   help="Wall-clock timeout in seconds. 0 = no timeout (default).")
    p.add_argument("--fail-fast", action="store_true",
                   help="Return non-zero immediately when any selected task fails.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Print one line per poll tick to stderr (debug only). "
                        "Default emits state-change events only.")
    # --quiet kept as no-op for backwards compat; default is already quiet.
    p.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args()


def get_status() -> dict[str, Any]:
    if not shutil.which("pueue"):
        print("error: pueue CLI not found on PATH", file=sys.stderr)
        sys.exit(4)
    try:
        proc = subprocess.run(
            ["pueue", "status", "--json"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        print("error: pueue CLI not found", file=sys.stderr)
        sys.exit(4)
    if proc.returncode != 0:
        print(f"error: pueued unreachable: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(4)
    return json.loads(proc.stdout)


def select_ids(args: argparse.Namespace, status: dict[str, Any]) -> list[int]:
    """Resolve selectors to a concrete set of task ids using current status."""
    if not (args.ids or args.label or args.label_prefix or args.group):
        print("error: at least one of --ids/--label/--label-prefix/--group is required",
              file=sys.stderr)
        sys.exit(1)

    selected: set[int] = set()
    tasks = status.get("tasks", {})

    if args.ids:
        for tok in args.ids.split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                selected.add(int(tok))
            except ValueError:
                print(f"error: --ids: '{tok}' is not an integer", file=sys.stderr)
                sys.exit(1)

    for tid_str, task in tasks.items():
        tid = int(tid_str)
        label = task.get("label") or ""
        group = task.get("group") or ""
        if args.label and label in args.label:
            selected.add(tid)
        if args.label_prefix and label.startswith(args.label_prefix):
            selected.add(tid)
        if args.group and group == args.group:
            selected.add(tid)

    return sorted(selected)


def classify_status(status_obj: Any) -> tuple[str, str | None, int | None]:
    """Reduce pueue's tagged-enum status to (state, result, exit_code).

    state ∈ {"Queued","Running","Stashed","Paused","Locked","Done","Unknown"}
    result is set only when state == "Done"; None otherwise.
    exit_code is set only when result is a Failed-with-code value; None otherwise.
    """
    if not isinstance(status_obj, dict) or not status_obj:
        return "Unknown", None, None
    # status is e.g. {"Done": {"result": "Success", ...}} or {"Running": {...}}
    state, payload = next(iter(status_obj.items()))
    if state != "Done":
        return state, None, None
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, str):
        return "Done", result, None
    if isinstance(result, dict):
        # e.g. {"Failed": 1}
        if "Failed" in result:
            return "Done", "Failed", int(result["Failed"])
        # Unknown variant — surface the key.
        return "Done", next(iter(result), "Unknown"), None
    return "Done", None, None


def is_terminal(state: str) -> bool:
    return state == "Done"


def is_failure(summary: dict[str, Any]) -> bool:
    """True when a task reached a terminal state that is not Success.

    Allowlist, not denylist: only `Success` counts as good. Pueue's result is a
    tagged enum, and an unrecognized future variant must read as failure rather
    than silently passing (see TERMINAL_RESULTS_BAD).
    """
    return summary["state"] == "Done" and summary["result"] not in TERMINAL_RESULTS_GOOD


def task_summary(task: dict[str, Any]) -> dict[str, Any]:
    state, result, exit_code = classify_status(task.get("status") or {})
    payload = task.get("status", {}).get(state, {}) if isinstance(task.get("status"), dict) else {}
    return {
        "id": task["id"],
        "label": task.get("label"),
        "group": task.get("group"),
        "state": state,
        "result": result,
        "exit_code": exit_code,
        "start": payload.get("start") if isinstance(payload, dict) else None,
        "end": payload.get("end") if isinstance(payload, dict) else None,
    }


def main() -> int:
    args = parse_args()

    initial = get_status()
    ids = select_ids(args, initial)
    if not ids:
        print("error: selectors matched zero tasks", file=sys.stderr)
        sys.exit(1)

    print(f"waiting on {len(ids)} task(s): {ids}", file=sys.stderr)

    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    started = time.monotonic()
    tick = 0
    last_summaries: list[dict[str, Any]] = []
    prev_state: dict[int, str] = {}  # task id → last seen state

    while True:
        tick += 1
        status = get_status()
        tasks = status.get("tasks", {})
        present = [tasks[str(i)] for i in ids if str(i) in tasks]
        if len(present) != len(ids):
            missing = [i for i in ids if str(i) not in tasks]
            print(f"error: tasks vanished from queue: {missing}", file=sys.stderr)
            sys.exit(5)

        summaries = [task_summary(t) for t in present]
        last_summaries = summaries

        # Emit state-change events on stderr (quiet by default; one line per
        # transition rather than per tick).
        for s in summaries:
            cur = s["state"] if s["state"] != "Done" else f"Done.{s['result']}"
            prev = prev_state.get(s["id"])
            if prev != cur:
                if prev is None:
                    msg = f"task {s['id']} ({s['label']}): {cur}"
                else:
                    msg = f"task {s['id']} ({s['label']}): {prev} -> {cur}"
                print(msg, file=sys.stderr)
                prev_state[s["id"]] = cur

        states = [s["state"] for s in summaries]
        if args.verbose:
            n_done = sum(1 for x in states if x == "Done")
            n_running = sum(1 for x in states if x == "Running")
            n_queued = sum(1 for x in states if x == "Queued")
            n_other = len(states) - n_done - n_running - n_queued
            print(
                f"tick {tick}: {n_done} done, {n_running} running, {n_queued} queued, {n_other} other",
                file=sys.stderr,
            )

        if args.fail_fast and any(is_failure(s) for s in summaries):
            emit_summary(summaries, started)
            return 5

        if all(is_terminal(s) for s in states):
            break

        if deadline is not None and time.monotonic() >= deadline:
            print("timeout reached", file=sys.stderr)
            emit_summary(summaries, started)
            return 6

        time.sleep(args.poll_seconds)

    return emit_summary_and_exit(last_summaries, started)


def emit_summary(summaries: list[dict[str, Any]], started: float) -> None:
    counts = {"total": len(summaries), "success": 0, "failed": 0, "killed": 0, "dependency_failed": 0, "failed_to_spawn": 0, "running": 0, "other": 0}
    for s in summaries:
        if s["state"] != "Done":
            counts["running"] += 1 if s["state"] == "Running" else 0
            counts["other"] += 1 if s["state"] != "Running" else 0
            continue
        r = s["result"]
        if r == "Success":
            counts["success"] += 1
        elif r == "Failed":
            counts["failed"] += 1
        elif r == "Killed":
            counts["killed"] += 1
        elif r == "DependencyFailed":
            counts["dependency_failed"] += 1
        elif r == "FailedToSpawn":
            counts["failed_to_spawn"] += 1
        else:
            counts["other"] += 1
    elapsed = round(time.monotonic() - started, 3)
    print(json.dumps({"summary": counts, "tasks": summaries, "elapsed_seconds": elapsed}))


def emit_summary_and_exit(summaries: list[dict[str, Any]], started: float) -> int:
    emit_summary(summaries, started)
    bad = sum(1 for s in summaries if is_failure(s))
    return 5 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
