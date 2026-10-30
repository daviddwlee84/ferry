"""Transfer windows: uploads/downloads only start inside allowed hours."""
import datetime as dt

import pytest

import ferry.core as core
from ferry.adapters.local import LocalAdapter
from ferry.core import PreflightError, adapter_for, wait_for_window
from ferry.host import load_host_budget
from ferry.job import job_from_dict
from ferry.windows import Schedule, Window, wait_seconds

CST = dt.timezone(dt.timedelta(hours=8))
NIGHT = Schedule(allowed=(Window.parse("* 04:00-07:00 Asia/Shanghai"),))
MARKET = Schedule(blocked=(Window.parse("Mon-Fri 09:15-15:30 Asia/Shanghai"),))


@pytest.mark.parametrize("when,wait_min", [
    (dt.datetime(2026, 10, 31, 3, 30, tzinfo=CST), 30),    # before the night window
    (dt.datetime(2026, 10, 31, 4, 0, tzinfo=CST), 0),      # inside
    (dt.datetime(2026, 10, 31, 6, 59, tzinfo=CST), 0),
    (dt.datetime(2026, 10, 31, 7, 0, tzinfo=CST), 21 * 60),  # closed until tomorrow 04:00
])
def test_allowed_window(when, wait_min):
    assert wait_seconds([NIGHT], when) == pytest.approx(wait_min * 60)


def test_blocked_window_and_combination():
    friday_noon = dt.datetime(2026, 10, 30, 12, 0, tzinfo=CST)
    assert wait_seconds([MARKET], friday_noon) == pytest.approx(210 * 60)  # until 15:30
    assert wait_seconds([MARKET], dt.datetime(2026, 10, 31, 12, 0, tzinfo=CST)) == 0  # Saturday
    # both must hold: allowed 09:00-17:00 but market hours blocked -> opens 15:30
    day = Schedule(allowed=(Window.parse("* 09:00-17:00 Asia/Shanghai"),))
    assert wait_seconds([day, MARKET], friday_noon) == pytest.approx(210 * 60)
    assert wait_seconds([Schedule(), Schedule()], friday_noon) == 0


def test_never_open_is_reported():
    never = Schedule(allowed=(Window.parse("Sat 01:00-02:00 UTC"),),
                     blocked=(Window.parse("Sat 00:00-23:59 UTC"),))
    assert wait_seconds([never], dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)) is None
    with pytest.raises(PreflightError):
        wait_for_window("upload", [never], sleep=lambda s: None)


def test_job_and_host_schedules_parse(tmp_path, monkeypatch):
    host_file = tmp_path / "host.toml"
    host_file.write_text('upload_allowed = ["* 04:00-07:00 Asia/Shanghai"]\n')
    monkeypatch.setenv("FERRY_HOST_CONFIG", str(host_file))
    host = load_host_budget()
    assert host.schedule("upload") and not host.schedule("download") and host.active
    job = job_from_dict({"name": "w", "units": {"root": str(tmp_path), "glob": "*"},
                         "remote": {"adapter": "local", "prefix": "/p",
                                    "options": {"root": str(tmp_path)},
                                    "download_blocked": ["Mon-Fri 09:15-15:30 Asia/Shanghai"]}})
    assert job.remote.download_schedule and not job.remote.upload_schedule


def test_put_waits_for_window_then_transfers(tmp_path, monkeypatch):
    (tmp_path / "relay").mkdir()
    job = job_from_dict({"name": "w", "state_dir": str(tmp_path / "state"),
                         "units": {"root": str(tmp_path), "glob": "*"},
                         "remote": {"adapter": "local", "prefix": "/p",
                                    "options": {"root": str(tmp_path / "relay")},
                                    "upload_allowed": ["* 04:00-07:00 Asia/Shanghai"]}})
    waits = iter([1800.0, 0.0])  # closed for 30 min, then open
    naps = []
    monkeypatch.setattr(core, "wait_seconds", lambda schedules: next(waits))
    monkeypatch.setattr(core.time, "sleep", lambda s: naps.append(s))
    adapter = adapter_for(job, "sender")
    src = tmp_path / "f"
    src.write_bytes(b"data")
    adapter.put(src, "/p/f")
    assert naps == [300] and (tmp_path / "relay" / "p" / "f").read_bytes() == b"data"
    assert isinstance(adapter, LocalAdapter)


def test_deadline_stops_a_running_transfer():
    import time as _time

    from ferry.proc import WindowClosed, run_transfer, transfer_deadline
    started = _time.monotonic()
    with pytest.raises(WindowClosed), transfer_deadline(0.3):
        run_transfer(["sleep", "5"])
    assert _time.monotonic() - started < 3
    assert run_transfer(["true"]) == ""  # no deadline outside the block


def test_seconds_until_close():
    from ferry.windows import seconds_until_close
    inside = dt.datetime(2026, 10, 31, 6, 30, tzinfo=CST)
    assert seconds_until_close([NIGHT], inside) == pytest.approx(30 * 60)
    assert seconds_until_close([Schedule()], inside) is None  # always open


def test_window_end_cuts_backend_and_can_be_disabled(tmp_path, monkeypatch):
    from ferry.proc import WindowClosed, run_transfer

    class SlowAdapter(LocalAdapter):
        def put(self, local, remote):
            run_transfer(["sleep", "5"])

    monkeypatch.setattr("ferry.core.make_adapter",
                        lambda name, options, log=None: SlowAdapter(options, log))
    monkeypatch.setattr(core, "wait_seconds", lambda schedules: 0.0)
    monkeypatch.setattr(core, "seconds_until_close", lambda schedules: 0.3)
    base = {"name": "w", "state_dir": str(tmp_path / "state"),
            "units": {"root": str(tmp_path), "glob": "*"}}
    remote = {"adapter": "local", "prefix": "/p", "options": {"root": str(tmp_path)},
              "upload_allowed": ["* 04:00-07:00 Asia/Shanghai"]}
    adapter = adapter_for(job_from_dict({**base, "remote": remote}), "sender")
    with pytest.raises(WindowClosed):
        adapter.put(tmp_path / "x", "/p/x")
    calls = []
    monkeypatch.setattr(core, "seconds_until_close", lambda schedules: calls.append(1) or 0.3)
    monkeypatch.setattr(SlowAdapter, "put", lambda self, local, remote: None)
    relaxed = adapter_for(job_from_dict({**base, "remote": {**remote,
                                                            "stop_at_window_end": False}}),
                          "sender")
    relaxed.put(tmp_path / "x", "/p/x")
    assert calls == []  # no deadline computed when stopping is disabled
