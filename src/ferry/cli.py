"""ferry command line. Inspect, transfer and supervise resumable jobs."""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from dataclasses import asdict

from . import __version__
from .core import AlreadyRunning, PreflightError, adapter_for, preflight
from .job import JobError, enumerate_units, load_job
from .receiver import receive
from .sender import send
from .sizes import human
from .status import side_status


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ferry", description=__doc__)
    p.add_argument("--version", action="version", version=f"ferry {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("init", help="interactively create a source-preserving job (offline)")
    c.add_argument("--output", help="explicit new job path; default: "
                   "$XDG_CONFIG_HOME/ferry/jobs/<name>.toml "
                   "(falls back to ~/.config/ferry/jobs/<name>.toml)")
    c.add_argument("--stdout", action="store_true", help="print TOML instead of writing a file; "
                   "still requires interactive stdin")

    def job_cmd(name: str, help_: str) -> argparse.ArgumentParser:
        c = sub.add_parser(name, help=help_)
        c.add_argument("job", help="job TOML file")
        c.add_argument("--json", action="store_true", help="print JSON")
        return c

    c = job_cmd("doctor", "check backend access and direct egress")
    c.add_argument("--side", choices=("sender", "receiver"), default="sender")
    c.add_argument("--allow-proxy", action="store_true")
    job_cmd("plan", "list source units and what is left to send")
    for name, help_ in (("send", "pack and push units"), ("receive", "pull and verify units")):
        c = job_cmd(name, help_)
        c.add_argument("--follow", action="store_true",
                       help="keep running and pick up new units forever")
        c.add_argument("--allow-proxy", action="store_true",
                       help="accept proxied egress for this run (recorded in the ledger)")
        c.add_argument("--poll", type=float, default=60, help="idle poll seconds")
        if name == "receive":
            c.add_argument("--once", action="store_true", help="single pass over the relay")
    c = job_cmd("inventory", "receiver: fingerprint data already held and publish it so the "
                "sender skips identical units")
    c.add_argument("--root", help="local tree laid out like the source "
                   "(default: receiver.dest when it has no {key})")
    c.add_argument("--no-upload", action="store_true", help="only print the fingerprints")
    c = job_cmd("service", "print or install a systemd user service / crontab entry")
    c.add_argument("--side", choices=("sender", "receiver"), required=True)
    c.add_argument("--kind", choices=("systemd", "cron"), default="systemd")
    c.add_argument("--schedule", default="0 * * * *", help="cron schedule (kind=cron)")
    c.add_argument("--poll", type=float, help="idle poll seconds passed to ferry")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="write and enable it")
    g.add_argument("--uninstall", action="store_true", help="disable and remove it")
    c = job_cmd("status", "progress, throughput and ETA from the ledgers")
    c.add_argument("--side", choices=("sender", "receiver"), default="sender")
    c.add_argument("--scan-source", action="store_true", help="count remaining units (sender)")
    c.add_argument("--remote", action="store_true", help="query relay free space and backlog")
    return p


