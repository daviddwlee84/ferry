"""Cheap, host-independent unit fingerprints (stat only, no content read).

The fingerprint hashes the sorted ``relative path<TAB>size`` list of every file in a
unit. It is equal on two hosts holding the same files regardless of mtimes, so it serves
both change detection on the sender and seeding from data the receiver already has.
Content integrity is still guaranteed separately by sha256 during transfer.

Symlinks are never followed (``lstat``): they are packed as links, so a dangling link
must not crash the sender and a link's target size must not count as unit content.
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
        if top.is_symlink() or top.is_file():
            out.append((rel, top.lstat()))
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames.sort()
            # os.walk never descends into symlinked directories but lists them as dirs;
            # they are unit content (a link entry), so record them as files.
            links = [d for d in dirnames if (Path(dirpath) / d).is_symlink()]
            for name in filenames + links:
                path = Path(dirpath) / name
                out.append((str(path.relative_to(unit.root)), path.lstat()))
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
