"""Remote layout, manifest schema and the append-only ledger.

Remote layout for job prefix P and unit key K::

    P/K/part-0000 ...      payload parts (never empty)
    P/K/manifest.json      uploaded last; its presence means the unit is READY
    P/_acks/K.json         receiver acknowledgement (only when the sender asks for one)
    P/_sender_done.json    sender finished every unit it knows about

Every remote object is non-empty because some backends (Baidu) silently drop empty
files. Integrity uses only our own sha256 and byte sizes, never backend checksums.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

MANIFEST_VERSION = 1


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Part:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    key: str
    job: str
    format: str  # "tar" | "raw"
    filename: str  # name of the reassembled payload at the destination
    size: int
    sha256: str
    parts: tuple[Part, ...]
    members: int  # files inside the tar (1 for raw)
    ack_required: bool
    source_host: str
    created_at: str
    version: int = MANIFEST_VERSION
    paths: tuple[str, ...] = ()  # unit members relative to the source root
    fingerprint: str = ""  # see ferry.fingerprint
    revision: int = 1  # bumped each time a changed unit is re-sent

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        data = json.loads(text)
        if data.get("version") != MANIFEST_VERSION:
            raise ValueError(f"unsupported manifest version {data.get('version')}")
        data["parts"] = tuple(Part(**p) for p in data["parts"])
        data["paths"] = tuple(data.get("paths", ()))
        return cls(**data)


def unit_dir(prefix: str, key: str) -> str:
    return f"{prefix}/{key}"


def ack_path(prefix: str, key: str) -> str:
    return f"{prefix}/_acks/{key}.json"


def sender_done_path(prefix: str) -> str:
    return f"{prefix}/_sender_done.json"


def inventory_path(prefix: str) -> str:
    """Receiver's fingerprints of data it already holds; the sender seeds from it."""
    return f"{prefix}/_inventory.json"


class Ledger:
    """Append-only JSONL event log; the replayed state decides what is left to do."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, key: str | None = None, **fields) -> None:
        record = {"ts": utcnow(), "event": event, "key": key, **fields}
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # torn final line after a crash
        return out

    def keys(self, event: str) -> dict[str, dict]:
        """Latest record of ``event`` per key."""
        return {r["key"]: r for r in self.records() if r["event"] == event and r.get("key")}
