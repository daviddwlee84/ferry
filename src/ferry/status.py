"""Progress, throughput and ETA from the local ledgers (no source scan unless asked)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .core import adapter_for, ledger
from .job import Job, enumerate_units
from .pack import dir_bytes, staged

RATE_WINDOW = 20


@dataclass
class SideStatus:
    side: str
    done: int
    done_bytes: int
    recent_mb_s: float | None
    failures_last_24h: int
    last_event: dict | None
    extra: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _rate(records: list[dict]) -> float | None:
    window = [r for r in records if r.get("secs")][-RATE_WINDOW:]
    secs = sum(r["secs"] for r in window)
    return round(sum(r["size"] for r in window) / 1e6 / secs, 2) if secs > 0 else None


def _recent_failures(records: list[dict]) -> int:
    import datetime as dt
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    return sum(1 for r in records if r["event"] == "failed" and r["ts"] >= cutoff)


def side_status(job: Job, side: str, *, scan_source: bool = False,
                remote: bool = False) -> SideStatus:
    records = ledger(job, side).records()
    event = "pushed" if side == "sender" else "verified"
    latest: dict[str, dict] = {}
    for r in records:
        if r["event"] == event:
            latest[r["key"]] = r
    done = list(latest.values())
    extra: dict = {}
    rate = _rate(done)
    avg = sum(r["size"] for r in done) / len(done) if done else None
    if side == "sender":
        items = staged(job.sender.stage_dir)
        extra["staged"] = len(items)
        extra["staged_bytes"] = dir_bytes(job.sender.stage_dir)
        extra["source_deleted"] = len({r["key"] for r in records if r["event"] == "source_deleted"})
        extra["seeded"] = len({r["key"] for r in records if r["event"] == "seeded"})
        extra["resent_revisions"] = sum(1 for r in done if r.get("revision", 1) > 1)
        if scan_source:
            remaining = [u.key for u in enumerate_units(job.units) if u.key not in latest]
            extra["remaining_units"] = len(remaining)
            if avg and rate:
                extra["eta_hours"] = round(len(remaining) * avg / 1e6 / rate / 3600, 1)
    if remote:
        adapter = adapter_for(job, side)
        extra["remote_free_bytes"] = adapter.free_bytes()
        extra["remote_units"] = sum(1 for e in adapter.list(job.remote.prefix)
                                    if e.endswith("/") and not e.startswith("_"))
    return SideStatus(side=side, done=len(done), done_bytes=sum(r["size"] for r in done),
                      recent_mb_s=rate, failures_last_24h=_recent_failures(records),
                      last_event=records[-1] if records else None, extra=extra)
