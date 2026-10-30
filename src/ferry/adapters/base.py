"""Backend adapter interface.

Remote paths are POSIX-style strings rooted at the backend (``/ferry/cf/20200102/part-0000``).
Adapters must make a file visible only once it is complete (cloud uploads do this
naturally; the local adapter writes to a temporary name and renames).
"""
from __future__ import annotations

import posixpath
from abc import ABC, abstractmethod
from pathlib import Path


class Adapter(ABC):
    name: str = "?"
    #: Hostnames contacted by this backend; checked by the egress guard.
    endpoints: tuple[str, ...] = ()
    #: Largest single object the backend accepts (None = no known limit).
    max_part_size: int | None = None
    #: Part size used when the job does not set one.
    default_part_size: int | None = None

    def __init__(self, options: dict, log: Path | None = None):
        self.options = options
        self.log = log

    def check(self) -> list[str]:
        """Problems that make the backend unusable (missing binary, not logged in...)."""
        return []

    def proxy_settings(self) -> list[str]:
        """Proxies configured inside the backend tool itself (not via environment)."""
        return []

    @abstractmethod
    def put(self, local: Path, remote: str) -> None: ...

    @abstractmethod
    def get(self, remote: str, local: Path) -> None: ...

    @abstractmethod
    def stat(self, remote: str) -> int | None:
        """Size of a remote file, or None when it does not exist."""

    @abstractmethod
    def list(self, remote_dir: str) -> list[str]:
        """Child names of a remote directory; directories end with '/'. [] if missing."""

    @abstractmethod
    def delete(self, remote: str) -> None:
        """Delete a file or a whole directory; missing paths are not an error."""

    def free_bytes(self) -> int | None:
        return None

    def put_text(self, text: str, remote: str, scratch: Path) -> None:
        """Upload a small text object (manifests, acks) via a scratch file of the same name."""
        scratch.mkdir(parents=True, exist_ok=True)
        local = scratch / posixpath.basename(remote)
        local.write_text(text)
        try:
            self.put(local, remote)
        finally:
            local.unlink(missing_ok=True)

    def get_text(self, remote: str, scratch: Path) -> str | None:
        if self.stat(remote) is None:
            return None
        scratch.mkdir(parents=True, exist_ok=True)
        local = scratch / posixpath.basename(remote)
        try:
            self.get(remote, local)
            return local.read_text()
        finally:
            local.unlink(missing_ok=True)
