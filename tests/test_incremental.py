"""Backfill and incremental pulls are the same job: new, changed and seeded units."""
import dataclasses
import os
import time

from ferry.fingerprint import fingerprint
from ferry.inventory import build_inventory, publish_inventory
from ferry.job import ReceiverSpec, UnitsSpec, enumerate_units, job_from_dict
from ferry.protocol import Ledger
from ferry.receiver import receive
from ferry.sender import send

from .conftest import tree

FAST = {"poll": 0.05}


def _mirror_job(make_job):
    """Receiver extracts into one tree laid out exactly like the source."""
    job, src, base = make_job()
    mirror = base / "mirror"
    job = dataclasses.replace(job, receiver=ReceiverSpec(dest=str(mirror), extract=True))
    return job, src, base, mirror


def test_backfill_then_increment_then_revision(make_job):
    job, src, base, mirror = _mirror_job(make_job)
    send(job, **FAST)
    receive(job, **FAST)
    assert tree(mirror) == tree(src)

    # New day appears, and a historical day is repaired: one file replaced, one removed.
    new = src / "order" / "ours" / "20200107"
    new.mkdir(parents=True)
    (new / "000001.SZ.feather").write_bytes(b"new" * 1000)
    (src / "_manifests" / "20200107.json").write_text("{}")
    day = src / "trans" / "ours" / "20200103"
    (day / "000001.SZ.feather").write_bytes(b"repaired" * 5000)
    (day / "000002.SZ.feather").unlink()

    send(job, **FAST)
    receive(job, **FAST)
    assert tree(mirror) == tree(src)  # stale file removed by the replacing extract
    pushed = Ledger(job.state_dir / "sender.jsonl").keys("pushed")
    assert pushed["20200103"]["revision"] == 2
    assert pushed["20200107"]["revision"] == 1
    assert pushed["20200102"]["revision"] == 1  # unchanged, not re-sent


def test_seeding_skips_units_the_receiver_already_has(make_job):
    job, src, base, mirror = _mirror_job(make_job)
    # The receiver already holds two of three days (e.g. an older manual copy).
    for rel in ("order/ours/20200102", "trans/ours/20200102", "_manifests/20200102.json",
                "order/ours/20200103", "trans/ours/20200103"):
        target = mirror / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.system(f"cp -R '{src / rel}' '{target}'")
    (mirror / "_manifests" / "20200103.json").write_text("stale")  # differs -> not seeded
    publish_inventory(job, build_inventory(job))
    send(job, **FAST)
    led = Ledger(job.state_dir / "sender.jsonl")
    assert set(led.keys("seeded")) == {"20200102"}
    assert set(led.keys("pushed")) == {"20200103", "20200106"}
    receive(job, **FAST)
    assert tree(mirror) == tree(src)


def test_settle_holds_back_fresh_units(make_job):
    job, src, base = make_job(days=("20200102",))
    job = dataclasses.replace(job, units=dataclasses.replace(job.units, settle_seconds=3600))
    assert send(job, **FAST).pushed == 0
    old = time.time() - 7200
    for p in src.rglob("*"):
        os.utime(p, (old, old))
    assert send(job, **FAST).pushed == 1


def test_glob_keys_never_collide_with_protocol_names(tmp_path):
    root = tmp_path / "src"
    for d in ("_index", "_audit"):
        (root / d).mkdir(parents=True)
        (root / d / "x.parquet").write_bytes(b"1")
    units = enumerate_units(UnitsSpec(root=root, glob="_*"))
    assert [u.key for u in units] == ["audit", "index"]
    assert fingerprint(units[0]).files == 1


def test_trusted_interface_is_accepted(make_job):
    from ferry.egress import check_routes

    def resolve(host, port, type=None):  # noqa: A002
        import socket
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("110.242.70.78", port))]

    found = check_routes(["d.pcs.baidu.com"], resolve=resolve, route_iface=lambda a: "Meta")
    assert found[0].problem
    found = check_routes(["d.pcs.baidu.com"], resolve=resolve, route_iface=lambda a: "Meta",
                         trusted=("Meta",))
    assert found[0].problem is None
    job = job_from_dict({"name": "t", "units": {"root": "/x", "glob": "*"},
                         "remote": {"adapter": "local", "prefix": "/p",
                                    "trusted_interfaces": ["Meta"], "options": {"root": "/"}}})
    assert job.remote.trusted_interfaces == ("Meta",)
