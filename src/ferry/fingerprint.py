"""Cheap, host-independent unit fingerprints (stat only, no content read).

The fingerprint hashes the sorted ``relative path<TAB>size`` list of every file in a
unit. It is equal on two hosts holding the same files regardless of mtimes, so it serves
both change detection on the sender and seeding from data the receiver already has.
Content integrity is still guaranteed separately by sha256 during transfer.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .job import Unit


@dataclass(frozen=True)
class Fingerprint:
    digest: str
    files: int
    bytes: int
    newest_mtime: float


def files_of(unit: Unit) -> list[tuple[str, os.stat_result]]:
    out = []
    for rel in unit.members:
        top = unit.root / rel
        if top.is_file():
            out.append((rel, top.stat()))
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames.sort()
            for name in filenames:
                path = Path(dirpath) / name
                out.append((str(path.relative_to(unit.root)), path.stat()))
    out.sort()
    return out


def fingerprint(unit: Unit) -> Fingerprint:
    h = hashlib.sha256()
    total = 0
    newest = 0.0
    entries = files_of(unit)
    for rel, st in entries:
        h.update(f"{rel}\t{st.st_size}\n".encode())
        total += st.st_size
        newest = max(newest, st.st_mtime)
    return Fingerprint(h.hexdigest(), len(entries), total, newest)
