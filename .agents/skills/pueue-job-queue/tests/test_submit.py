"""Tests for scripts/submit.sh — single-task pueue submission."""
from __future__ import annotations

import json
import subprocess

from conftest import pueue_status, wait_for_terminal


def run_submit(env, *args):
    """Run submit.sh against the isolated daemon and return parsed JSON."""
    cmd = ["bash", str(env["__SUBMIT_SH"]), *args]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
    return json.loads(r.stdout), r


def test_submit_minimal(pueue_env, scripts_dir):
    pueue_env["__SUBMIT_SH"] = str(scripts_dir / "submit.sh")
    result, proc = run_submit(pueue_env, "--label", "t-min", "--", "true")
    assert isinstance(result["task_id"], int)
    assert result["label"] == "t-min"
    assert result["group"] == "default"
    assert result["after"] == []
    assert result["immediate"] is False

    st = wait_for_terminal(pueue_env, [result["task_id"]])
    task = st["tasks"][str(result["task_id"])]
    assert task["status"]["Done"]["result"] == "Success"


def test_submit_with_after(pueue_env, scripts_dir):
    pueue_env["__SUBMIT_SH"] = str(scripts_dir / "submit.sh")
    parent, _ = run_submit(pueue_env, "--label", "parent", "--", "true")
    child, _ = run_submit(
        pueue_env, "--label", "child", "--after", str(parent["task_id"]), "--", "true",
    )
    assert child["after"] == [parent["task_id"]]
    st = wait_for_terminal(pueue_env, [parent["task_id"], child["task_id"]])
    assert st["tasks"][str(child["task_id"])]["dependencies"] == [parent["task_id"]]
    assert st["tasks"][str(child["task_id"])]["status"]["Done"]["result"] == "Success"


def test_submit_dependency_failure_propagates(pueue_env, scripts_dir):
    pueue_env["__SUBMIT_SH"] = str(scripts_dir / "submit.sh")
    parent, _ = run_submit(pueue_env, "--label", "fail-parent", "--", "false")
    child, _ = run_submit(
        pueue_env, "--label", "fail-child", "--after", str(parent["task_id"]), "--", "true",
    )
    st = wait_for_terminal(pueue_env, [parent["task_id"], child["task_id"]])
    parent_result = st["tasks"][str(parent["task_id"])]["status"]["Done"]["result"]
    child_result = st["tasks"][str(child["task_id"])]["status"]["Done"]["result"]
    assert isinstance(parent_result, dict) and "Failed" in parent_result
    assert child_result == "DependencyFailed"


def test_submit_autocreates_group(pueue_env, scripts_dir):
    pueue_env["__SUBMIT_SH"] = str(scripts_dir / "submit.sh")
    # New group that definitely doesn't exist in the isolated daemon
    new_group = "test-autocreate-x9"
    result, _ = run_submit(
        pueue_env, "--label", "ag", "--group", new_group, "--", "true",
    )
    assert result["group"] == new_group
    # Verify group was created in daemon
    r = subprocess.run(
        ["pueue", "group", "--json"], env=pueue_env,
        capture_output=True, text=True, check=True,
    )
    groups = json.loads(r.stdout)
    assert new_group in groups
    wait_for_terminal(pueue_env, [result["task_id"]])


def test_submit_dry_run_emits_no_task(pueue_env, scripts_dir):
    pueue_env["__SUBMIT_SH"] = str(scripts_dir / "submit.sh")
    before = pueue_status(pueue_env)
    n_before = len(before["tasks"])
    result, _ = run_submit(pueue_env, "--label", "dry", "--dry-run", "--", "true")
    assert result["task_id"] is None
    assert result["dry_run"] is True
    after = pueue_status(pueue_env)
    assert len(after["tasks"]) == n_before


def test_submit_invalid_after_returns_3(pueue_env, scripts_dir):
    submit_sh = scripts_dir / "submit.sh"
    r = subprocess.run(
        ["bash", str(submit_sh), "--after", "999999", "--", "true"],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 3, f"expected exit 3 (pueue add failed), got {r.returncode}: {r.stderr}"


def test_submit_no_command_returns_1(pueue_env, scripts_dir):
    submit_sh = scripts_dir / "submit.sh"
    r = subprocess.run(
        ["bash", str(submit_sh)],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 1
