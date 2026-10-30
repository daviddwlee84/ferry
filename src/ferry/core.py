"""Shared runtime: preflight (backend + egress), single-instance lock, logging."""
from __future__ import annotations

import fcntl
import logging
import os
import shutil
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .adapters import Adapter, make_adapter
from .egress import check_routes, proxy_vars_present
from .host import HostBudget, load_host_budget
from .job import Job
from .protocol import Ledger


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

    Every put/get then holds a host transfer slot (see ferry.host), so the total of all
    ferry transfers on this machine stays within ``host.toml``.
    """
    name, options = job.remote.for_side(side)
    adapter = make_adapter(name, options, log=job.state_dir / f"{side}.transfer.log")
    host = host if host is not None else load_host_budget()
    up, down = job.remote.rates_for_side(side)
    adapter.upload_rate = host.effective("upload", up)
    adapter.download_rate = host.effective("download", down)
    if host.max_transfers:
        _hold_slots(adapter, host)
    return adapter


def _hold_slots(adapter: Adapter, host: HostBudget) -> None:
    put, get = adapter.put, adapter.get
    log = logging.getLogger("ferry.host")

    def waiting(direction: str, slots: int) -> None:
        log.info("waiting for a host %s slot (%d in use by other ferry transfers)",
                 direction, slots)

    def limited_put(local: Path, remote: str) -> None:
        with host.slot("upload", on_wait=waiting):
            put(local, remote)

    def limited_get(remote: str, local: Path) -> None:
        with host.slot("download", on_wait=waiting):
            get(remote, local)

    adapter.put, adapter.get = limited_put, limited_get


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
