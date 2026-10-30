"""Any rclone remote (Google Drive, S3, OneDrive, ...).

``options.remote`` is the rclone remote with optional base path, e.g. ``"gdrive:"`` or
``"s3:bucket/relay"``. rclone honours proxy environment variables, which the egress guard
strips; endpoints for the route check come from ``options.endpoints`` or are inferred
from the remote's backend type.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..proc import CommandError, run, run_transfer
from .base import Adapter

_BACKEND_ENDPOINTS = {
    "drive": ("www.googleapis.com",),
    "onedrive": ("graph.microsoft.com",),
    "dropbox": ("api.dropboxapi.com", "content.dropboxapi.com"),
    "box": ("api.box.com", "upload.box.com"),
    "b2": ("api.backblazeb2.com",),
}


class RcloneAdapter(Adapter):
    name = "rclone"
    supports_rate_limit = True

    def __init__(self, options: dict, log: Path | None = None):
        super().__init__(options, log)
        self.bin = options.get("binary") or shutil.which("rclone") or "rclone"
        self.remote = options["remote"]
        if ":" not in self.remote:
            raise ValueError("rclone remote must look like 'name:' or 'name:path'")
        self.flags = list(options.get("flags", []))
        self.endpoints = tuple(options.get("endpoints") or self._infer_endpoints())

    def _infer_endpoints(self) -> tuple[str, ...]:
        name = self.remote.split(":", 1)[0]
        try:
            remotes = run([self.bin, "listremotes", "--long"], timeout=60)
        except (OSError, CommandError):
            return ()
        for line in remotes.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == f"{name}:":
                return _BACKEND_ENDPOINTS.get(parts[1], ())
        return ()

    def _full(self, remote: str) -> str:
        base = self.remote if self.remote.endswith(":") else self.remote.rstrip("/") + "/"
        return base + remote.lstrip("/")

    def check(self) -> list[str]:
        if not shutil.which(self.bin) and not Path(self.bin).is_file():
            return [f"rclone not found ({self.bin})"]
        problems = []
        if not self.endpoints:
            problems.append("rclone endpoints unknown; set remote.options.endpoints "
                            "so the egress guard can check routes")
        return problems

    def _bwlimit(self) -> list[str]:
        if not (self.upload_rate or self.download_rate):
            return []
        fmt = lambda n: f"{n}B" if n else "off"  # noqa: E731
        return ["--bwlimit", f"{fmt(self.upload_rate)}:{fmt(self.download_rate)}"]

    def put(self, local: Path, remote: str) -> None:
        run_transfer([self.bin, "copyto", *self.flags, *self._bwlimit(), str(local),
                      self._full(remote)], log=self.log)

    def get(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        run_transfer([self.bin, "copyto", *self.flags, *self._bwlimit(), self._full(remote),
                      str(local)], log=self.log)

    def _lsjson(self, remote: str, *flags: str) -> list[dict] | dict | None:
        try:
            out = run([self.bin, "lsjson", *flags, self._full(remote)], timeout=300)
        except CommandError as e:
            if "not found" in e.output.lower() or "doesn't exist" in e.output.lower():
                return None
            raise
        return json.loads(out)

    def stat(self, remote: str) -> int | None:
        info = self._lsjson(remote, "--stat")
        if not info or info.get("IsDir"):
            return None
        return int(info["Size"])

    def list(self, remote_dir: str) -> list[str]:
        entries = self._lsjson(remote_dir) or []
        return sorted(e["Name"] + ("/" if e.get("IsDir") else "") for e in entries)

    def delete(self, remote: str) -> None:
        info = self._lsjson(remote, "--stat")
        if info is None:
            return
        verb = "purge" if info.get("IsDir") else "deletefile"
        run([self.bin, verb, self._full(remote)], timeout=600)

    def free_bytes(self) -> int | None:
        try:
            about = json.loads(run([self.bin, "about", "--json", self.remote.split(":")[0] + ":"],
                                   timeout=120))
        except (CommandError, json.JSONDecodeError):
            return None
        return about.get("free")
