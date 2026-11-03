"""The relay is not trusted: unsafe names are refused, acks must match, retries back off."""
import json

import pytest

import ferry.receiver as receiver_mod
from ferry.protocol import Manifest, is_safe_key, is_safe_relative
from ferry.receiver import receive
from ferry.sender import send

from .conftest import tree

FAST = {"poll": 0.05}


def test_name_predicates():
    assert is_safe_key("20200102") and is_safe_key("a.b_c-d")
    assert not is_safe_key("_acks") and not is_safe_key("..") and not is_safe_key("a/b")
    assert not is_safe_key("bad key") and not is_safe_key("")
    assert is_safe_relative("order/ours/20200102") and is_safe_relative("a.tar")
    for bad in ("", "/etc/passwd", "../x", "a/../../b", "a\0b", " a"):
        assert not is_safe_relative(bad), bad


def _manifest(**over):
    data = {"key": "k", "job": "j", "format": "raw", "filename": "k.bin", "size": 1,
            "sha256": "0" * 64, "parts": [{"name": "part-0000", "size": 1, "sha256": "0" * 64}],
            "members": 1, "ack_required": False, "source_host": "h", "created_at": "t",
            "version": 1, "paths": ["k.bin"]}
    data.update(over)
    return json.dumps(data)


@pytest.mark.parametrize("over", [
    {"key": "../escape"}, {"key": "_acks"}, {"filename": "../.bashrc"},
    {"filename": "sub/x"}, {"paths": ["../../etc"]}, {"paths": ["/abs"]},
    {"parts": [{"name": "../p", "size": 1, "sha256": ""}]},
    {"parts": [{"name": "part-0000", "size": 0, "sha256": ""}]}, {"parts": []},
])
def test_manifest_rejects_unsafe_names(over):
    with pytest.raises(ValueError):
        Manifest.from_json(_manifest(**over))


def test_manifest_accepts_safe_names():
    assert Manifest.from_json(_manifest()).key == "k"


def test_receiver_ignores_unsafe_relay_directory(make_job):
    job, src, base = make_job(days=("20200102",))
    send(job, **FAST)
    bad = base / "relay" / "ferry" / "test" / "bad key"
    bad.mkdir()
    (bad / "manifest.json").write_text(_manifest(key="bad key"))
    rec = receive(job, once=True, **FAST)
    assert rec.verified == 1 and not rec.failed
    assert bad.exists()  # left alone, never touched


def test_ack_with_other_sha256_does_not_delete_source(make_job):
    job, src, base = make_job(days=("20200102",), delete_source="after_receiver_ack")
    before = tree(src)
    acks = base / "relay" / "ferry" / "test" / "_acks"
    acks.mkdir(parents=True)
    (acks / "20200102.json").write_text(json.dumps({"key": "20200102", "sha256": "f" * 64}))
    # one push loop pass is enough: acks are consumed before each push; the sender then
    # waits for a real ack, so run it on a thread and stop it.
    import threading
    stop = threading.Event()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", send(job, stop=stop, **FAST)))
    t.start()
    t.join(timeout=1)
    stop.set()
    t.join(timeout=30)
    assert out["r"].pushed == 1 and out["r"].source_deleted == 0
    assert tree(src) == before
    assert (acks / "20200102.json").exists()  # kept for the operator to inspect


def test_failed_unit_backs_off_and_gives_up(make_job, monkeypatch):
    job, src, base = make_job(days=("20200102",))
    send(job, **FAST)
    part = base / "relay" / "ferry" / "test" / "20200102" / "part-0001"
    data = bytearray(part.read_bytes())
    data[100] ^= 0xFF
    part.write_bytes(bytes(data))
    monkeypatch.setattr(receiver_mod, "RETRY_BASE", 0.0)
    monkeypatch.setattr(receiver_mod, "MAX_ATTEMPTS", 3)
    calls = {"n": 0}
    real = receiver_mod._receive_unit

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(receiver_mod, "_receive_unit", counting)
    # not --once and the sender is done, but the relay is not empty: the receiver keeps
    # polling; after MAX_ATTEMPTS it stops touching the unit, so stop it from outside.
    import threading
    stop = threading.Event()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", receive(job, stop=stop, **FAST)))
    t.start()
    t.join(timeout=1)
    stop.set()
    t.join(timeout=30)
    assert calls["n"] == 3
    assert out["r"].failed == ["20200102"]
