"""Shared runtime: preflight (backend + egress), single-instance lock, logging."""
from __future__ import annotations

import fcntl
import logging
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .adapters import Adapter, make_adapter
from .egress import check_routes, proxy_vars_present
from .host import HostBudget, load_host_budget
from .job import Job
from .proc import transfer_deadline
from .protocol import Ledger
from .windows import seconds_until_close, wait_seconds


class PreflightError(RuntimeError):
    pass


class AlreadyRunning(RuntimeError):
    pass


@dataclass
class Preflight:
    adapter: str
    egress_policy: str
    problems: list[str] = field(default_factory=list)  # always fatal
    egress_findings: list[str] = field(default_factory=list)  # fatal under "direct"
    routes: list[dict] = field(default_factory=list)
    proxy_env_stripped: list[str] = field(default_factory=list)
    rates: dict = field(default_factory=dict)  # effective bytes/s after host budget

    @property
    def ok(self) -> bool:
        return not self.problems and (self.egress_policy == "any" or not self.egress_findings)

    def to_dict(self) -> dict:
        return {**asdict(self), "ok": self.ok}


def adapter_for(job: Job, side: str, host: HostBudget | None = None) -> Adapter:
    """Build the side's adapter with the job caps tightened by the host-wide budget.

    Every put/get then waits for its transfer window (job and host schedules) and holds
    a host transfer slot (see ferry.host), so all ferry transfers on this machine stay
    within ``host.toml`` and only run when allowed.
    """
    name, options = job.remote.for_side(side)
    adapter = make_adapter(name, options, log=job.state_dir / f"{side}.transfer.log")
    host = host if host is not None else load_host_budget()
    up, down = job.remote.rates_for_side(side)
    adapter.upload_rate = host.effective("upload", up)
    adapter.download_rate = host.effective("download", down)
    schedules = {
        "upload": [job.remote.upload_schedule, host.schedule("upload")],
        "download": [job.remote.download_schedule, host.schedule("download")],
    }
    if (host.max_transfers or host.upload_daily or host.download_daily
            or any(s for pair in schedules.values() for s in pair)):
        stop = job.remote.stop_at_window_end and host.stop_at_window_end
        _gate(adapter, host, schedules, stop_at_window_end=stop)
    return adapter


def wait_for_window(direction: str, schedules: list, *, sleep=None,
                    max_nap: float = 300) -> None:
    """Block until every schedule for `direction` is open (re-checked every few minutes)."""
    sleep = sleep or time.sleep
    log = logging.getLogger("ferry.host")
    announced = False
    while True:
        wait = wait_seconds(schedules)
        if wait == 0:
            return
        if wait is None:
            raise PreflightError(f"{direction} schedules never open within 8 days; "
                                 "check *_allowed / *_blocked windows")
        if not announced:
            log.info("%s outside its transfer window; next opening in %.0f min",
                     direction, wait / 60)
            announced = True
        sleep(min(wait, max_nap))


def _gate(adapter: Adapter, host: HostBudget, schedules: dict, *,
          stop_at_window_end: bool = True) -> None:
    put, get = adapter.put, adapter.get
    log = logging.getLogger("ferry.host")

    def waiting(direction: str, slots: int) -> None:
        log.info("waiting for a host %s slot (%d in use by other ferry transfers)",
                 direction, slots)

    def limit(direction: str):
        # Stop the backend when the window closes; the part is retried in the next window.
        close = seconds_until_close(schedules[direction]) if stop_at_window_end else None
        return transfer_deadline(close)

    def admit(direction: str) -> None:
        """Wait for the transfer window and for room in today's quota."""
        announced = False
        while True:
            wait_for_window(direction, schedules[direction])
            wait = host.quota_wait(direction)
            if wait == 0:
                return
            if not announced:
                log.info("daily %s quota used up; resuming in %.1f h", direction, wait / 3600)
                announced = True
            time.sleep(min(wait, 300))

    def gated_put(local: Path, remote: str) -> None:
        admit("upload")
        with host.slot("upload", on_wait=waiting), limit("upload"):
            put(local, remote)
        host.record("upload", local.stat().st_size if local.exists() else 0)

    def gated_get(remote: str, local: Path) -> None:
        admit("download")
        with host.slot("download", on_wait=waiting), limit("download"):
            get(remote, local)
        host.record("download", local.stat().st_size if local.exists() else 0)

    adapter.put, adapter.get = gated_put, gated_get


def preflight(job: Job, adapter: Adapter, *, allow_proxy: bool = False, routes=None) -> Preflight:
    policy = "any" if allow_proxy else job.remote.egress
    report = Preflight(adapter=adapter.name, egress_policy=policy,
                       proxy_env_stripped=proxy_vars_present(),
                       rates={"upload": adapter.upload_rate, "download": adapter.download_rate})
    report.problems.extend(adapter.check())
    report.problems.extend(adapter.rate_problems())
    report.egress_findings.extend(adapter.proxy_settings())
    if routes is None:
        def routes(hosts):
            return check_routes(hosts, trusted=job.remote.trusted_interfaces)
    for finding in routes(adapter.endpoints):
        report.routes.append(asdict(finding))
        if finding.problem:
            report.egress_findings.append(f"{finding.host}: {finding.problem}")
    return report


def require(report: Preflight, log: logging.Logger) -> None:
    for item in report.egress_findings:
        log.warning("egress: %s", item)
    if report.proxy_env_stripped:
        log.info("proxy variables removed for child processes: %s",
                 ", ".join(report.proxy_env_stripped))
    if not report.ok:
        reasons = report.problems + (
            report.egress_findings if report.egress_policy == "direct" else [])
        raise PreflightError("preflight failed: " + "; ".join(reasons) + (
            "\n(egress findings can be accepted with remote.egress = \"any\" or --allow-proxy)"
            if report.egress_findings and report.egress_policy == "direct" else ""))


@contextmanager
def instance_lock(job: Job, side: str):
    """One sender and one receiver per job and host; a second instance fails fast."""
    job.state_dir.mkdir(parents=True, exist_ok=True)
    path = job.state_dir / f"{side}.lock"
    fh = path.open("a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        holder = fh.read().strip() or "?"
        fh.close()
        raise AlreadyRunning(f"another ferry {side} for job {job.name} is running "
                             f"(pid {holder}, lock {path})") from None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def logger(job: Job, side: str) -> logging.Logger:
    log = logging.getLogger(f"ferry.{job.name}.{side}")
    if not log.handlers:
        job.state_dir.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
        for handler in (logging.FileHandler(job.state_dir / f"{side}.log"),
                        logging.StreamHandler()):
            handler.setFormatter(fmt)
            log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def ledger(job: Job, side: str) -> Ledger:
    return Ledger(job.state_dir / f"{side}.jsonl")


def remove_inside(root: Path, rel: str) -> None:
    """Delete root/rel, refusing anything that resolves outside root."""
    target = (root / rel)
    resolved = target.resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise PreflightError(f"refusing to delete {target}: outside source root")
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)
