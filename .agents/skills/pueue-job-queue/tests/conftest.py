"""Shared pytest fixtures for pueue-job-queue tests.

The fixtures spin up an **isolated** `pueued` daemon under a tmpdir-scoped
config so the suite never touches the user's real queue. Each test session
gets its own daemon; tasks created during a test are cleaned between tests
via a `pueue clean`.

If `pueue` / `pueued` aren't on PATH, the entire suite is skipped.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Iterator

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
ASSETS_DIR = SKILL_ROOT / "assets"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _has_pueue() -> bool:
    return shutil.which("pueue") is not None and shutil.which("pueued") is not None


def pytest_collection_modifyitems(config, items):
    """Skip the entire suite when pueue isn't installed."""
    if _has_pueue():
        return
    skip_marker = pytest.mark.skip(reason="pueue/pueued not on PATH")
    for item in items:
        item.add_marker(skip_marker)


def _wait_socket(socket_path: Path, timeout: float = 5.0) -> None:
    """Poll until the daemon's unix socket exists (or timeout)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if socket_path.exists():
            # Try connecting to make sure pueued is actually accepting.
            try:
                with socket.socket(socket.AF_UNIX) as s:
                    s.settimeout(0.5)
                    s.connect(str(socket_path))
                return
            except OSError:
                pass
        time.sleep(0.1)
    raise TimeoutError(f"pueued socket {socket_path} never became ready")


@pytest.fixture(scope="session")
def pueue_env() -> Iterator[dict[str, str]]:
    """Start an isolated pueued daemon. Yields an env dict to pass to subprocess.

    Tests that shell out to `pueue` / our scripts must merge this dict into
    `os.environ` (or pass `env=pueue_env` to subprocess.run). The fixture
    handles teardown.
    """
    if not _has_pueue():
        pytest.skip("pueue/pueued not on PATH")

    # Resolve test root: PUEUE_TEST_DIR override (must be a real directory)
    # OR a fresh tmpdir under the system tempfile root. **Never** fall back to
    # the empty string / cwd — that resolves to the repo root and pollutes it
    # with pueued state files (shared_secret, certs, state.json).
    raw = os.environ.get("PUEUE_TEST_DIR", "").strip()
    cleanup = False
    if raw and Path(raw).is_dir():
        tmpdir = Path(raw)
    else:
        import tempfile
        tmpdir = Path(tempfile.mkdtemp(prefix="pueue-test-"))
        cleanup = True

    state_dir = tmpdir / "state"
    config_dir = tmpdir / "config"
    state_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "pueue.yml"
    config_path.write_text(
        f"""
shared:
  pueue_directory: {state_dir}
  use_unix_socket: true
daemon:
  default_parallel_tasks: 4
  pause_group_on_failure: false
""".lstrip(),
        encoding="utf-8",
    )

    env = {**os.environ, "PUEUE_CONFIG_PATH": str(config_path)}
    socket_path = state_dir / f"pueue_{os.environ.get('USER', 'test')}.socket"

    # Start the daemon in the background.
    daemon = subprocess.Popen(
        ["pueued", "--config", str(config_path)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        # Wait for the socket; fall back to polling `pueue status`.
        try:
            _wait_socket(socket_path, timeout=5.0)
        except TimeoutError:
            pass
        # Final readiness check: `pueue status` returns 0.
        deadline = time.monotonic() + 5.0
        ready = False
        while time.monotonic() < deadline:
            r = subprocess.run(
                ["pueue", "status", "--json"], env=env,
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                ready = True
                break
            time.sleep(0.1)
        if not ready:
            daemon.terminate()
            daemon.wait(timeout=2)
            raise RuntimeError("isolated pueued never became ready")

        yield env

    finally:
        # Graceful shutdown.
        subprocess.run(
            ["pueue", "shutdown"], env=env,
            capture_output=True, text=True,
        )
        try:
            daemon.wait(timeout=3)
        except subprocess.TimeoutExpired:
            daemon.terminate()
            try:
                daemon.wait(timeout=2)
            except subprocess.TimeoutExpired:
                daemon.kill()
        if cleanup:
            shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_between_tests(pueue_env):
    """Reset task list + unpause groups between tests.

    `pueue reset --force` is the cleanest reset: kills everything, removes
    tasks, and crucially **resumes paused groups**. Plain `pueue kill --all`
    + `clean` would leave groups paused (per `pueue kill --help`: "This
    also pauses all groups"), causing subsequent tests' tasks to sit Queued
    forever.
    """
    yield
    subprocess.run(
        ["pueue", "reset", "--force"], env=pueue_env,
        capture_output=True, text=True,
    )
    # `reset` may take a beat to settle the daemon state.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["pueue", "status", "--json"], env=pueue_env,
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            st = json.loads(r.stdout)
            if not st.get("tasks") and all(
                g.get("status") == "Running" for g in st.get("groups", {}).values()
            ):
                break
        time.sleep(0.1)


@pytest.fixture(scope="session")
def scripts_dir() -> Path:
    return SCRIPTS_DIR


@pytest.fixture(scope="session")
def assets_dir() -> Path:
    return ASSETS_DIR


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


def pueue_status(env: dict[str, str]) -> dict:
    """Helper: parse `pueue status --json` against the isolated daemon."""
    r = subprocess.run(
        ["pueue", "status", "--json"], env=env,
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def wait_for_terminal(env: dict[str, str], task_ids: list[int], timeout: float = 30.0) -> dict:
    """Helper: poll status until all `task_ids` are Done.* (or timeout)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = pueue_status(env)
        all_done = all(
            isinstance(st["tasks"].get(str(tid), {}).get("status"), dict)
            and "Done" in st["tasks"][str(tid)]["status"]
            for tid in task_ids
        )
        if all_done:
            return st
        time.sleep(0.2)
    raise TimeoutError(f"tasks {task_ids} never reached Done within {timeout}s")
