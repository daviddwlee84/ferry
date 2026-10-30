#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""submit-dag.py — submit a YAML/JSON DAG of pueue tasks with success-only deps.

Spec format (YAML or JSON; auto-detected):

    version: 1
    default_group: ml          # optional; falls back to --default-group
    tasks:
      fetch:
        cmd: ./fetch.sh
        group: io              # optional; falls back to default_group
      featurize:
        cmd: python feat.py
        after: [fetch]
      train_a:
        cmd: python train.py --seed 1
        after: [featurize]
      train_b:
        cmd: python train.py --seed 2
        after: [featurize]
      evaluate:
        cmd: python eval.py
        after: [train_a, train_b]

The submitter:
  1. Parses the spec.
  2. Validates: missing `cmd`, unknown `after` references, cycles.
  3. Topologically sorts.
  4. Submits in topo order, mapping name→pueue-task-id.
  5. Wires `--after` from already-submitted ids.

No partial submits: validation runs *before* any `pueue add` call. Mid-run
pueue failures (exit 3) still flush the partial id map to stdout so callers
can clean up.

Exit codes:
  0  all tasks submitted
  1  schema error (missing cmd, unknown after, cycle, empty tasks)
  2  pueue CLI not installed
  3  mid-run pueue add failure (partial map emitted)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="submit-dag.py",
        description="Submit a YAML/JSON DAG of pueue tasks (success-only deps).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  submit-dag.py dag.yaml
  submit-dag.py - < dag.json
  submit-dag.py dag.yaml --label-prefix nightly- --default-group ml --dry-run
