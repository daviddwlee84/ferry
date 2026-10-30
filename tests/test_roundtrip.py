import json
import tarfile
import threading

import pytest

import ferry.sender as sender_mod
from ferry.adapters.local import LocalAdapter
from ferry.core import AlreadyRunning, PreflightError, instance_lock, preflight, require
from ferry.egress import RouteFinding
from ferry.job import enumerate_units, job_from_dict
from ferry.protocol import Ledger
from ferry.receiver import receive
from ferry.sender import send

from .conftest import tree

FAST = {"poll": 0.05}


def _unpack(path, into):
    with tarfile.open(path) as tar:
        tar.extractall(into, filter="data")
    return tree(into)


def _expected(src, key):
    return {k: v for k, v in tree(src).items() if key in k}


def test_roundtrip_multipart_keeps_source(make_job, tmp_path):
    job, src, base = make_job()
    before = tree(src)
    result = send(job, **FAST)
    assert result.pushed == 3 and not result.failed
    rec = receive(job, **FAST)
    assert rec.verified == 3 and not rec.failed
    for key in ("20200102", "20200103", "20200106"):
        archive = base / "dest" / "2020" / f"{key}.tar"
        assert _unpack(archive, tmp_path / "x" / key) == _expected(src, key)
    assert tree(src) == before  # delete_source = never
    relay = tree(base / "relay")
    assert list(relay) == ["ferry/test/_sender_done.json"]
    pushed = Ledger(job.state_dir / "sender.jsonl").keys("pushed")
    assert all(r["size"] > 64 * 1024 for r in pushed.values())  # really multi-part


def test_after_remote_size_deletes_source_members_only(make_job):
    job, src, base = make_job(delete_source="after_remote_size")
    (src / "unrelated.txt").write_text("keep me")
    send(job, **FAST)
    assert not list((src / "order" / "ours").iterdir())
    assert not list((src / "_manifests").iterdir())
    assert (src / "unrelated.txt").exists()
    assert receive(job, **FAST).verified == 3


def test_after_receiver_ack_waits_for_receiver(make_job):
    job, src, base = make_job(delete_source="after_receiver_ack")
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("rec", receive(job, **FAST)))
    t.start()
    res = send(job, **FAST)
    t.join(timeout=30)
    assert res.source_deleted == 3 and out["rec"].verified == 3
    assert not list((src / "_manifests").iterdir())
    assert tree(base / "relay") == {"ferry/test/_sender_done.json": tree(base / "relay")[
        "ferry/test/_sender_done.json"]}


def test_transient_upload_failure_resumes_without_reuploading(make_job, monkeypatch):
    job, src, base = make_job(days=("20200102",))
    monkeypatch.setattr(sender_mod, "INITIAL_BACKOFF", 0.01)
    calls = {"n": 0, "names": []}
    real_put = LocalAdapter.put

    def flaky(self, local, remote):
        calls["n"] += 1
        calls["names"].append(remote.rsplit("/", 1)[-1])
        if calls["n"] == 3:
            raise OSError("connection reset")
        return real_put(self, local, remote)

    monkeypatch.setattr(LocalAdapter, "put", flaky)
    assert send(job, **FAST).pushed == 1
    names = calls["names"]
    assert names.count("part-0000") == 1 and names.count("part-0002") == 2
    assert receive(job, **FAST).verified == 1


def test_corrupted_relay_part_is_rejected_and_kept(make_job):
    job, src, base = make_job(days=("20200102",))
    send(job, **FAST)
    part = base / "relay" / "ferry" / "test" / "20200102" / "part-0001"
    data = bytearray(part.read_bytes())
    data[100] ^= 0xFF
    part.write_bytes(bytes(data))
    rec = receive(job, once=True, **FAST)
    assert rec.verified == 0 and rec.failed == ["20200102"]
    assert part.exists() and not (base / "dest" / "2020" / "20200102.tar").exists()


def test_second_instance_is_refused(make_job):
    job, *_ = make_job()
    with instance_lock(job, "sender"):
        with pytest.raises(AlreadyRunning):
            send(job, **FAST)


def test_direct_egress_policy_fails_closed(make_job):
    job, *_ = make_job()
    adapter = LocalAdapter(job.remote.options)
    adapter.endpoints = ("relay.example",)

    def routes(hosts):
        return [RouteFinding(h, "198.18.0.9", None, "fake-ip") for h in hosts]

    import logging
    log = logging.getLogger("t")
    with pytest.raises(PreflightError):
        require(preflight(job, adapter, routes=routes), log)
    require(preflight(job, adapter, allow_proxy=True, routes=routes), log)  # accepted


def test_raw_files_mode(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"x" * 150_000)
    (src / "b c.bin").write_bytes(b"y" * 10)
    (tmp_path / "relay").mkdir()
    job = job_from_dict({
        "name": "raw", "state_dir": str(tmp_path / "state"),
        "units": {"root": str(src), "glob": "*.bin", "pack": "none"},
        "receiver": {"dest": str(tmp_path / "dest")},
        "remote": {"adapter": "local", "prefix": "/r", "part_size": "64KiB",
                   "options": {"root": str(tmp_path / "relay"), "drop_empty": True}}})
    assert [u.key for u in enumerate_units(job.units)] == ["a.bin", "b_c.bin"]
    send(job, **FAST)
    receive(job, **FAST)
    assert (tmp_path / "dest" / "a.bin").read_bytes() == b"x" * 150_000
    assert (tmp_path / "dest" / "b c.bin").read_bytes() == b"y" * 10


def test_extract_on_receive(make_job):
    job, src, base = make_job(days=("20200102",), receiver={"dest": "PLACEHOLDER"})
    job = job.__class__(**{**job.__dict__, "receiver": job.receiver.__class__(
        dest=str(base / "out"), extract=True)})
    send(job, **FAST)
    receive(job, **FAST)
    assert tree(base / "out") == _expected(src, "20200102")


def test_manifest_records_members(make_job):
    job, src, base = make_job(days=("20200102",))
    send(job, **FAST)
    manifest = json.loads(
        (base / "relay" / "ferry" / "test" / "20200102" / "manifest.json").read_text())
    assert manifest["members"] == 7 and manifest["format"] == "tar"
    assert sum(p["size"] for p in manifest["parts"]) == manifest["size"]


def test_direct_rsync_with_local_receiver_override(make_job, tmp_path):
    """Sender pushes with rsync (host '' = local); receiver reads the same dir as 'local'."""
    import shutil as _shutil
    if not _shutil.which("rsync"):
        pytest.skip("rsync not installed")
    job, src, base = make_job(days=("20200102", "20200103"))
    landing = base / "landing"
    landing.mkdir()
    remote = job.remote.__class__(
        adapter="rsync", prefix="/inbox", part_size=job.remote.part_size,
        options={"host": "", "root": str(landing)},
        sides={"receiver": {"adapter": "local", "options": {"root": str(landing)}}})
    job = job.__class__(**{**job.__dict__, "remote": remote})
    assert send(job, **FAST).pushed == 2
    assert receive(job, **FAST).verified == 2
    for key in ("20200102", "20200103"):
        out = _unpack(base / "dest" / "2020" / f"{key}.tar", tmp_path / "y" / key)
        assert out == _expected(src, key)
    assert [p.name for p in (landing / "inbox").iterdir()] == ["_sender_done.json"]
