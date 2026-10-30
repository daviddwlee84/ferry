from __future__ import annotations

from pathlib import Path

from .base import Adapter


def make_adapter(name: str, options: dict, log: Path | None = None) -> Adapter:
    if name == "local":
        from .local import LocalAdapter
        return LocalAdapter(options, log)
    if name == "baidupcs":
        from .baidupcs import BaiduPCSAdapter
        return BaiduPCSAdapter(options, log)
    if name == "rclone":
        from .rclone import RcloneAdapter
        return RcloneAdapter(options, log)
    if name == "rsync":
        from .rsync import RsyncAdapter
        return RsyncAdapter(options, log)
    if name == "ghrelease":
        from .ghrelease import GhReleaseAdapter
        return GhReleaseAdapter(options, log)
    raise ValueError(f"unknown adapter {name!r} (local, baidupcs, rclone, ghrelease, rsync)")


__all__ = ["Adapter", "make_adapter"]
