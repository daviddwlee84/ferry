"""Direct host-to-host relay over ssh with rsync (no cloud in between).

The sender pushes parts into ``root`` on ``host``; on that host the receiver reads the same
directory with the ``local`` adapter (``[remote.sides.receiver]`` override), so the
protocol, verification, ledgers and deletion policies are identical to cloud relays.
rsync writes to a temporary name and renames, so a part is visible only when complete.
``host = ""`` runs everything locally (tests, or a mounted path).
"""
from __future__ import annotations

import posixpath
import re
import shlex
import shutil
from pathlib import Path

from ..proc import CommandError, run, run_transfer
from .base import Adapter

_PROXY_HINT = re.compile(r"(nc|ncat|connect|corkscrew|socat).*(-x|-X|--proxy|socks|7890|7891)",
                         re.I)


def _bwlimit(rate: int | None) -> list[str]:
    return [f"--bwlimit={max(1, rate // 1024)}"] if rate else []


class RsyncAdapter(Adapter):
    name = "rsync"
    supports_rate_limit = True

    def __init__(self, options: dict, log: Path | None = None):
        super().__init__(options, log)
        self.host = options.get("host", "")
        self.root = options["root"].rstrip("/") or "/"
        self.ssh = ["ssh", *options.get("ssh_options", ["-o", "BatchMode=yes"])]
        self.rsync = options.get("binary") or shutil.which("rsync") or "rsync"
        self.flags = list(options.get("flags", ["--partial"]))
        self._ssh_config: dict[str, str] | None = None
        self.endpoints = (self._config().get("hostname", self.host),) if self.host else ()

    def _config(self) -> dict[str, str]:
        if self._ssh_config is None:
            self._ssh_config = {}
            if self.host:
                try:
                    out = run(["ssh", "-G", self.host], timeout=30)
                except (OSError, CommandError):
                    out = ""
                for line in out.splitlines():
                    key, _, value = line.partition(" ")
                    self._ssh_config[key] = value
        return self._ssh_config

    def _abs(self, remote: str) -> str:
        return posixpath.join(self.root, remote.lstrip("/"))

    def _sh(self, command: str, timeout: float = 120) -> str:
        argv = [*self.ssh, self.host, command] if self.host else ["sh", "-c", command]
        return run(argv, timeout=timeout)

    def _target(self, path: str) -> str:
        return f"{self.host}:{shlex.quote(path)}" if self.host else path

    def check(self) -> list[str]:
        try:
            self._sh(f"test -d {shlex.quote(self.root)} || mkdir -p {shlex.quote(self.root)}")
        except (OSError, CommandError) as e:
            return [f"cannot reach {self.host or 'local'}:{self.root}: {e}"]
        return []

    def proxy_settings(self) -> list[str]:
        cfg = self._config()
        found = []
        command = cfg.get("proxycommand", "none")
        if command != "none" and _PROXY_HINT.search(command):
            found.append(f"ssh ProxyCommand looks like a proxy: {command}")
        if cfg.get("proxyjump", "none") != "none":
            found.append(f"ssh ProxyJump {cfg['proxyjump']} (traffic relayed by the jump host)")
        return found

    def put(self, local: Path, remote: str) -> None:
        path = self._abs(remote)
        self._sh(f"mkdir -p {shlex.quote(posixpath.dirname(path))}")
        ssh = ["-e", shlex.join(self.ssh)] if self.host else []
        run_transfer([self.rsync, *self.flags, *_bwlimit(self.upload_rate), *ssh, str(local),
                      self._target(path)], log=self.log)

    def get(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        ssh = ["-e", shlex.join(self.ssh)] if self.host else []
        run_transfer([self.rsync, *self.flags, *_bwlimit(self.download_rate), *ssh,
                      self._target(self._abs(remote)), str(local)], log=self.log)

    def stat(self, remote: str) -> int | None:
        path = shlex.quote(self._abs(remote))
        out = self._sh(f"if [ -f {path} ]; then wc -c < {path}; fi").strip()
        return int(out) if out else None

    def list(self, remote_dir: str) -> list[str]:
        path = shlex.quote(self._abs(remote_dir))
        out = self._sh(f"if [ -d {path} ]; then cd {path} && ls -1Ap; fi")
        return sorted(n for n in out.splitlines() if n and not n.startswith("."))

    def delete(self, remote: str) -> None:
        path = self._abs(remote)
        if path.rstrip("/") == self.root:
            raise ValueError("refusing to delete the relay root")
        self._sh(f"rm -rf -- {shlex.quote(path)}")

    def free_bytes(self) -> int | None:
        out = self._sh(f"df -Pk {shlex.quote(self.root)} | tail -1")
        cols = out.split()
        return int(cols[3]) * 1024 if len(cols) >= 4 and cols[3].isdigit() else None
