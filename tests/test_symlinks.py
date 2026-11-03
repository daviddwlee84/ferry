"""Symlinks inside units: packed as links, never followed, never fatal."""
import os

from ferry.fingerprint import fingerprint
from ferry.job import enumerate_units, job_from_dict
from ferry.receiver import receive
from ferry.sender import send

FAST = {"poll": 0.05}


def _job(tmp_path, variant):
    src = tmp_path / "src"
    unit = src / "data" / "k1"
    unit.mkdir(parents=True)
    (unit / "a.bin").write_bytes(b"x" * 1000)
    if variant == "dirlink":
        (unit / "sub").mkdir()
        (unit / "sub" / "b.bin").write_bytes(b"y" * 1000)
        os.symlink("sub", unit / "link")
    elif variant == "dangling":
        os.symlink("/nonexistent/target", unit / "dangling")
    (tmp_path / "relay").mkdir()
    return job_from_dict({
        "name": "t", "state_dir": str(tmp_path / "state"),
        "units": {"root": str(src), "glob": "data/*"},
        "sender": {"stage_dir": str(tmp_path / "stage")},
        "receiver": {"dest": str(tmp_path / "dest"), "extract": True},
        "remote": {"adapter": "local", "prefix": "/r",
                   "options": {"root": str(tmp_path / "relay")}}})


def test_symlink_to_directory_roundtrips(tmp_path):
    job = _job(tmp_path, "dirlink")
    assert send(job, **FAST).pushed == 1
    rec = receive(job, once=True, **FAST)
    assert rec.verified == 1 and not rec.failed
    link = tmp_path / "dest" / "data" / "k1" / "link"
    assert link.is_symlink() and os.readlink(link) == "sub"
    assert (link / "b.bin").read_bytes() == b"y" * 1000


def test_dangling_symlink_does_not_crash_sender(tmp_path):
    job = _job(tmp_path, "dangling")
    (unit,) = enumerate_units(job.units)
    fp = fingerprint(unit)
    assert fp.files == 2  # the link itself counts, its missing target does not
    assert send(job, **FAST).pushed == 1
    rec = receive(job, once=True, **FAST)
    assert rec.verified == 1 and not rec.failed
    dangling = tmp_path / "dest" / "data" / "k1" / "dangling"
    assert dangling.is_symlink() and not dangling.exists()


def test_fingerprint_ignores_link_target_size(tmp_path):
    job = _job(tmp_path, "dirlink")
    (unit,) = enumerate_units(job.units)
    before = fingerprint(unit).digest
    (unit.root / "data" / "k1" / "sub" / "c.bin").write_bytes(b"z")
    # the new file under sub/ changes the digest once (via sub/), not twice (via link/)
    after = fingerprint(unit)
    assert after.digest != before and after.files == 4
