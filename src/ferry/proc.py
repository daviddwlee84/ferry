"""Subprocess execution for backend CLIs: proxy-free environment, quiet logs.

Transfer tools print progress bars with carriage returns many times per second; those
lines are dropped from the log (only the final progress line of each run is kept) so a
multi-week transfer does not produce gigabytes of log.
"""
from __future__ import annotations

import contextvars
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .egress import clean_env

_PROGRESS = re.compile(r"[↑↓]|\d+(\.\d+)?%|Transferred:|ETA ")


#: Monotonic time at which a running transfer must be stopped (transfer window closes).
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("deadline", default=None)
#: Seconds without any output after which a running transfer is killed (hung backend).
_stall: contextvars.ContextVar[float | None] = contextvars.ContextVar("stall", default=None)


class WindowClosed(RuntimeError):
    """A transfer was stopped because its transfer window closed; retry in the next one."""


class Stalled(RuntimeError):
    """A transfer printed nothing for too long and was killed; treated as a failure."""


@contextmanager
def transfer_deadline(seconds: float | None):
    """Stop any run_transfer started inside this block after `seconds` (None: no limit)."""
    token = _deadline.set(None if seconds is None else time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


@contextmanager
def transfer_stall(seconds: float | None):
    """Kill any run_transfer inside this block once it is silent for `seconds`.

    Only meaningful for tools that print progress while transferring (BaiduPCS-Go does;
    rclone needs ``--stats``, rsync ``--info=progress2``; adapters add those flags when a
    stall timeout is set).
    """
    token = _stall.set(seconds)
    try:
        yield
    finally:
        _stall.reset(token)


class CommandError(RuntimeError):
    def __init__(self, argv: list[str], code: int, output: str):
        tail = "\n".join(output.strip().splitlines()[-5:])
        super().__init__(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} exited {code}: {tail}")
        self.argv, self.code, self.output = argv, code, output


def run(argv: list[str], *, log: Path | None = None, timeout: float | None = None,
        check: bool = True) -> str:
    """Run quickly-returning commands and return combined stdout+stderr."""
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          env=clean_env(), timeout=timeout, stdin=subprocess.DEVNULL)
    if log is not None:
        _append(log, argv, proc.stdout)
    if check and proc.returncode != 0:
        raise CommandError(argv, proc.returncode, proc.stdout)
    return proc.stdout


def run_transfer(argv: list[str], *, log: Path | None = None) -> str:
    """Run a long transfer, streaming output; progress lines are not kept in the log."""
    kept: list[str] = []
    last_progress = ""
    deadline, stall = _deadline.get(), _stall.get()
    stopped, stalled = threading.Event(), threading.Event()
    last_output = [time.monotonic()]
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=clean_env(), stdin=subprocess.DEVNULL) as proc:
        if deadline is not None or stall is not None:
            def watchdog() -> None:
                while proc.poll() is None:
                    now = time.monotonic()
                    if deadline is not None and now >= deadline:
                        stopped.set()
                    elif stall is not None and now - last_output[0] >= stall:
                        stalled.set()
                    if stopped.is_set() or stalled.is_set():
                        proc.terminate()
                        try:
                            proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        return
                    time.sleep(0.25)
            threading.Thread(target=watchdog, name="ferry-watchdog", daemon=True).start()
        buf = b""
        assert proc.stdout is not None
        while chunk := proc.stdout.read1(65536):
            last_output[0] = time.monotonic()
            buf += chunk
            *lines, buf = re.split(rb"[\r\n]", buf)
            for raw in lines:
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                if _PROGRESS.search(line):
                    last_progress = line
                else:
                    kept.append(line)
        if buf.strip():
            kept.append(buf.decode("utf-8", "replace").rstrip())
        code = proc.wait()
    if last_progress:
        kept.append(last_progress)
    output = "\n".join(kept)
    if log is not None:
        _append(log, argv, output)
    head = f"{argv[0]} {argv[1] if len(argv) > 1 else ''}"
    if stopped.is_set():
        raise WindowClosed(f"{head} stopped: transfer window closed")
    if stalled.is_set():
        raise Stalled(f"{head} killed: no output for {stall:.0f}s (stall_timeout)")
    if code != 0:
        raise CommandError(argv, code, output)
    return output


def _append(log: Path, argv: list[str], output: str) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write("$ " + " ".join(argv) + "\n" + output.rstrip() + "\n")
