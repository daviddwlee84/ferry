"""Tests for scripts/wait.py — block-until-terminal poller."""
from __future__ import annotations

import json
import subprocess


def submit_quick(env, scripts_dir, label, cmd):
    """Submit one task, return task_id."""
    r = subprocess.run(
        ["bash", str(scripts_dir / "submit.sh"), "--label", label, "--", cmd],
        env=env, capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)["task_id"]


def test_wait_success(pueue_env, scripts_dir):
    tid = submit_quick(pueue_env, scripts_dir, "wait-ok", "true")
    r = subprocess.run(
        [str(scripts_dir / "wait.py"), "--ids", str(tid), "--timeout-seconds", "20", "--quiet"],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["summary"]["total"] == 1
    assert out["summary"]["success"] == 1
    assert out["tasks"][0]["state"] == "Done"
    assert out["tasks"][0]["result"] == "Success"


def test_wait_failure_returns_5(pueue_env, scripts_dir):
    tid = submit_quick(pueue_env, scripts_dir, "wait-fail", "false")
    r = subprocess.run(
        [str(scripts_dir / "wait.py"), "--ids", str(tid), "--timeout-seconds", "20", "--quiet"],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 5
    out = json.loads(r.stdout)
    assert out["summary"]["failed"] == 1
    assert out["tasks"][0]["result"] == "Failed"
    assert out["tasks"][0]["exit_code"] == 1


def test_wait_timeout_returns_6(pueue_env, scripts_dir):
    tid = submit_quick(pueue_env, scripts_dir, "wait-timeout", "sleep 30")
    r = subprocess.run(
        [str(scripts_dir / "wait.py"), "--ids", str(tid), "--timeout-seconds", "1", "--quiet"],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 6, f"expected timeout (6), got {r.returncode}: {r.stderr}"


def test_wait_label_prefix(pueue_env, scripts_dir):
    submit_quick(pueue_env, scripts_dir, "lp-foo-1", "true")
    submit_quick(pueue_env, scripts_dir, "lp-foo-2", "true")
    submit_quick(pueue_env, scripts_dir, "lp-bar-1", "true")  # not selected
    r = subprocess.run(
        [
            str(scripts_dir / "wait.py"),
            "--label-prefix", "lp-foo-",
            "--timeout-seconds", "20", "--quiet",
        ],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["summary"]["total"] == 2


def test_wait_no_selectors_returns_1(pueue_env, scripts_dir):
    r = subprocess.run(
        [str(scripts_dir / "wait.py"), "--timeout-seconds", "5"],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 1


def test_wait_no_match_returns_1(pueue_env, scripts_dir):
    r = subprocess.run(
        [
            str(scripts_dir / "wait.py"),
            "--label", "definitely-no-such-task",
            "--timeout-seconds", "5",
        ],
        env=pueue_env, capture_output=True, text=True,
    )
    assert r.returncode == 1