def _print(args, data: dict, text: str) -> None:
    print(json.dumps(data, indent=1, default=str) if args.json else text)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            from .wizard import run
            return run(args.output, to_stdout=args.stdout)
        job = load_job(args.job)
        if args.command == "doctor":
            report = preflight(job, adapter_for(job, args.side), allow_proxy=args.allow_proxy)
            lines = [f"adapter: {report.adapter}  egress policy: {report.egress_policy}"]
            lines += [f"  route {r['host']} -> {r['address']} via {r['interface']}"
                      + (f"  !! {r['problem']}" if r["problem"] else "") for r in report.routes]
            lines += [f"  problem: {x}" for x in report.problems]
            shown = {f"{r['host']}: {r['problem']}" for r in report.routes if r["problem"]}
            lines += [f"  egress: {x}" for x in report.egress_findings if x not in shown]
            rates = {k: (human(v) + "/s" if v else "uncapped") for k, v in report.rates.items()}
            lines.append(f"  bandwidth: upload {rates.get('upload')}, "
                         f"download {rates.get('download')}")
            from .host import load_host_budget
            host = load_host_budget()
            if host.source:
                lines.append(f"  host budget ({host.source}): up {human(host.upload_rate)}/s, "
                             f"down {human(host.download_rate)}/s, "
                             f"{host.max_transfers or 'unlimited'} transfer(s) per direction")
            from .windows import wait_seconds
            for direction in ("upload", "download"):
                pair = [getattr(job.remote, f"{direction}_schedule"), host.schedule(direction)]
                if any(pair):
                    wait = wait_seconds(pair)
                    state = ("open now" if wait == 0 else "never opens (check windows)"
                             if wait is None else f"opens in {wait / 3600:.1f} h")
                    lines.append(f"  {direction} window: {state}")
            if report.proxy_env_stripped:
                lines.append("  proxy env (removed for child processes): "
                             + ", ".join(report.proxy_env_stripped))
            lines.append("OK" if report.ok else "NOT OK")
            _print(args, report.to_dict(), "\n".join(lines))
            return 0 if report.ok else 2
        if args.command == "plan":
            units = enumerate_units(job.units)
            data = {"units": [{"key": u.key, "members": list(u.members)} for u in units]}
            _print(args, data, "\n".join(f"{u.key}  {len(u.members)} member(s)" for u in units)
                   + f"\n{len(units)} units")
            return 0
        if args.command == "inventory":
            from .inventory import build_inventory, publish_inventory
            inv = build_inventory(job, args.root)
            if not args.no_upload:
                publish_inventory(job, inv)
            _print(args, inv if args.json else {}, f"{len(inv['keys'])} units fingerprinted "
                   f"under {inv['root']}" + ("" if args.no_upload else "; published to relay"))
            return 0
        if args.command == "service":
            from . import service as svc
            if args.uninstall:
                (svc.uninstall_systemd if args.kind == "systemd" else svc.uninstall_cron)(
                    job, args.side)
                print(f"removed {args.kind} entry for {job.name}/{args.side}")
                return 0
            if args.kind == "systemd":
                if args.install:
                    path = svc.install_systemd(job, args.side, poll=args.poll)
                    print(f"installed and started {path}")
                    if svc.linger_enabled() is False:
                        print("note: lingering is off; the service stops at logout and does not "
                              "start at boot. Enable with: loginctl enable-linger $USER",
                              file=sys.stderr)
                else:
                    print(svc.systemd_unit(job, args.side, poll=args.poll), end="")
                    print("note: surviving logout/reboot requires lingering; check your host's "
                          "policy before using loginctl enable-linger $USER", file=sys.stderr)
            else:
                if args.install:
                    print("installed: " + svc.install_cron(job, args.side, args.schedule,
                                                           poll=args.poll))
                else:
                    print(svc.cron_line(job, args.side, args.schedule, poll=args.poll))
            return 0
        if args.command == "status":
            st = side_status(job, args.side, scan_source=args.scan_source, remote=args.remote)
            text = (f"{st.side}: {st.done} units, {human(st.done_bytes)}, "
                    f"recent {st.recent_mb_s} MB/s, failures(24h) {st.failures_last_24h}\n"
                    + "\n".join(f"  {k}: {v}" for k, v in st.extra.items()))
            _print(args, st.to_dict(), text)
            return 0
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            if args.command == "send":
                result = send(job, follow=args.follow, allow_proxy=args.allow_proxy,
                              poll=args.poll, stop=stop)
            else:
                result = receive(job, once=args.once, follow=args.follow,
                                 allow_proxy=args.allow_proxy, poll=args.poll, stop=stop)
        except KeyboardInterrupt:
            stop.set()
            return 130
        _print(args, asdict(result), json.dumps(asdict(result)))
        return 1 if result.failed else 0
    except (JobError, PreflightError, AlreadyRunning, ValueError) as e:
        print(f"ferry: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
