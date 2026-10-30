"""Host-wide transfer budget shared by every ferry job on this machine.

``$XDG_CONFIG_HOME/ferry/host.toml`` (or ``$FERRY_HOST_CONFIG``)::

    upload_rate = "16Mbps"     # total budget for this host, per direction
    download_rate = "240Mbps"
    max_transfers = 1          # concurrent ferry transfers per direction

Backend tools (BaiduPCS-Go, rclone, rsync) fix their rate when they start, so the total
cannot be split dynamically. Instead each upload or download takes one of
``max_transfers`` slots (file locks under ``$XDG_STATE_HOME/ferry/host-slots``) and runs
at ``rate / max_transfers``; transfers beyond the slots wait. That bounds the total of
all ferry transfers on the host without root. A job's own cap still applies when it is
lower. Traffic from programs other than ferry is not covered (use tc or the router).
"""
from __future__ import annotations

import fcntl
import os
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .sizes import parse_size

DIRECTIONS = ("upload", "download")


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var, "")
    return Path(value) if os.path.isabs(value) else Path(os.path.expanduser(default))


def default_config_path() -> Path:
    if os.environ.get("FERRY_HOST_CONFIG"):
        return Path(os.environ["FERRY_HOST_CONFIG"]).expanduser()
    return _xdg("XDG_CONFIG_HOME", "~/.config") / "ferry" / "host.toml"


def default_slot_dir() -> Path:
    return _xdg("XDG_STATE_HOME", "~/.local/state") / "ferry" / "host-slots"


@dataclass(frozen=True)
class HostBudget:
    upload_rate: int | None = None
    download_rate: int | None = None
    max_transfers: int | None = None  # None: no slot limit
    slot_dir: Path = Path()
    source: Path | None = None

    def rate(self, direction: str) -> int | None:
        return self.upload_rate if direction == "upload" else self.download_rate

    def per_transfer(self, direction: str) -> int | None:
        total = self.rate(direction)
        if not total:
            return None
        return total // (self.max_transfers or 1)

    def effective(self, direction: str, job_rate: int | None) -> int | None:
        """The stricter of the job cap and this host's per-transfer share."""
        caps = [r for r in (job_rate, self.per_transfer(direction)) if r]
        return min(caps) if caps else None

    @property
    def active(self) -> bool:
        return bool(self.upload_rate or self.download_rate or self.max_transfers)

    @contextmanager
    def slot(self, direction: str, *, poll: float = 1.0, on_wait=None):
        """Hold one of ``max_transfers`` slots for ``direction`` (no-op when unlimited)."""
        if not self.max_transfers:
            yield None
            return
        directory = self.slot_dir / direction
        directory.mkdir(parents=True, exist_ok=True)
        waited = False
        while True:
            for i in range(self.max_transfers):
                fh = (directory / f"slot-{i}.lock").open("a+")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    fh.close()
                    continue
                fh.seek(0)
                fh.truncate()
                fh.write(f"{os.getpid()}\n")
                fh.flush()
                try:
                    yield i
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                    fh.close()
                return
            if not waited and on_wait is not None:
                on_wait(direction, self.max_transfers)
                waited = True
            time.sleep(poll)


def load_host_budget(path: Path | None = None) -> HostBudget:
    path = path or default_config_path()
    slot_dir = default_slot_dir()
    if not path.is_file():
        return HostBudget(slot_dir=slot_dir)
    with path.open("rb") as f:
        data = tomllib.load(f)
    unknown = set(data) - {"upload_rate", "download_rate", "max_transfers", "slot_dir"}
    if unknown:
        raise ValueError(f"{path}: unknown host settings {sorted(unknown)}")
    up, down = parse_size(data.get("upload_rate")), parse_size(data.get("download_rate"))
    max_transfers = data.get("max_transfers")
    if max_transfers is None and (up or down):
        max_transfers = 1  # a host total is only guaranteed when transfers are bounded
    if max_transfers is not None and (not isinstance(max_transfers, int) or max_transfers < 1):
        raise ValueError(f"{path}: max_transfers must be a positive integer")
    if "slot_dir" in data:
        slot_dir = Path(os.path.expanduser(data["slot_dir"]))
    return HostBudget(up, down, max_transfers, slot_dir, path)
