"""Receiver: pull READY units from the relay, verify, place, acknowledge, clean the relay.

A unit is READY when its ``manifest.json`` exists remotely. Each part is checked against
the manifest's size and sha256 before reassembly; the reassembled payload is checked
against the whole-stream sha256 and, for tars, the member count. Only then is the
relay copy deleted. Already-verified parts are kept across restarts.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .core import adapter_for, instance_lock, ledger, logger, preflight, require
from .job import Job, dest_dir
from .protocol import Manifest, ack_path, sender_done_path, unit_dir, utcnow

READ_BUF = 8 << 20


@dataclass
class ReceiveResult:
    verified: int
    verified_bytes: int
    failed: list[str]


class VerifyError(RuntimeError):
    pass


def receive(job: Job, *, once: bool = False, follow: bool = False, allow_proxy: bool = False,
            poll: float = 60, stop: threading.Event | None = None) -> ReceiveResult:
    if job.receiver is None:
        raise ValueError("job has no [receiver] section")
    stop = stop or threading.Event()
    log = logger(job, "receiver")
    adapter = adapter_for(job, "receiver")
    scratch = job.state_dir / "scratch"
    failed: list[str] = []
    with instance_lock(job, "receiver"):
        require(preflight(job, adapter, allow_proxy=allow_proxy), log)
        led = ledger(job, "receiver")
        led.append("start", once=once, follow=follow, allow_proxy=allow_proxy)
        attempts: dict[str, int] = {}
        while not stop.is_set():
            entries = adapter.list(job.remote.prefix)
            keys = sorted(e.rstrip("/") for e in entries
                          if e.endswith("/") and not e.startswith("_"))
            verified = led.keys("verified")
            progressed = False
            for key in keys:
                if stop.is_set():
                    break
                text = adapter.get_text(f"{unit_dir(job.remote.prefix, key)}/manifest.json",
                                        scratch)
                if text is None:
                    continue  # still uploading
                try:
                    manifest = Manifest.from_json(text)
                    if verified.get(key, {}).get("sha256") == manifest.sha256:
                        # verified earlier; only the relay cleanup did not finish
                        adapter.delete(unit_dir(job.remote.prefix, key))
                        continue
                    if not _has_room(job, manifest, log):
                        break
                    _receive_unit(job, adapter, manifest, led, log)
                    progressed = True
                except Exception as e:  # noqa: BLE001 - keep receiving other units
                    attempts[key] = attempts.get(key, 0) + 1
                    log.error("receive %s failed (attempt %d): %s", key, attempts[key], e)
                    led.append("failed", key, error=str(e)[:500], attempt=attempts[key])
                    if key not in failed:
                        failed.append(key)
            if once:
                break
            if not follow and not progressed and \
                    adapter.stat(sender_done_path(job.remote.prefix)) is not None and \
                    not any(e.endswith("/") and not e.startswith("_")
                            for e in adapter.list(job.remote.prefix)):
                log.info("sender finished and relay is empty; receiver done")
                break
            if not progressed:
                stop.wait(poll)
        done = led.keys("verified")
        led.append("stop")
        return ReceiveResult(verified=len(done), failed=[k for k in failed if k not in done],
                             verified_bytes=sum(r["size"] for r in done.values()))


def _has_room(job: Job, m: Manifest, log) -> bool:
    assert job.receiver is not None
    dest = dest_dir(job.receiver, m.key)
    probe = dest
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    need = job.receiver.min_free + 2 * m.size
    if free < need:
        log.warning("destination free %d bytes < %d needed for %s; waiting", free, need, m.key)
        return False
    return True


def _extract_replacing(archive: Path, dest: Path, m: Manifest) -> None:
    """Extract so that each unit path mirrors the sender exactly (stale files removed).

    Paths are swapped one by one: extract into a scratch dir on the same filesystem, move
    the current copy aside, move the new one in, then drop the old copy.
    """
    scratch = dest / f".ferry-extract-{m.key}"
    old = dest / f".ferry-old-{m.key}"
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    scratch.mkdir(parents=True)
    with tarfile.open(archive, "r:") as tar:
        tar.extractall(scratch, filter="data")
    paths = m.paths or tuple(p.name for p in scratch.iterdir())
    for rel in paths:
        new, target = scratch / rel, dest / rel
        if not new.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            (old / rel).parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, old / rel)
        os.replace(new, target)
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(READ_BUF):
            h.update(chunk)
    return h.hexdigest()


def _receive_unit(job: Job, adapter, m: Manifest, led, log) -> None:
    assert job.receiver is not None
    dest = dest_dir(job.receiver, m.key)
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / m.filename
    remote = unit_dir(job.remote.prefix, m.key)
    started = time.monotonic()
    if not (final.is_file() and final.stat().st_size == m.size and _sha256(final) == m.sha256):
        partial = dest / f".ferry-{m.key}.partial"
        partial.mkdir(exist_ok=True)
        for part in m.parts:
            local = partial / part.name
            if local.is_file() and local.stat().st_size == part.size and \
                    _sha256(local) == part.sha256:
                continue
            adapter.get(f"{remote}/{part.name}", local)
            if local.stat().st_size != part.size or _sha256(local) != part.sha256:
                local.unlink(missing_ok=True)
                raise VerifyError(f"{m.key}/{part.name}: size or sha256 mismatch")
        tmp = dest / f".{m.filename}.ferry-tmp"
        if len(m.parts) == 1:
            os.replace(partial / m.parts[0].name, tmp)
        else:
            h = hashlib.sha256()
            with tmp.open("wb") as out:
                for part in m.parts:
                    with (partial / part.name).open("rb") as f:
                        while chunk := f.read(READ_BUF):
                            out.write(chunk)
                            h.update(chunk)
            if h.hexdigest() != m.sha256:
                tmp.unlink()
                raise VerifyError(f"{m.key}: reassembled sha256 mismatch")
        if len(m.parts) == 1 and m.parts[0].sha256 != m.sha256:
            tmp.unlink()
            raise VerifyError(f"{m.key}: whole-payload sha256 mismatch")
        os.replace(tmp, final)
        shutil.rmtree(partial, ignore_errors=True)
    transfer_secs = round(time.monotonic() - started, 1)
    if m.format == "tar":
        with tarfile.open(final, "r:") as tar:
            members = sum(1 for t in tar if not t.isdir())
        if members != m.members:
            raise VerifyError(f"{m.key}: tar has {members} files, manifest says {m.members}")
        if job.receiver.extract:
            _extract_replacing(final, dest, m)
            final.unlink()
    led.append("verified", m.key, size=m.size, sha256=m.sha256, members=m.members,
               secs=transfer_secs, path=str(final), revision=m.revision,
               fingerprint=m.fingerprint)
    log.info("verified %s %d bytes in %.0fs (%.1f MB/s)", m.key, m.size, transfer_secs,
             m.size / 1e6 / max(transfer_secs, 1e-3))
    if m.ack_required:
        adapter.put_text(json.dumps({"key": m.key, "sha256": m.sha256, "at": utcnow()}),
                         ack_path(job.remote.prefix, m.key), job.state_dir / "scratch")
    adapter.delete(remote)
    led.append("remote_deleted", m.key)
