"""Tests for scripts/submit-dag.py — declarative DAG submission."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime

from conftest import pueue_status, wait_for_terminal


def parse_iso(s):
    return datetime.fromisoformat(s)


def test_dag_dry_run_validates(pueue_env, scripts_dir, fixtures_dir):
    submit_dag = scripts_dir / "submit-dag.py"
    r = subprocess.run(
        [str(submit_dag), str(fixtures_dir / "simple-dag.yaml"), "--dry-run", "--print-graph"],
        env=pueue_env, capture_output=True, text=True, check=True,
    )
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert set(out["tasks"].keys()) == {"a", "b", "c", "d"}
    assert out["topo_order"][0] == "a"
    # d must come last (depends on b and c)
    assert out["topo_order"][-1] == "d"


def test_dag_submission_topo_invariant(pueue_env, scripts_dir, fixtures_dir):
    submit_dag = scripts_dir / "submit-dag.py"
    r = subprocess.run(
        [str(submit_dag), str(fixtures_dir / "simple-dag.yaml"), "--label-prefix", "dag-test-"],
        env=pueue_env, capture_output=True, text=True, check=True,
    )
    out = json.loads(r.stdout)
    name_to_id = out["tasks"]
    assert set(name_to_id.keys()) == {"a", "b", "c", "d"}

    # Wait for all to finish
    wait_for_terminal(pueue_env, list(name_to_id.values()), timeout=30)

    # Topo invariant: a's end <= b's start, a's end <= c's start, b's end <= d's start, c's end <= d's start
    st = pueue_status(pueue_env)
    a_end = parse_iso(st["tasks"][str(name_to_id["a"])]["status"]["Done"]["end"])
    b_start = parse_iso(st["tasks"][str(name_to_id["b"])]["status"]["Done"]["start"])
    c_start = parse_iso(st["tasks"][str(name_to_id["c"])]["status"]["Done"]["start"])
    b_end = parse_iso(st["tasks"][str(name_to_id["b"])]["status"]["Done"]["end"])
    c_end = parse_iso(st["tasks"][str(name_to_id["c"])]["status"]["Done"]["end"])
    d_start = parse_iso(st["tasks"][str(name_to_id["d"])]["status"]["Done"]["start"])

    assert a_end <= b_start, "b must start after a finishes"
    assert a_end <= c_start, "c must start after a finishes"
    assert b_end <= d_start, "d must start after b finishes"
    assert c_end <= d_start, "d must start after c finishes"


def test_dag_cycle_detected(pueue_env, scripts_dir, tmp_path):
    submit_dag = scripts_dir / "submit-dag.py"
    spec = tmp_path / "cycle.yaml"
    spec.write_text(
        "tasks:\n"
        "  a: { cmd: 'true', after: [b] }\n"
        "  b: { cmd: 'true', after: [a] }\n"
    )
    r = subprocess.run(
        [str(submit_dag), str(spec)],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "cycle" in r.stderr.lower()


def test_dag_unknown_after(pueue_env, scripts_dir, tmp_path):
    submit_dag = scripts_dir / "submit-dag.py"
    spec = tmp_path / "unknown.yaml"
    spec.write_text("tasks:\n  a: { cmd: 'true', after: [ghost] }\n")
    r = subprocess.run(
        [str(submit_dag), str(spec)],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "ghost" in r.stderr


def test_dag_missing_cmd(pueue_env, scripts_dir, tmp_path):
    submit_dag = scripts_dir / "submit-dag.py"
    spec = tmp_path / "missing.yaml"
    spec.write_text("tasks:\n  a: {}\n")
    r = subprocess.run(
        [str(submit_dag), str(spec)],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "cmd" in r.stderr.lower()


def test_dag_stdin(pueue_env, scripts_dir):
    submit_dag = scripts_dir / "submit-dag.py"
    spec = "tasks:\n  only: { cmd: 'true' }\n"
    r = subprocess.run(
        [str(submit_dag), "-"],
        input=spec, env=pueue_env, capture_output=True, text=True, check=True,
    )
    out = json.loads(r.stdout)
    assert "only" in out["tasks"]


def test_dag_isolated_group_creates_fresh_group(pueue_env, scripts_dir, fixtures_dir):
    submit_dag = scripts_dir / "submit-dag.py"
    r = subprocess.run(
        [str(submit_dag), str(fixtures_dir / "simple-dag.yaml"),
         "--isolated-group", "--label-prefix", "iso-"],
        env=pueue_env, capture_output=True, text=True, check=True,
    )
    out = json.loads(r.stdout)
    isolated = out["isolated_group"]
    assert isolated.startswith("dag-")
    assert out["default_group"] == isolated

    # All tasks should be in the isolated group
    st_proc = subprocess.run(
        ["pueue", "status", "--json"], env=pueue_env,
        capture_output=True, text=True, check=True,
    )
    st = json.loads(st_proc.stdout)
    for tid in out["tasks"].values():
        assert st["tasks"][str(tid)]["group"] == isolated

    # The group's parallel_tasks should match DAG width (2 for diamond)
    gp_proc = subprocess.run(
        ["pueue", "group", "--json"], env=pueue_env,
        capture_output=True, text=True, check=True,
    )
    groups = json.loads(gp_proc.stdout)
    assert isolated in groups
    assert groups[isolated]["parallel_tasks"] >= 2


def test_dag_isolated_group_named(pueue_env, scripts_dir, fixtures_dir):
    submit_dag = scripts_dir / "submit-dag.py"
    r = subprocess.run(
        [str(submit_dag), str(fixtures_dir / "simple-dag.yaml"),
         "--isolated-group", "test-iso-x9", "--label-prefix", "named-"],
        env=pueue_env, capture_output=True, text=True, check=True,
    )
    out = json.loads(r.stdout)
    assert out["isolated_group"] == "test-iso-x9"


def test_dag_isolated_group_conflicts_with_default_group(pueue_env, scripts_dir, fixtures_dir):
    submit_dag = scripts_dir / "submit-dag.py"
    r = subprocess.run(
        [str(submit_dag), str(fixtures_dir / "simple-dag.yaml"),
         "--isolated-group", "x", "--default-group", "y"],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "mutually exclusive" in r.stderr
