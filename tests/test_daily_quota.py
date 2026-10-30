"""Daily transfer quotas (ISP fair-use limits): shared per host, reset at local midnight."""
import datetime as dt

import pytest

import ferry.core as core
from ferry.core import adapter_for
from ferry.host import HostBudget, load_host_budget
from ferry.job import job_from_dict

CST = dt.timezone(dt.timedelta(hours=8))


def _host(tmp_path, **kw):
    return HostBudget(slot_dir=tmp_path / "slots", quota_timezone="Asia/Shanghai", **kw)


def test_usage_accumulates_per_day_and_direction(tmp_path):
    host = _host(tmp_path, upload_daily=1000)
    day1 = dt.datetime(2026, 10, 31, 10, 0, tzinfo=CST)
    host.record("upload", 600, day1)
    host.record("upload", 300, day1)
    host.record("download", 5000, day1)
    assert host.used_today("upload", day1) == 900
    assert host.quota_wait("upload", day1) == 0  # 900 < 1000: one more part may start
    host.record("upload", 200, day1)
    wait = host.quota_wait("upload", day1)
    assert wait == pytest.approx(14 * 3600)  # until 00:00 CST
    assert host.used_today("upload", day1 + dt.timedelta(days=1)) == 0
    assert host.quota_wait("download", day1) == 0  # no download quota configured


def test_quota_is_shared_between_processes_via_file(tmp_path):
    a, b = _host(tmp_path, upload_daily=10), _host(tmp_path, upload_daily=10)
    now = dt.datetime(2026, 10, 31, 1, 0, tzinfo=CST)
    a.record("upload", 7, now)
    b.record("upload", 5, now)
    assert a.used_today("upload", now) == 12 and b.quota_wait("upload", now) > 0


def test_host_toml_parses_quota(tmp_path):
    path = tmp_path / "host.toml"
    path.write_text('upload_daily = "15GB"\ndownload_daily = "250GB"\n'
                    'quota_timezone = "Asia/Shanghai"\n')
    host = load_host_budget(path)
    assert (host.upload_daily, host.download_daily) == (15 * 10**9, 250 * 10**9)
    assert host.active and host.max_transfers is None


def test_transfers_are_counted_and_wait_when_quota_is_spent(tmp_path, monkeypatch):
    (tmp_path / "relay").mkdir()
    host_file = tmp_path / "host.toml"
    host_file.write_text('upload_daily = "1KB"\n')
    monkeypatch.setenv("FERRY_HOST_CONFIG", str(host_file))
    job = job_from_dict({"name": "q", "state_dir": str(tmp_path / "state"),
                         "units": {"root": str(tmp_path), "glob": "*"},
                         "remote": {"adapter": "local", "prefix": "/p",
                                    "options": {"root": str(tmp_path / "relay")}}})
    adapter = adapter_for(job, "sender")
    src = tmp_path / "blob"
    src.write_bytes(b"x" * 1500)
    adapter.put(src, "/p/a")  # starts with room, then exceeds the quota
    assert load_host_budget().used_today("upload") == 1500
    naps = []

    def fake_sleep(seconds):
        naps.append(seconds)
        raise RuntimeError("stop waiting")

    monkeypatch.setattr(core.time, "sleep", fake_sleep)
    with pytest.raises(RuntimeError, match="stop waiting"):
        adapter.put(src, "/p/b")
    assert naps and naps[0] <= 300