""",
    )
    p.add_argument("spec_path", help="Path to YAML/JSON spec, or '-' for stdin.")
    p.add_argument("--format", choices=["yaml", "json", "auto"], default="auto",
                   help="Spec format (default: auto-detect from extension or content).")
    p.add_argument("--default-group", default=None,
                   help="Fallback group when a task or spec doesn't set one.")
    p.add_argument("--label-prefix", default="",
                   help="Prepend STR to each task's `name` to form its pueue label.")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate + print plan; submit nothing.")
    p.add_argument("--print-graph", action="store_true",
                   help="Print topo order, edges, and per-task `name -> id` "
                        "lines to stderr. Default emits only the JSON map on "
                        "stdout (plus DAG-width warnings if applicable).")
    p.add_argument("--auto-parallel", action="store_true",
                   help="Before submitting, set each group's `parallel_tasks` "
                        "to at least the DAG's max width in that group. "
                        "Without this flag, the script only warns. Mutates "
                        "the target groups' config — prefer --isolated-group "
                        "if you don't want that side effect.")
    p.add_argument("--isolated-group", nargs="?", const="__auto__", default=None,
                   metavar="NAME",
                   help="Run the whole DAG in a fresh dedicated group sized "
                        "to the DAG width. NAME is auto-generated from spec "
                        "content + timestamp if omitted. Per-task `group:` "
                        "overrides in the spec are ignored under this flag — "
                        "isolation means one group for the run. After the "
                        "run, `pueue group remove <NAME>` to clean up "
                        "(remaining tasks move to default).")
    return p.parse_args()


def load_spec(path: str, fmt: str) -> dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read()
        source = "<stdin>"
    else:
        p = Path(path)
        if not p.is_file():
            print(f"error: spec file not found: {path}", file=sys.stderr)
            sys.exit(1)
        raw = p.read_text()
        source = str(p)

    if fmt == "auto":
        if path != "-" and path.endswith(".json"):
            fmt = "json"
        elif raw.lstrip().startswith(("{", "[")):
            fmt = "json"
        else:
            fmt = "yaml"

    try:
        if fmt == "json":
            spec = json.loads(raw)
        else:
            spec = yaml.safe_load(raw)
    except (yaml.YAMLError, json.JSONDecodeError) as e:
        print(f"error: failed to parse {source} as {fmt}: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(spec, dict):
        print(f"error: {source}: top-level must be a mapping", file=sys.stderr)
        sys.exit(1)
    return spec


def validate(spec: dict[str, Any], default_group_arg: str | None) -> tuple[dict[str, dict[str, Any]], list[str], str | None]:
    """Returns (task_map, topo_order, default_group). Exits 1 on any problem."""
    tasks = spec.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        print("error: spec must contain a non-empty `tasks:` mapping", file=sys.stderr)
        sys.exit(1)

    name_re = re.compile(r"^[A-Za-z0-9._-]+$")
    cleaned: dict[str, dict[str, Any]] = {}
    for name, body in tasks.items():
        if not isinstance(name, str) or not name_re.match(name):
            print(f"error: task name '{name}' is not [A-Za-z0-9._-]+", file=sys.stderr)
            sys.exit(1)
        if not isinstance(body, dict):
            print(f"error: task '{name}' must be a mapping", file=sys.stderr)
            sys.exit(1)
        cmd = body.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            print(f"error: task '{name}' missing string `cmd`", file=sys.stderr)
            sys.exit(1)
        after = body.get("after", [])
        if after is None:
            after = []
        if not isinstance(after, list):
            print(f"error: task '{name}'.after must be a list", file=sys.stderr)
            sys.exit(1)
        for ref in after:
            if not isinstance(ref, str):
                print(f"error: task '{name}'.after has non-string entry: {ref!r}",
                      file=sys.stderr)
                sys.exit(1)
            if ref not in tasks:
                print(f"error: task '{name}'.after references unknown task '{ref}'",
                      file=sys.stderr)
                sys.exit(1)
            if ref == name:
                print(f"error: task '{name}' depends on itself", file=sys.stderr)
                sys.exit(1)
        group = body.get("group")
        if group is not None and not isinstance(group, str):
            print(f"error: task '{name}'.group must be a string", file=sys.stderr)
            sys.exit(1)
        cleaned[name] = {
            "cmd": cmd,
            "after": list(after),
            "group": group,
        }

    # Resolve default_group: CLI flag wins over spec field.
    default_group = default_group_arg or spec.get("default_group")
    if default_group is not None and not isinstance(default_group, str):
        print("error: default_group must be a string", file=sys.stderr)
        sys.exit(1)

    # Topo sort (Kahn). Detect cycles.
    in_deg = {n: 0 for n in cleaned}
    edges: dict[str, list[str]] = {n: [] for n in cleaned}
    for n, body in cleaned.items():
        for ref in body["after"]:
            edges[ref].append(n)
            in_deg[n] += 1

    ready = [n for n, d in in_deg.items() if d == 0]
    ready.sort()  # deterministic order
    topo: list[str] = []
    while ready:
        cur = ready.pop(0)
        topo.append(cur)
        for nxt in edges[cur]:
            in_deg[nxt] -= 1
            if in_deg[nxt] == 0:
                ready.append(nxt)
        ready.sort()

    if len(topo) != len(cleaned):
        unresolved = [n for n, d in in_deg.items() if d > 0]
        print(f"error: cycle detected involving tasks: {unresolved}", file=sys.stderr)
        sys.exit(1)

    return cleaned, topo, default_group


def compute_max_width_per_group(cleaned: dict[str, dict[str, Any]],
                                topo: list[str],
                                default_group: str | None) -> dict[str, int]:
    """Per-group lower-bound on max parallel tasks across the DAG.

    Computes each task's longest-path level from a source, groups tasks by
    (group, level), and returns the max group-size per group. This is the
    minimum group `parallel_tasks` needed for the DAG's fan-out to actually
    run in parallel rather than be serialized by group capacity.
    """
    levels: dict[str, int] = {}
    for name in topo:
        deps = cleaned[name]["after"]
        levels[name] = max((levels[d] + 1 for d in deps), default=0)
    by_group_level: dict[str, dict[int, list[str]]] = {}
    for name, lvl in levels.items():
        g = cleaned[name]["group"] or default_group or "default"
        by_group_level.setdefault(g, {}).setdefault(lvl, []).append(name)
    return {g: max(len(tasks) for tasks in lvls.values())
            for g, lvls in by_group_level.items()}


def submit_one(name: str, body: dict[str, Any], after_ids: list[int],
               default_group: str | None, label_prefix: str,
               dry_run: bool, verbose: bool) -> int | None:
    label = f"{label_prefix}{name}"
    group = body["group"] or default_group
    pueue_args = ["pueue", "add", "--print-task-id", "--label", label]
    if group:
        pueue_args += ["--group", group]
    for aid in after_ids:
        pueue_args += ["--after", str(aid)]
    pueue_args += ["--", body["cmd"]]

    if dry_run:
        if verbose:
            print(f"  {name} -> [dry-run] {' '.join(pueue_args)}", file=sys.stderr)
        return None

    # Auto-create group if missing.
    if group:
        gp = subprocess.run(["pueue", "group", "--json"], capture_output=True, text=True)
        if gp.returncode == 0:
            existing = set(json.loads(gp.stdout).keys())
            if group not in existing:
                subprocess.run(["pueue", "group", "add", group],
                               capture_output=True, text=True, check=False)

    proc = subprocess.run(pueue_args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"error: `pueue add` failed for task '{name}': {proc.stdout.strip() or proc.stderr.strip()}",
              file=sys.stderr)
        return -1
    out = proc.stdout.strip()
    digits = "".join(ch for ch in out if ch.isdigit() or ch == "\n").strip().splitlines()
    digits = [d for d in digits if d.isdigit()]
    if not digits:
        print(f"error: couldn't parse task id from `pueue add` output for '{name}': {out!r}",
              file=sys.stderr)
        return -1
    return int(digits[-1])


def main() -> int:
    args = parse_args()

    if not args.dry_run and not shutil.which("pueue"):
        print("error: pueue CLI not found on PATH", file=sys.stderr)
        return 2

    if args.isolated_group is not None and args.default_group is not None:
        print("error: --isolated-group and --default-group are mutually exclusive",
              file=sys.stderr)
        return 1

    spec_raw = ""
    if args.spec_path != "-":
        p = Path(args.spec_path)
        if p.is_file():
            spec_raw = p.read_text()

    spec = load_spec(args.spec_path, args.format)
    cleaned, topo, default_group = validate(spec, args.default_group)

    isolated_group: str | None = None
    if args.isolated_group is not None:
        if args.isolated_group == "__auto__":
            import hashlib, time as _time
            seed = (spec_raw or json.dumps(spec, sort_keys=True)) + str(_time.time())
            digest = hashlib.sha256(seed.encode()).hexdigest()[:8]
            isolated_group = f"dag-{digest}"
        else:
            isolated_group = args.isolated_group
        # Override the resolved default + strip per-task group overrides so
        # everything lands in the isolated group.
        default_group = isolated_group
        for body in cleaned.values():
            body["group"] = None

    if args.print_graph or args.dry_run:
        print("topo order:", " -> ".join(topo), file=sys.stderr)
        for name in topo:
            after = cleaned[name]["after"]
            if after:
                print(f"  {name}: after={after}", file=sys.stderr)

    # Compare DAG fan-out width against each group's parallel_tasks.
    # Without enough slots, --after deps run correctly but siblings serialize.
    width_per_group = compute_max_width_per_group(cleaned, topo, default_group)
    width_warnings: list[str] = []
    if not args.dry_run and shutil.which("pueue"):
        gp = subprocess.run(["pueue", "group", "--json"], capture_output=True, text=True)
        if gp.returncode == 0:
            groups_state = json.loads(gp.stdout)
            # If --isolated-group, create the group sized to the DAG's width
            # for that group. No mutation of any other group.
            if isolated_group is not None and isolated_group not in groups_state:
                subprocess.run(["pueue", "group", "add", isolated_group],
                               capture_output=True, text=True, check=False)
                needed = width_per_group.get(isolated_group, 1)
                if needed > 1:
                    subprocess.run(["pueue", "parallel", str(needed),
                                    "--group", isolated_group],
                                   capture_output=True, text=True, check=False)
                print(f"isolated-group: created '{isolated_group}' "
                      f"with parallel_tasks={max(needed, 1)}",
                      file=sys.stderr)
            else:
                for g, needed in width_per_group.items():
                    have = groups_state.get(g, {}).get("parallel_tasks")
                    # parallel_tasks=0 means unlimited
                    if have is not None and have != 0 and have < needed and needed > 1:
                        msg = (f"warning: DAG fan-out width in group '{g}' is "
                               f"{needed}, but `parallel_tasks={have}` — "
                               f"siblings will serialize. Run: "
                               f"pueue parallel {needed} --group {g} "
                               f"(or pass --isolated-group / --auto-parallel)")
                        width_warnings.append(msg)
                        if args.auto_parallel:
                            subprocess.run(["pueue", "parallel", str(needed),
                                            "--group", g],
                                           capture_output=True, text=True,
                                           check=False)
                            print(f"auto-parallel: pueue parallel {needed} "
                                  f"--group {g}", file=sys.stderr)
                        else:
                            print(msg, file=sys.stderr)

    name_to_id: dict[str, int] = {}
    for idx, name in enumerate(topo):
        body = cleaned[name]
        after_ids = [name_to_id[ref] for ref in body["after"]]
        tid = submit_one(name, body, after_ids, default_group,
                         args.label_prefix, args.dry_run, args.print_graph)
        if args.dry_run:
            # Use synthetic ids so chained --after lookups resolve.
            name_to_id[name] = idx
            continue
        if tid is None or tid < 0:
            partial = {"tasks": name_to_id, "topo_order": topo,
                       "default_group": default_group,
                       "error": f"submit failed at task '{name}'"}
            print(json.dumps(partial))
            return 3
        name_to_id[name] = tid
        if args.print_graph:
            print(f"  {name} -> {tid}", file=sys.stderr)

    out = {
        "tasks": name_to_id,
        "topo_order": topo,
        "default_group": default_group,
        "width_per_group": width_per_group,
    }
    if isolated_group is not None:
        out["isolated_group"] = isolated_group
    if width_warnings and not args.auto_parallel and isolated_group is None:
        out["parallelism_warnings"] = width_warnings
    if args.dry_run:
        out["dry_run"] = True
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
