"""Baidu Pan through the BaiduPCS-Go CLI (tested with v4.0.1).

BaiduPCS-Go exits 0 even when the server reports an error (e.g. code 31066, "file or
directory does not exist"), so results are parsed from its output and every transfer is
verified by size afterwards. Reported MD5s are unreliable and never used.
Login/session state lives in BaiduPCS-Go's own config and is never read here.
"""
from __future__ import annotations

import os
import posixpath
import re
import shutil
import tempfile
from pathlib import Path

from ..proc import run, run_transfer
from .base import Adapter

_UNIT = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}
_LS_ROW = re.compile(
    r"^\s*\d+\s+(?:-|[\d.]+[KMGTP]?B)\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s{2}(.+?)\s*$")
_MISSING = ("31066", "不存在")


def parse_quota(text: str) -> tuple[int, int] | None:
    def grab(label: str) -> int | None:
        m = re.search(label + r":\s*([\d.]+)\s*([KMGTP]?B)", text)
        return int(float(m.group(1)) * _UNIT[m.group(2)]) if m else None

    total, used = grab("总空间"), grab("已用空间")
    return (total, used) if total is not None and used is not None else None


def parse_meta_size(text: str) -> int | None:
    if any(s in text for s in _MISSING):
        return None
    if re.search(r"类型\s+目录", text):
        return None
    m = re.search(r"文件大小\s+(\d+),", text)
    return int(m.group(1)) if m else None


def parse_ls(text: str) -> list[str]:
    if any(s in text for s in _MISSING):
        return []
    return [m.group(1) for line in text.splitlines() if (m := _LS_ROW.match(line))]


def parse_config_value(text: str, key: str) -> str:
    """Value column of ``BaiduPCS-Go config`` (name, value, [range], description)."""
    for line in text.splitlines():
        cols = re.split(r"\s{2,}", line.strip())
        if cols and cols[0] == key:
            return cols[1] if len(cols) >= 3 else ""
    return ""


class BaiduPCSAdapter(Adapter):
    name = "baidupcs"
    endpoints = ("pan.baidu.com", "pcs.baidu.com", "d.pcs.baidu.com", "c.pcs.baidu.com")
    default_part_size = 3900 * 1024**2  # below the 4 GB single-file limit of non-SVIP accounts

    def __init__(self, options: dict, log: Path | None = None):
        super().__init__(options, log)
        binary = options.get("binary") or shutil.which("BaiduPCS-Go") or \
            os.path.expanduser("~/.local/bin/BaiduPCS-Go")
        self.bin = str(binary)
        self.upload_threads = int(options.get("upload_threads", 0))
        self.download_threads = int(options.get("download_threads", 4))

    def _run(self, *args: str) -> str:
        return run([self.bin, *args], timeout=300)

    def check(self) -> list[str]:
        if not Path(self.bin).is_file():
            return [f"BaiduPCS-Go not found at {self.bin}"]
        if parse_quota(self._run("quota")) is None:
            return ["BaiduPCS-Go quota failed; is the account logged in?"]
        return []

    def proxy_settings(self) -> list[str]:
        if not Path(self.bin).is_file():
            return []
        value = parse_config_value(self._run("config"), "proxy")
        return [f"BaiduPCS-Go config proxy={value!r}"] if value else []

    def put(self, local: Path, remote: str) -> None:
        remote_dir, name = posixpath.split(remote)
        if name != local.name:
            raise ValueError(f"baidupcs uploads keep the local name ({local.name} != {name})")
        args = [self.bin, "upload", "--policy", "overwrite"]
        if self.upload_threads:
            args += ["-p", str(self.upload_threads)]
        run_transfer([*args, str(local), remote_dir or "/"], log=self.log)

    def get(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        name = posixpath.basename(remote)
        target = local.parent if local.name == name else Path(tempfile.mkdtemp(dir=local.parent))
        run_transfer([self.bin, "download", "-p", str(self.download_threads), "--ow",
                      "--nocheck", "--saveto", str(target), remote], log=self.log)
        if target != local.parent:
            (target / name).replace(local)
            shutil.rmtree(target, ignore_errors=True)
        if not local.is_file():
            raise RuntimeError(f"download of {remote} produced no file")

    def stat(self, remote: str) -> int | None:
        return parse_meta_size(self._run("meta", remote))

    def list(self, remote_dir: str) -> list[str]:
        return parse_ls(self._run("ls", remote_dir))

    def delete(self, remote: str) -> None:
        out = self._run("rm", remote)
        if "遇到错误" in out and not any(s in out for s in _MISSING):
            raise RuntimeError(f"BaiduPCS-Go rm {remote} failed: {out.strip()[-200:]}")

    def free_bytes(self) -> int | None:
        quota = parse_quota(self._run("quota"))
        return None if quota is None else quota[0] - quota[1]
