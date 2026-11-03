"""Resumable bulk transfer between hosts that cannot reach each other, via cloud storage."""
from __future__ import annotations

__version__ = "0.1.0"

from .core import Preflight, adapter_for, preflight
from .job import Job, enumerate_units, load_job
from .receiver import ReceiveResult, receive
from .sender import SendResult, send
from .status import SideStatus, side_status

__all__ = [
    "Job", "Preflight", "ReceiveResult", "SendResult", "SideStatus", "__version__",
    "adapter_for", "enumerate_units", "load_job", "preflight", "receive", "send", "side_status",
]
