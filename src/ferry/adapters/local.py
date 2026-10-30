"""A directory used as the relay (tests, or a share mounted on both hosts)."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .base import Adapter


def _copy(src: Path, dst: Path, rate: int | None) -> None:
    """Copy, sleeping as needed to stay at or below `rate` bytes/second."""
    if not rate:
        shutil.copyfile(src, dst)
        return
    chunk = max(4096, min(rate // 4, 8 << 20))
    start, done = time.monotonic(), 0
    with src.open("rb") as fin, dst.open("wb") as fout:
        while data := fin.read(chunk):
            fout.write(data)
            done += len(data)
            ahead = done / rate - (time.monotonic() - start)
            if ahead > 0:
                time.sleep(ahead)


class LocalAdapter(Adapter):
    name = "local"
    supports_rate_limit = True

    def __init__(self, options: dict, log: Path | None = None):
        super().__init__(options, log)
        self.root = Path(os.path.expanduser(options["root"]))
        self.drop_empty = bool(options.get("drop_empty", False))  # emulate Baidu in tests

    def _p(self, remote: str) -> Path:
        path = (self.root / remote.lstrip("/")).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"remote path escapes root: {remote}")
        return path

    def check(self) -> list[str]:
        return [] if self.root.is_dir() else [f"local root {self.root} does not exist"]

    def put(self, local: Path, remote: str) -> None:
        if self.drop_empty and local.stat().st_size == 0:
            return
        dest = self._p(remote)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".uploading")
        _copy(local, tmp, self.upload_rate)
        tmp.replace(dest)

    def get(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        _copy(self._p(remote), local, self.download_rate)

    def stat(self, remote: str) -> int | None:
        p = self._p(remote)
        return p.stat().st_size if p.is_file() else None

    def list(self, remote_dir: str) -> list[str]:
        p = self._p(remote_dir)
        if not p.is_dir():
            return []
        return sorted(c.name + ("/" if c.is_dir() else "") for c in p.iterdir()
                      if not c.name.endswith(".uploading"))

    def delete(self, remote: str) -> None:
        p = self._p(remote)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)

    def free_bytes(self) -> int | None:
        if "free" in self.options:  # tests can pin the reported free space
            return int(self.options["free"])
        return shutil.disk_usage(self.root).free
