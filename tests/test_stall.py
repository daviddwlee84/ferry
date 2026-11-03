"""Stall watchdog and window closes: a hung backend is killed, a closed window is a pause."""
import time

import pytest

import ferry.core as core
from ferry.adapters.local import LocalAdapter
from ferry.adapters.rclone import RcloneAdapter
from ferry.adapters.rsync import RsyncAdapter
from ferry.job import JobError, job_from_dict
from ferry.proc import Stalled, WindowClosed, run_transfer, transfer_stall
from ferry.protocol import Ledger
from ferry.sender import send

FAST = {"poll": 0.05}


def test_silent_transfer_is_killed():
    started = time.monotonic()
    with pytest.raises(Stalled), transfer_stall(0.3):
        run_transfer(["sleep", "5"])
    assert time.monotonic() - started < 3
    with transfer_stall(0.5):  # output keeps it alive
        assert "x" in run_transfer(["sh", "-c", "for i in 1 2 3; do echo x; sleep 0.2; done"])
    assert run_transfer(["true"]) == ""  # no watchdog outside the block


def test_stall_timeout_is_parsed_and_applied(tmp_path):
    (tmp_path / "relay").mkdir()
    base = {"name": "s", "state_dir": str(tmp_path / "state"),
            "units": {"root": str(tmp_path), "glob": "*"}}
    remote = {"adapter": "local", "prefix": "/p", "options": {"root": str(tmp_path / "relay")}}
    job = job_from_dict({**base, "remote": {**remote, "stall_timeout": "20m"}})
    assert job.remote.stall_timeout == 1200
    assert job_from_dict({**base, "remote": remote}).remote.stall_timeout is None
    with pytest.raises(JobError):
        job_from_dict({**base, "remote": {**remote, "stall_timeout": 0}})
    adapter = core.adapter_for(job, "sender")
    assert adapter.stall_timeout == 1200
    src = tmp_path / "f"
    src.write_bytes(b"data")
    adapter.put(src, "/p/f")  # the wrapper still transfers
    assert (tmp_path / "relay" / "p" / "f").read_bytes() == b"data"


def test_quiet_tools_get_progress_flags(monkeypatch, tmp_path):
    calls = []

    def record(argv, log=None):
        calls.append(argv)

    monkeypatch.setattr("ferry.adapters.rclone.run_transfer", record)
    monkeypatch.setattr("ferry.adapters.rsync.run_transfer", record)
    rc = RcloneAdapter({"remote": "gdrive:", "endpoints": ["www.googleapis.com"]})
    rc.put(tmp_path / "f", "/a/f")
    assert "--stats" not in calls[-1]
    rc.stall_timeout = 600
    rc.put(tmp_path / "f", "/a/f")
    assert calls[-1][calls[-1].index("--stats") + 1] == "60s"
    rs = RsyncAdapter({"host": "", "root": str(tmp_path)})
    monkeypatch.setattr(rs, "_sh", lambda command, timeout=120: "")
    rs.stall_timeout = 600
    rs.put(tmp_path / "f", "/a/f")
    assert "--info=progress2" in calls[-1]


def test_window_close_is_a_pause_not_a_failure(tmp_path, monkeypatch):
    class Closing(LocalAdapter):
        closes = 1

        def put(self, local, remote):
            if Closing.closes and local.name.startswith("part-"):
                Closing.closes -= 1
                raise WindowClosed("window closed")
            super().put(local, remote)

    monkeypatch.setattr("ferry.core.make_adapter",
                        lambda name, options, log=None: Closing(options, log))
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "relay").mkdir()
    job = job_from_dict({"name": "w", "state_dir": str(tmp_path / "state"),
                         "units": {"root": str(src), "glob": "*", "pack": "none"},
                         "sender": {"stage_dir": str(tmp_path / "stage")},
                         "remote": {"adapter": "local", "prefix": "/p",
                                    "options": {"root": str(tmp_path / "relay")}}})
    started = time.monotonic()
    res = send(job, **FAST)
    assert res.pushed == 1 and not res.failed
    assert time.monotonic() - started < 5  # no 30 s failure backoff
    events = [r["event"] for r in Ledger(job.state_dir / "sender.jsonl").records()]
    assert "paused" in events and "failed" not in events
