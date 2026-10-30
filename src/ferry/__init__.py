"""Resumable bulk transfer between hosts that cannot reach each other, via cloud storage."""
from __future__ import annotations

__version__ = "0.1.0"

from .core import Preflight, adapter_for, preflight  # noqa: E402
from .job import Job, enumerate_units, load_job  # noqa: E402
from .receiver import ReceiveResult, receive  # noqa: E402
from .sender import SendResult, send  # noqa: E402
from .status import SideStatus, side_status  # noqa: E402

__all__ = [
    "Job", "Preflight", "ReceiveResult", "SendResult", "SideStatus", "__version__",
    "adapter_for", "enumerate_units", "load_job", "preflight", "receive", "send", "side_status",
]
