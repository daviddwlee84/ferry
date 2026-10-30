"""Receiver-side inventory: fingerprints of data already present at the destination.

Published to ``<prefix>/_inventory.json``; the sender marks units whose source fingerprint
matches as ``seeded`` instead of sending them again. Only meaningful when the destination
mirrors the source layout (``receiver.extract`` into a fixed ``dest``).
"""
from __future__ import annotations

import dataclasses
import json

from .core import adapter_for
from .fingerprint import fingerprint
from .job import Job, JobError, _path, enumerate_units
from .protocol import inventory_path, utcnow


def build_inventory(job: Job, root: str | None = None) -> dict:
    if job.receiver is None:
        raise JobError("job has no [receiver] section")
    if root is None:
        if "{key" in job.receiver.dest:
            raise JobError("receiver.dest depends on the key; pass --root")
        root = job.receiver.dest
    spec = dataclasses.replace(job.units, root=_path(root))
    keys = {}
    for unit in enumerate_units(spec):
        fp = fingerprint(unit)
        if fp.files:
            keys[unit.key] = fp.digest
    return {"root": str(spec.root), "created_at": utcnow(), "keys": keys}


def publish_inventory(job: Job, inventory: dict) -> None:
    adapter = adapter_for(job, "receiver")
    adapter.put_text(json.dumps(inventory), inventory_path(job.remote.prefix),
                     job.state_dir / "scratch")
