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
import posixpath
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

MANIFEST_VERSION = 1
KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def is_safe_key(key: str) -> bool:
    """Unit keys: one path component, never starting with the reserved ``_``/``.``."""
    return bool(KEY_RE.fullmatch(key))


def is_safe_relative(path: str) -> bool:
    """A relative POSIX path that stays inside its root (no ``..``, no absolute, no NUL)."""
    if not path or "\0" in path or posixpath.isabs(path):
        return False
    return ".." not in path.split("/") and path.strip() == path


def utcnow() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


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
        manifest = cls(**data)
        manifest.validate()
        return manifest

    def validate(self) -> None:
        """Reject names that would escape the destination; the relay is not trusted."""
        if not is_safe_key(self.key):
            raise ValueError(f"unsafe unit key {self.key!r}")
        if not is_safe_relative(self.filename) or "/" in self.filename:
            raise ValueError(f"{self.key}: unsafe filename {self.filename!r}")
        for rel in self.paths:
            if not is_safe_relative(rel):
                raise ValueError(f"{self.key}: unsafe member path {rel!r}")
        if not self.parts:
            raise ValueError(f"{self.key}: manifest lists no parts")
        for part in self.parts:
            if not is_safe_relative(part.name) or "/" in part.name or part.size <= 0:
                raise ValueError(f"{self.key}: unsafe part {part.name!r}")


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
    """Append-only JSONL event log; the replayed state decides what is left to do.

    The file is parsed once; later reads come from memory and ``append`` updates both,
    so a job with tens of thousands of units does not re-parse the whole log on every
    poll. One process writes a ledger at a time (``core.instance_lock``).
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._records: list[dict] | None = None
        self._latest: dict[str, dict[str, dict]] = {}  # event -> key -> record
        path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if self._records is None:
            out: list[dict] = []
            if self.path.exists():
                for line in self.path.read_text().splitlines():
                    if line.strip():
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue  # torn final line after a crash
            self._records = out
            self._latest = {}
            for r in out:
                self._index(r)
        return self._records

    def _index(self, record: dict) -> None:
        if record.get("key"):
            self._latest.setdefault(record["event"], {})[record["key"]] = record

    def append(self, event: str, key: str | None = None, **fields) -> None:
        record = {"ts": utcnow(), "event": event, "key": key, **fields}
        with self._lock:
            records = self._load()  # before writing, or the new line is read twice
            with self.path.open("a") as f:
                f.write(json.dumps(record, sort_keys=True) + "\n")
                f.flush()
            records.append(record)
            self._index(record)

    def records(self) -> list[dict]:
        with self._lock:
            return list(self._load())

    def keys(self, event: str) -> dict[str, dict]:
        """Latest record of ``event`` per key."""
        with self._lock:
            self._load()
            return dict(self._latest.get(event, {}))
