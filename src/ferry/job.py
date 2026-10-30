"""Job specification (TOML) and unit enumeration.

A *unit* is the smallest resumable piece of work: one key, one tar (or one raw file),
one remote directory. Units are enumerated either from ``keys_from`` + ``members``
templates (several paths per key, e.g. one trading day across kinds) or from ``glob``
(each match is its own unit).
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .sizes import parse_size
from .windows import Window

DELETE_POLICIES = ("never", "after_remote_size", "after_receiver_ack")
EGRESS_POLICIES = ("direct", "any")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class JobError(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    key: str
    root: Path
    members: tuple[str, ...]  # paths relative to root that exist


@dataclass(frozen=True)
class UnitsSpec:
    root: Path
    keys_from: str | None = None
    members: tuple[str, ...] = ()
    glob: str | None = None
    pack: str = "tar"  # "tar" | "none" (single regular file, sent byte for byte)
    #: Skip units whose newest file is younger than this (still being written).
    settle_seconds: float = 0
    #: Re-send a unit when its fingerprint changed since it was pushed.
    resend_changed: bool = True
    #: After the first pass, only the newest N keys are re-fingerprinted each round.
    recheck_recent: int | None = None


@dataclass(frozen=True)
class SenderSpec:
    stage_dir: Path
    stage_cap: int | None = None
    read_blocked: tuple[Window, ...] = ()
    delete_source: str = "never"


@dataclass(frozen=True)
class ReceiverSpec:
    dest: str  # template; {key} and slices like {key[0:4]}
    extract: bool = False
    #: Wait instead of receiving while the destination has less free space than this
    #: plus about twice the unit size (download + reassembly/extract).
    min_free: int = 0


@dataclass(frozen=True)
class RemoteSpec:
    adapter: str
    prefix: str
    part_size: int | None = None
    min_free: int = 0
    max_inflight: int | None = None
    egress: str = "direct"
    #: Interfaces the operator verified as direct (e.g. a TUN whose rules send this
    #: backend DIRECT); routes through them are not egress findings.
    trusted_interfaces: tuple[str, ...] = ()
    options: dict = field(default_factory=dict)
    #: Per-side adapter overrides, e.g. the receiver reads an rsync target as "local".
    sides: dict = field(default_factory=dict)

    #: Bandwidth caps in bytes/second for transfers started on this host (None = no cap).
    upload_rate: int | None = None
    download_rate: int | None = None

    def for_side(self, side: str) -> tuple[str, dict]:
        override = self.sides.get(side, {})
        return override.get("adapter", self.adapter), dict(override.get("options", self.options))

    def rates_for_side(self, side: str) -> tuple[int | None, int | None]:
        """(upload, download) caps; a side override wins over the [remote] default."""
        override = self.sides.get(side, {})
        up = parse_size(override["upload_rate"]) if "upload_rate" in override else self.upload_rate
        down = (parse_size(override["download_rate"]) if "download_rate" in override
                else self.download_rate)
        return up, down


@dataclass(frozen=True)
class Job:
    name: str
    path: Path | None
    state_dir: Path
    units: UnitsSpec
    sender: SenderSpec
    receiver: ReceiverSpec | None
    remote: RemoteSpec


def _duration(value: str | int | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", value)
    if not m:
        raise JobError(f"invalid duration {value!r}; use e.g. '30m', '6h', '2d'")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def _path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value)))


def load_job(path: str | Path) -> Job:
    path = Path(path)
    with path.open("rb") as f:
        return job_from_dict(tomllib.load(f), path)


def job_from_dict(data: dict, path: Path | None = None) -> Job:
    try:
        name = data["name"]
        if not _KEY.fullmatch(name):
            raise JobError(f"job name {name!r} must be [A-Za-z0-9._-]")
        u, s, r = data["units"], data.get("sender", {}), data["remote"]
        units = UnitsSpec(root=_path(u["root"]), keys_from=u.get("keys_from"),
                          members=tuple(u.get("members", ())), glob=u.get("glob"),
                          pack=u.get("pack", "tar"),
                          settle_seconds=_duration(u.get("settle", 0)),
                          resend_changed=bool(u.get("resend_changed", True)),
                          recheck_recent=u.get("recheck_recent"))
        if bool(units.keys_from) == bool(units.glob):
            raise JobError("units needs exactly one of keys_from (+members) or glob")
        if units.keys_from and not units.members:
            raise JobError("units.keys_from requires units.members templates")
        if units.pack not in ("tar", "none"):
            raise JobError("units.pack must be 'tar' or 'none'")
        state_dir = _path(data.get("state_dir", f"~/.local/state/ferry/{name}"))
        sender = SenderSpec(
            stage_dir=_path(s.get("stage_dir", str(state_dir / "stage"))),
            stage_cap=parse_size(s.get("stage_cap")),
            read_blocked=tuple(Window.parse(w) for w in s.get("read_blocked", ())),
            delete_source=s.get("delete_source", "never"))
        if sender.delete_source not in DELETE_POLICIES:
            raise JobError(f"sender.delete_source must be one of {DELETE_POLICIES}")
        receiver = None
        if "receiver" in data:
            rc = data["receiver"]
            receiver = ReceiverSpec(dest=rc["dest"], extract=bool(rc.get("extract", False)),
                                    min_free=parse_size(rc.get("min_free", 0)) or 0)
        remote = RemoteSpec(adapter=r["adapter"], prefix=r["prefix"].rstrip("/"),
                            part_size=parse_size(r.get("part_size")),
                            min_free=parse_size(r.get("min_free", 0)) or 0,
                            max_inflight=parse_size(r.get("max_inflight")),
                            egress=r.get("egress", "direct"),
                            trusted_interfaces=tuple(r.get("trusted_interfaces", ())),
                            options=dict(r.get("options", {})),
                            sides=dict(r.get("sides", {})),
                            upload_rate=parse_size(r.get("upload_rate")),
                            download_rate=parse_size(r.get("download_rate")))
        if set(remote.sides) - {"sender", "receiver"}:
            raise JobError("remote.sides keys must be 'sender' or 'receiver'")
        if remote.egress not in EGRESS_POLICIES:
            raise JobError(f"remote.egress must be one of {EGRESS_POLICIES}")
    except KeyError as e:
        raise JobError(f"missing required job field {e}") from None
    return Job(name, path, state_dir, units, sender, receiver, remote)


def enumerate_units(spec: UnitsSpec) -> list[Unit]:
    """All units currently present under the source root, sorted by key."""
    root = spec.root
    units: dict[str, Unit] = {}
    if spec.keys_from:
        for match in root.glob(spec.keys_from):
            key = match.stem if match.is_file() else match.name
            if not _KEY.fullmatch(key):
                raise JobError(f"derived key {key!r} is not a safe name")
            members = tuple(m for m in (t.format(key=key) for t in spec.members)
                            if (root / m).exists())
            units[key] = Unit(key, root, members)
    else:
        for match in root.glob(spec.glob or ""):
            # Remote names starting with "_" are reserved for protocol files.
            key = re.sub(r"[^A-Za-z0-9._-]", "_", match.name).lstrip("._-")
            if key in units:
                raise JobError(f"two glob matches map to key {key!r}")
            units[key] = Unit(key, root, (str(match.relative_to(root)),))
    if spec.pack == "none":
        for unit in units.values():
            if len(unit.members) != 1 or not (root / unit.members[0]).is_file():
                raise JobError(f"pack='none' needs exactly one regular file per unit ({unit.key})")
    return [units[k] for k in sorted(units)]


def dest_dir(receiver: ReceiverSpec, key: str) -> Path:
    def slice_(m: re.Match) -> str:
        a, b = (int(x) if x else None for x in (m.group(1), m.group(2)))
        return key[a:b]

    text = re.sub(r"\{key\[(-?\d*):(-?\d*)\]\}", slice_, receiver.dest).replace("{key}", key)
    return _path(text)
