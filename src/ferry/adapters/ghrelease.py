"""Assets of a GitHub *draft* release as the relay, through the ``gh`` CLI.

Draft releases are visible only to repository collaborators, which makes them a handy
private staging area. Remote paths are flattened into asset names (``/`` -> ``__``).
Each asset must be below 2 GiB, so the default part size is 1900 MiB.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from ..proc import CommandError, run, run_transfer
from .base import Adapter

SEP = "__"


def flat(remote: str) -> str:
    name = remote.strip("/").replace("/", SEP)
    if not name:
        raise ValueError("empty remote path")
    return name


class GhReleaseAdapter(Adapter):
    name = "ghrelease"
    endpoints = ("api.github.com", "uploads.github.com", "github.com",
                 "objects.githubusercontent.com", "release-assets.githubusercontent.com")
    max_part_size = 2 * 1024**3 - 1
    default_part_size = 1900 * 1024**2

    def __init__(self, options: dict, log: Path | None = None):
        super().__init__(options, log)
        self.bin = options.get("binary") or shutil.which("gh") or "gh"
        self.repo = options["repo"]
        self.tag = options.get("tag", "ferry-relay")
        self._ensured = False

    def _gh(self, *args: str, timeout: float = 300) -> str:
        return run([self.bin, *args, "-R", self.repo], timeout=timeout)

    def _assets(self) -> dict[str, int]:
        try:
            out = self._gh("release", "view", self.tag, "--json", "assets,isDraft")
        except CommandError as e:
            if "release not found" in e.output.lower():
                return {}
            raise
        return {a["name"]: int(a["size"]) for a in json.loads(out)["assets"]}

    def _ensure_release(self) -> None:
        if self._ensured:
            return
        try:
            info = json.loads(self._gh("release", "view", self.tag, "--json", "isDraft"))
            if not info.get("isDraft"):
                raise RuntimeError(f"release {self.tag} in {self.repo} is published, not a draft;"
                                   " refusing to stage data in a public release")
        except CommandError as e:
            if "release not found" not in e.output.lower():
                raise
            self._gh("release", "create", self.tag, "--draft", "--title", "ferry relay",
                     "--notes", "Staging area for ferry transfers. Assets are temporary.")
        self._ensured = True

    def check(self) -> list[str]:
        if not shutil.which(self.bin) and not Path(self.bin).is_file():
            return [f"gh not found ({self.bin})"]
        try:
            run([self.bin, "auth", "status"], timeout=60)
        except CommandError:
            return ["gh is not authenticated"]
        return []

    def put(self, local: Path, remote: str) -> None:
        if local.stat().st_size > self.max_part_size:
            raise ValueError(f"{local} exceeds the 2 GiB GitHub asset limit; lower part_size")
        self._ensure_release()
        tmp = Path(tempfile.mkdtemp(dir=local.parent))
        try:
            named = tmp / flat(remote)
            try:
                os.link(local, named)
            except OSError:
                shutil.copyfile(local, named)
            run_transfer([self.bin, "release", "upload", self.tag, str(named), "--clobber",
                          "-R", self.repo], log=self.log)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def get(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=local.parent))
        try:
            run_transfer([self.bin, "release", "download", self.tag, "-p", flat(remote),
                          "-D", str(tmp), "--clobber", "-R", self.repo], log=self.log)
            (tmp / flat(remote)).replace(local)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def stat(self, remote: str) -> int | None:
        return self._assets().get(flat(remote))

    def list(self, remote_dir: str) -> list[str]:
        prefix = flat(remote_dir) + SEP if remote_dir.strip("/") else ""
        children = set()
        for name in self._assets():
            if name.startswith(prefix):
                head, sep, _ = name[len(prefix):].partition(SEP)
                children.add(head + ("/" if sep else ""))
        return sorted(children)

    def delete(self, remote: str) -> None:
        target = flat(remote)
        for name in self._assets():
            if name == target or name.startswith(target + SEP):
                self._gh("release", "delete-asset", self.tag, name, "-y")
