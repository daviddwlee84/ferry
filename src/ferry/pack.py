"""Pack a unit into size-limited parts with sha256 computed while streaming (one read)."""
from __future__ import annotations

import hashlib
import shutil
import socket
import tarfile
from pathlib import Path

from .job import Unit
from .protocol import Manifest, Part, utcnow

COPY_BUF = 8 << 20


class _PartWriter:
    """File-like sink that splits the stream into ``part-NNNN`` files and hashes everything."""

    def __init__(self, directory: Path, part_size: int | None):
        self.dir = directory
        self.part_size = part_size
        self.total = hashlib.sha256()
        self.size = 0
        self.parts: list[Part] = []
        self._fh = None
        self._hash = None
        self._written = 0

    def _open(self) -> None:
        name = f"part-{len(self.parts):04d}"
        self._fh = (self.dir / name).open("wb")
        self._hash = hashlib.sha256()
        self._written = 0
        self.parts.append(Part(name, 0, ""))

    def _close(self) -> None:
        if self._fh is None:
            return
        self._fh.close()
        last = self.parts[-1]
        self.parts[-1] = Part(last.name, self._written, self._hash.hexdigest())
        self._fh = None

    def write(self, data) -> int:
        view = memoryview(data)
        while view:
            if self._fh is None or (self.part_size and self._written >= self.part_size):
                self._close()
                self._open()
            room = len(view) if not self.part_size else self.part_size - self._written
            chunk = view[:room]
            self._fh.write(chunk)
            self._hash.update(chunk)
            self.total.update(chunk)
            self._written += len(chunk)
            self.size += len(chunk)
            view = view[room:]
        return len(data)

    def close(self) -> None:
        self._close()


def pack_unit(unit: Unit, stage: Path, *, job: str, fmt: str, part_size: int | None,
              ack_required: bool, fingerprint: str = "", revision: int = 1) -> Manifest:
    """Write parts + manifest.json into ``stage/<key>`` atomically (via a .tmp directory)."""
    final = stage / unit.key
    tmp = stage / f"{unit.key}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    writer = _PartWriter(tmp, part_size)
    if fmt == "tar":
        members = 0
        with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT,
                          bufsize=COPY_BUF, copybufsize=COPY_BUF) as tar:
            for rel in unit.members:
                for path in _walk(unit.root / rel):
                    tar.add(path, arcname=str(path.relative_to(unit.root)), recursive=False)
                    members += not path.is_dir()
        filename = f"{unit.key}.tar"
    else:
        source = unit.root / unit.members[0]
        with source.open("rb") as f:
            shutil.copyfileobj(f, writer, COPY_BUF)
        members, filename = 1, source.name
    writer.close()
    if writer.size == 0:
        shutil.rmtree(tmp)
        raise ValueError(f"unit {unit.key} produced an empty payload")
    manifest = Manifest(key=unit.key, job=job, format="tar" if fmt == "tar" else "raw",
                        filename=filename, size=writer.size, sha256=writer.total.hexdigest(),
                        parts=tuple(writer.parts), members=members, ack_required=ack_required,
                        source_host=socket.gethostname(), created_at=utcnow(),
                        paths=tuple(unit.members), fingerprint=fingerprint, revision=revision)
    (tmp / "manifest.json").write_text(manifest.to_json())
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    return manifest


def _walk(path: Path):
    """Deterministic (sorted) traversal including directories, so tar output is stable."""
    yield path
    if path.is_dir() and not path.is_symlink():
        for child in sorted(path.iterdir()):
            yield from _walk(child)


def staged(stage: Path) -> list[Path]:
    """Completed staged units (manifest written), oldest key first."""
    if not stage.exists():
        return []
    return sorted(p for p in stage.iterdir()
                  if p.is_dir() and not p.name.endswith(".tmp") and (p / "manifest.json").exists())


def dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0
