"""Sender: pack units into local staging, push them to the relay, optionally delete sources.

Two threads share a job: *pack* (reads the source; obeys ``read_blocked`` windows and the
staging cap) and *push* (uploads at any time; obeys remote free-space and in-flight
limits). Every step is recorded in ``sender.jsonl`` so a restart resumes where it stopped.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .core import adapter_for, instance_lock, ledger, logger, preflight, remove_inside, require
from .fingerprint import fingerprint
from .job import Job, Unit, enumerate_units
from .pack import dir_bytes, pack_unit, staged
from .protocol import Manifest, ack_path, inventory_path, sender_done_path, unit_dir, utcnow
from .windows import blocked_for

INITIAL_BACKOFF = 30
MAX_BACKOFF = 600


@dataclass
class SendResult:
    pushed: int
    pushed_bytes: int
    source_deleted: int
    failed: list[str]


def send(job: Job, *, follow: bool = False, allow_proxy: bool = False, poll: float = 60,
         stop: threading.Event | None = None) -> SendResult:
    stop = stop or threading.Event()
    log = logger(job, "sender")
    adapter = adapter_for(job, "sender")
    with instance_lock(job, "sender"):
        require(preflight(job, adapter, allow_proxy=allow_proxy), log)
        led = ledger(job, "sender")
        led.append("start", follow=follow, allow_proxy=allow_proxy, adapter=adapter.name)
        stage = job.sender.stage_dir
        stage.mkdir(parents=True, exist_ok=True)
        part_size = job.remote.part_size or adapter.default_part_size
        if adapter.max_part_size and (part_size is None or part_size > adapter.max_part_size):
            raise ValueError(f"part_size must be <= {adapter.max_part_size} for {adapter.name}")
        ack = job.sender.delete_source == "after_receiver_ack"
        pack_done = threading.Event()
        errors: list[BaseException] = []
        failed: list[str] = []

        def pack_loop() -> None:
            first_pass = True
            try:
                while not stop.is_set():
                    inventory = _read_inventory(job, adapter, led, log)
                    for unit, fp, revision in _candidates(job, led, stage, first_pass,
                                                          inventory, log):
                        if stop.is_set():
                            return
                        _wait_pack_slot(job, stage, stop, log)
                        if stop.is_set():
                            return
                        started = time.monotonic()
                        try:
                            m = pack_unit(unit, stage, job=job.name, fmt=job.units.pack,
                                          part_size=part_size, ack_required=ack,
                                          fingerprint=fp.digest, revision=revision)
                        except (OSError, ValueError) as e:
                            log.error("pack %s failed: %s", unit.key, e)
                            led.append("failed", unit.key, stage="pack", error=str(e))
                            failed.append(unit.key)
                            continue
                        (stage / unit.key / "unit.json").write_text(json.dumps(
                            {"root": str(unit.root), "members": list(unit.members),
                             "fingerprint": fp.digest, "revision": revision}))
                        led.append("packed", unit.key, size=m.size, sha256=m.sha256,
                                   parts=len(m.parts), members=m.members, revision=revision,
                                   fingerprint=fp.digest,
                                   secs=round(time.monotonic() - started, 1))
                        log.info("packed %s r%d %d bytes in %d parts", unit.key, revision,
                                 m.size, len(m.parts))
                    first_pass = False
                    if not follow:
                        return
                    stop.wait(poll)
            except BaseException as e:  # surface to the main thread
                errors.append(e)
            finally:
                pack_done.set()

        packer = threading.Thread(target=pack_loop, name="ferry-pack", daemon=True)
        packer.start()
        backoff = INITIAL_BACKOFF
        try:
            while not stop.is_set():
                if errors:
                    raise errors[0]
                if ack:
                    _consume_acks(job, adapter, led, log)
                items = staged(stage)
                if not items:
                    if pack_done.is_set() and not follow:
                        if ack and _awaiting_acks(led):
                            stop.wait(poll)
                            continue
                        break
                    stop.wait(min(poll, 5))
                    continue
                directory = items[0]
                try:
                    if not _push_unit(job, adapter, directory, led, log, stop, poll):
                        continue  # waited on backpressure
                    backoff = INITIAL_BACKOFF
                except Exception as e:  # noqa: BLE001 - keep relaying other units
                    log.error("push %s failed: %s (retry in %ds)", directory.name, e, backoff)
                    led.append("failed", directory.name, stage="push", error=str(e)[:500])
                    stop.wait(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
            if not stop.is_set() and not follow:
                done = {"finished_at": utcnow(), "pushed": len(led.keys("pushed"))}
                adapter.put_text(json.dumps(done), sender_done_path(job.remote.prefix),
                                 job.state_dir / "scratch")
                log.info("sender finished: %s", done)
        finally:
            stop.set()
            packer.join(timeout=60)
        pushed = led.keys("pushed")
        led.append("stop")
        return SendResult(pushed=len(pushed), pushed_bytes=sum(r["size"] for r in pushed.values()),
                          source_deleted=len(led.keys("source_deleted")), failed=failed)


def _sent(led) -> dict[str, dict]:
    """Latest pushed-or-seeded record per key (both mean the receiver has that content)."""
    out: dict[str, dict] = {}
    for r in led.records():
        if r["event"] in ("pushed", "seeded") and r.get("key"):
            out[r["key"]] = r
    return out


def _candidates(job: Job, led, stage: Path, first_pass: bool, inventory: dict[str, str],
                log):
    """Units to pack now: new, or changed since last sent; seeded ones are recorded."""
    sent = _sent(led)
    units = enumerate_units(job.units)
    recheck = None
    if not first_pass and job.units.recheck_recent:
        recheck = {u.key for u in units[-job.units.recheck_recent:]}
    now = time.time()
    for unit in units:
        prev = sent.get(unit.key)
        if (stage / unit.key / "manifest.json").exists():
            continue
        if prev and (not job.units.resend_changed or job.sender.delete_source != "never"
                     or (recheck is not None and unit.key not in recheck)):
            continue
        fp = fingerprint(unit)
        if fp.files == 0:
            continue
        if prev and prev.get("fingerprint") == fp.digest:
            continue
        if job.units.settle_seconds and now - fp.newest_mtime < job.units.settle_seconds:
            log.info("%s still settling (newest file %.0fs old)", unit.key, now - fp.newest_mtime)
            continue
        if not prev and inventory.get(unit.key) == fp.digest:
            led.append("seeded", unit.key, fingerprint=fp.digest, size=fp.bytes, files=fp.files,
                       revision=1)
            continue
        if prev:
            log.info("%s changed since revision %d; re-sending", unit.key,
                     prev.get("revision", 1))
        yield unit, fp, (prev.get("revision", 1) + 1) if prev else 1


def _read_inventory(job: Job, adapter, led, log) -> dict[str, str]:
    text = adapter.get_text(inventory_path(job.remote.prefix), job.state_dir / "scratch")
    if not text:
        return {}
    data = json.loads(text)
    seen = {r.get("created_at") for r in led.records() if r["event"] == "inventory"}
    if data.get("created_at") not in seen:
        led.append("inventory", created_at=data.get("created_at"), units=len(data["keys"]))
        log.info("receiver inventory with %d units (%s)", len(data["keys"]),
                 data.get("created_at"))
    return data["keys"]


def _wait_pack_slot(job: Job, stage: Path, stop: threading.Event, log) -> None:
    announced = False
    while not stop.is_set():
        wait = blocked_for(list(job.sender.read_blocked))
        if wait <= 0 and job.sender.stage_cap and staged(stage) and \
                dir_bytes(stage) >= job.sender.stage_cap:
            wait = 30
        if wait <= 0:
            return
        if not announced:
            log.info("packing paused for %ds (read window or staging cap)", wait)
            announced = True
        stop.wait(min(wait, 300))


def _remote_inflight(job: Job, adapter, led) -> int:
    pushed = led.keys("pushed")
    keys = [e.rstrip("/") for e in adapter.list(job.remote.prefix)
            if e.endswith("/") and not e.startswith("_")]
    return sum(pushed[k]["size"] for k in keys if k in pushed)


def _push_unit(job: Job, adapter, directory: Path, led, log, stop, poll) -> bool:
    manifest = Manifest.from_json((directory / "manifest.json").read_text())
    key = manifest.key
    free = adapter.free_bytes()
    if free is not None and free - manifest.size < job.remote.min_free:
        log.info("remote free %d bytes below floor; waiting before %s", free, key)
        stop.wait(poll)
        return False
    if job.remote.max_inflight and \
            _remote_inflight(job, adapter, led) + manifest.size > job.remote.max_inflight:
        log.info("remote in-flight limit reached; waiting before %s", key)
        stop.wait(poll)
        return False
    remote = unit_dir(job.remote.prefix, key)
    started = time.monotonic()
    for part in manifest.parts:
        target = f"{remote}/{part.name}"
        if adapter.stat(target) == part.size:
            continue  # uploaded before a restart
        adapter.put(directory / part.name, target)
        got = adapter.stat(target)
        if got != part.size:
            raise RuntimeError(f"{target}: remote size {got} != local {part.size}")
    adapter.put(directory / "manifest.json", f"{remote}/manifest.json")
    if adapter.stat(f"{remote}/manifest.json") is None:
        raise RuntimeError(f"{remote}/manifest.json missing after upload")
    secs = round(time.monotonic() - started, 1)
    info = json.loads((directory / "unit.json").read_text())
    led.append("pushed", key, size=manifest.size, sha256=manifest.sha256, secs=secs,
               root=info["root"], members=info["members"],
               fingerprint=info.get("fingerprint", ""), revision=info.get("revision", 1))
    log.info("pushed %s %d bytes in %.0fs (%.1f MB/s)", key, manifest.size, secs,
             manifest.size / 1e6 / max(secs, 1e-3))
    if job.sender.delete_source == "after_remote_size":
        _delete_source(key, info, led, log)
    shutil.rmtree(directory)
    return True


def _delete_source(key: str, info: dict, led, log) -> None:
    root = Path(info["root"])
    for member in info["members"]:
        remove_inside(root, member)
    led.append("source_deleted", key)
    log.info("deleted source of %s", key)


def _awaiting_acks(led) -> bool:
    return bool(set(led.keys("pushed")) - set(led.keys("source_deleted")))


def _consume_acks(job: Job, adapter, led, log) -> None:
    pushed, deleted = led.keys("pushed"), led.keys("source_deleted")
    for entry in adapter.list(f"{job.remote.prefix}/_acks"):
        key = entry.removesuffix(".json")
        if key in pushed and key not in deleted:
            _delete_source(key, pushed[key], led, log)
        if key in pushed:
            adapter.delete(ack_path(job.remote.prefix, key))


__all__ = ["SendResult", "Unit", "send"]
