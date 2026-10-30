"""Bandwidth caps: parsed per side, enforced per adapter, refused where unsupported."""
import time

import pytest

from ferry.adapters.baidupcs import BaiduPCSAdapter
from ferry.adapters.ghrelease import GhReleaseAdapter
from ferry.adapters.local import LocalAdapter
from ferry.adapters.rclone import RcloneAdapter
from ferry.adapters.rsync import RsyncAdapter
from ferry.core import adapter_for
from ferry.job import job_from_dict


def _job(tmp_path, **remote):
    return job_from_dict({
        "name": "t", "state_dir": str(tmp_path / "state"),
        "units": {"root": str(tmp_path), "glob": "*"},
        "remote": {"adapter": "local", "prefix": "/p", "options": {"root": str(tmp_path)},
                   **remote}})


def test_rates_parse_and_side_overrides(tmp_path):
    job = _job(tmp_path, upload_rate="2MB", download_rate="30MB",
               sides={"receiver": {"download_rate": "5MB"}})
    assert job.remote.rates_for_side("sender") == (2_000_000, 30_000_000)
    assert job.remote.rates_for_side("receiver") == (2_000_000, 5_000_000)
    adapter = adapter_for(job, "sender")
    assert (adapter.upload_rate, adapter.download_rate) == (2_000_000, 30_000_000)
    assert _job(tmp_path).remote.rates_for_side("sender") == (None, None)


def test_local_copy_is_throttled(tmp_path):
    src = tmp_path / "blob"
    src.write_bytes(b"x" * 400_000)
    adapter = LocalAdapter({"root": str(tmp_path / "relay")})
    (tmp_path / "relay").mkdir()
    adapter.upload_rate = 1_000_000  # 400 kB at 1 MB/s ~ 0.4 s
    started = time.monotonic()
    adapter.put(src, "/x/blob")
    assert time.monotonic() - started >= 0.3
    assert (tmp_path / "relay" / "x" / "blob").read_bytes() == src.read_bytes()


def test_rclone_and_rsync_pass_native_limits(monkeypatch, tmp_path):
    calls = []
    def record(argv, log=None):
        calls.append(argv)

    monkeypatch.setattr("ferry.adapters.rclone.run_transfer", record)
    monkeypatch.setattr("ferry.adapters.rsync.run_transfer", record)
    rc = RcloneAdapter({"remote": "gdrive:", "endpoints": ["www.googleapis.com"]})
    rc.upload_rate = 2_000_000
    rc.put(tmp_path / "f", "/a/f")
    assert calls[-1][calls[-1].index("--bwlimit") + 1] == "2000000B:off"
    rs = RsyncAdapter({"host": "", "root": str(tmp_path)})
    monkeypatch.setattr(rs, "_sh", lambda command, timeout=120: "")
    rs.download_rate = 3 * 1024 * 1024
    rs.get("/a/f", tmp_path / "g")
    assert "--bwlimit=3072" in calls[-1]
    rs.upload_rate = None
    rs.put(tmp_path / "f", "/a/f")
    assert not any(a.startswith("--bwlimit") for a in calls[-1])


def test_baidupcs_sets_host_wide_config_caps(monkeypatch, tmp_path):
    adapter = BaiduPCSAdapter({"binary": "/bin/true"})
    ran, transfers = [], []
    monkeypatch.setattr(adapter, "_run", lambda *a: ran.append(a) or "")
    monkeypatch.setattr("ferry.adapters.baidupcs.run_transfer",
                        lambda argv, log=None: transfers.append(argv))
    local = tmp_path / "part-0000"
    local.write_bytes(b"1")
    adapter.upload_rate = 2 * 1024 * 1024
    adapter.put(local, "/relay/k/part-0000")
    assert ("config", "set", "-max_upload_rate", "2048KB") in ran
    ran.clear()
    adapter.upload_rate = None
    adapter.put(local, "/relay/k/part-0000")
    assert ran == []  # no cap requested: leave the user's config alone


def test_backend_without_rate_support_fails_closed():
    gh = GhReleaseAdapter({"repo": "o/r"})
    assert gh.rate_problems() == []
    gh.upload_rate = 1_000_000
    assert gh.rate_problems() and "cannot limit bandwidth" in gh.rate_problems()[0]


@pytest.mark.parametrize("value,expected", [
    ("2MB", 2_000_000), ("2.5MB", 2_500_000), ("20Mbit", 2_500_000), ("20Mbps", 2_500_000),
    ("300Mbps", 37_500_000), ("800kbit", 100_000),
])
def test_rates_accept_bytes_or_bits(tmp_path, value, expected):
    assert _job(tmp_path, upload_rate=value).remote.upload_rate == expected


def test_capital_b_per_second_is_not_bits(tmp_path):
    with pytest.raises(ValueError):
        _job(tmp_path, upload_rate="20MBps")  # ambiguous: write 20MB or 160Mbps
