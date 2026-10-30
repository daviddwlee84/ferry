"""Generate (and optionally install) a supervisor entry for a job side.

Two kinds:

* ``systemd`` (default on Linux): a *user* service running ``ferry send|receive --follow``
  with ``Restart=on-failure``. Survives logout/reboot only with lingering enabled
  (``loginctl enable-linger $USER``); ``ferry service`` reports the current state.
* ``cron``: a one-shot run on a schedule (``send`` without ``--follow``, ``receive
  --once``). Overlapping runs are harmless: the per-job lock makes the second exit.

Without ``--install`` the unit / crontab line is only printed, so it can be reviewed or
managed by other tooling (ansible, chezmoi, ...).
"""
from __future__ import annotations

import getpass
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .job import Job

UNIT_DIR = Path("~/.config/systemd/user").expanduser()
CRON_TAG = "# ferry:"


def _ferry_argv() -> list[str]:
    exe = shutil.which("ferry")
    return [exe] if exe else [sys.executable, "-m", "ferry.cli"]


def command(job: Job, side: str, *, follow: bool, poll: float | None) -> list[str]:
    if job.path is None:
        raise ValueError("job must be loaded from a file to build a service")
    verb = "send" if side == "sender" else "receive"
    argv = [*_ferry_argv(), verb, str(job.path.resolve())]
    if follow:
        argv.append("--follow")
    elif side == "receiver":
        argv.append("--once")
    if poll is not None:
        argv += ["--poll", f"{poll:g}"]
    return argv


def unit_name(job: Job, side: str) -> str:
    return f"ferry-{job.name}-{side}.service"


def systemd_unit(job: Job, side: str, *, poll: float | None) -> str:
    argv = command(job, side, follow=True, poll=poll)
    return f"""[Unit]
Description=ferry {side} for job {job.name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={shlex.join(argv)}
Restart=on-failure
RestartSec=60
# ferry strips proxy variables for backend tools itself; keep the unit environment minimal.
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def cron_line(job: Job, side: str, schedule: str, *, poll: float | None) -> str:
    argv = command(job, side, follow=False, poll=poll)
    return f"{schedule} {shlex.join(argv)} >/dev/null 2>&1 {CRON_TAG}{job.name}:{side}"


def linger_enabled() -> bool | None:
    try:
        out = subprocess.run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.strip().endswith("=yes") if out.strip() else None


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True)


def install_systemd(job: Job, side: str, *, poll: float | None) -> Path:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    path = UNIT_DIR / unit_name(job, side)
    path.write_text(systemd_unit(job, side, poll=poll))
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", path.name)
    return path


def uninstall_systemd(job: Job, side: str) -> None:
    path = UNIT_DIR / unit_name(job, side)
    subprocess.run(["systemctl", "--user", "disable", "--now", path.name], check=False)
    path.unlink(missing_ok=True)
    _systemctl("daemon-reload")


def _crontab() -> list[str]:
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return out.stdout.splitlines() if out.returncode == 0 else []


def _write_crontab(lines: list[str]) -> None:
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


def install_cron(job: Job, side: str, schedule: str, *, poll: float | None) -> str:
    tag = f"{CRON_TAG}{job.name}:{side}"
    line = cron_line(job, side, schedule, poll=poll)
    _write_crontab([x for x in _crontab() if not x.endswith(tag)] + [line])
    return line


def uninstall_cron(job: Job, side: str) -> None:
    tag = f"{CRON_TAG}{job.name}:{side}"
    _write_crontab([x for x in _crontab() if not x.endswith(tag)])
