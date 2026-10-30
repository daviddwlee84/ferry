"""Offline, source-preserving job setup. No backend or supervisor is contacted."""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from . import service
from .job import _KEY, Job, job_from_dict

DOCS = "https://daviddwlee84.github.io/ferry/"


class Prompts:
    def __init__(self, stdin: TextIO, stderr: TextIO):
        self.stdin = stdin
        self.stderr = stderr

    def say(self, text: str) -> None:
        print(text, file=self.stderr)

    def ask(self, label: str, default: str | None = None,
            validate: Callable[[str], str] | None = None) -> str:
        while True:
            suffix = f" [{default}]" if default is not None else ""
            print(f"{label}{suffix}: ", end="", file=self.stderr, flush=True)
            line = self.stdin.readline()
            if not line:
                raise EOFError
            value = line.strip() or default
            try:
                if value is None:
                    raise ValueError("a value is required")
                if any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise ValueError("use a single line without control characters")
                return validate(value) if validate else value
            except ValueError as e:
                self.say(f"Invalid input: {e}")

    def choice(self, label: str, options: tuple[tuple[str, str], ...], default: str) -> str:
        self.say(label)
        for i, (key, description) in enumerate(options, 1):
            self.say(f"  {i}. {key}: {description}")
        keys = [key for key, _ in options]

        def select(value: str) -> str:
            value = value.lower()
            if value in keys:
                return value
            if value.isdecimal() and 1 <= int(value) <= len(keys):
                return keys[int(value) - 1]
            raise ValueError("choose a number or one of " + ", ".join(keys))

        return self.ask("Choice", default, select)

    def confirm(self, label: str, *, default: bool = False) -> bool:
        def yes_no(value: str) -> str:
            if value.lower() in ("y", "yes"):
                return "yes"
            if value.lower() in ("n", "no"):
                return "no"
            raise ValueError("answer yes or no")

        return self.ask(label, "yes" if default else "no", yes_no) == "yes"


def _name(value: str) -> str:
    if not _KEY.fullmatch(value):
        raise ValueError("use 1–128 letters, digits, dots, underscores or hyphens; "
                         "start with a letter or digit")
    return value


def _host_path(value: str) -> str:
    if not (value.startswith("/") or value.startswith("~/")):
        raise ValueError("use an absolute path or ~/path on the host that will use it")
    return value


def _absolute_path(value: str) -> str:
    if not value.startswith("/"):
        raise ValueError("use an absolute path on that host")
    return value


def _relative_pattern(value: str) -> str:
    if value.startswith("/") or ".." in value.split("/"):
        raise ValueError("use a pattern relative to the source root, without '..'")
    return value


def _member(value: str) -> str:
    _relative_pattern(value)
    if "{" in value.replace("{key}", "") or "}" in value.replace("{key}", ""):
        raise ValueError("member templates support {key}, e.g. data/{key}")
    return value


def _destination(value: str) -> str:
    _host_path(value)
    literal = re.sub(r"\{key(?:\[-?\d*:-?\d*\])?\}", "", value)
    if "{" in literal or "}" in literal:
        raise ValueError("destination templates support {key} and slices like {key[0:4]}")
    return value


def _binary(value: str) -> str:
    if value.startswith("-") or ("/" in value and not value.startswith("/")):
        raise ValueError("use an executable name on PATH or an absolute executable path")
    return value


def _rclone_remote(value: str) -> str:
    name, colon, _ = value.partition(":")
    if not colon or not re.fullmatch(r"[\w .-]+", name):
        raise ValueError("use a configured remote, e.g. gdrive: or s3:bucket/relay; "
                         "do not enter credentials or on-the-fly remote definitions")
    return value


def _repo(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) or value.startswith("-"):
        raise ValueError("use owner/repository, not a URL or access token")
    return value


def _ssh_host(value: str) -> str:
    if value.startswith("-") or any(c.isspace() for c in value):
        raise ValueError("use an SSH host alias or user@host, without command-line options")
    return value


def _prefix(value: str) -> str:
    if not value.startswith("/") or not value.strip("/") or ".." in value.split("/"):
        raise ValueError("use a dedicated absolute relay prefix, e.g. /ferry/my-job")
    return value.rstrip("/")


def _schedule(value: str) -> str:
    if len(value.split()) != 5 or not re.fullmatch(r"[A-Za-z0-9*/,\- ]+", value):
        raise ValueError("use five cron fields, e.g. '0 2 * * *' (without quotes)")
    return value


def collect(p: Prompts, name: str) -> dict:
    root = p.ask("Source root (sender host)", validate=_host_path)
    state = p.ask("State directory (local to each host)", f"~/.local/state/ferry/{name}",
                  _host_path)
    stage = p.ask("Staging directory (sender host)", f"{state.rstrip('/')}/stage", _host_path)
    units = {"root": root}
    selection = p.choice("How should paths be grouped into units?", (
        ("glob", "each matching file or directory is one unit"),
        ("keys", "collect several paths per key, e.g. a date")), "glob")
    if selection == "glob":
        units["glob"] = p.ask("Source-relative pattern (e.g. exports/*.parquet)", "*",
                              _relative_pattern)
    else:
        units["keys_from"] = p.ask("Key discovery pattern (file stem or directory name)",
                                   "_manifests/*.json", _relative_pattern)
        p.say("Enter one relative member template at a time, e.g. data/{key}.\n"
              "Missing members are omitted; review with ferry plan before sending.")
        members = [p.ask("Member template", validate=_member)]
        while value := p.ask("Another member template (blank finishes)", "", _member):
            members.append(value)
        units["members"] = members
    units["pack"] = p.choice("How should units be packed?", (
        ("tar", "archive a file, directory, or group (supports empty member files)"),
        ("none", "send one non-empty regular file per unit, unchanged")), "tar")
    dest = p.ask("Destination (receiver host; optional {key} or {key[0:4]})",
                 validate=_destination)
    extract = False
    if units["pack"] == "tar":
        p.say("Extraction replaces affected destination paths, including removing stale files.\n"
              "Leave it off to retain tar archives; use a dedicated destination either way.")
        extract = p.confirm("Extract archives at the destination?")
    adapter = p.choice("Which relay backend? Authentication must be configured separately.", (
        ("local", "shared/local directory; no backend CLI"),
        ("baidupcs", "Baidu Pan via authenticated BaiduPCS-Go"),
        ("rclone", "an existing configured rclone remote"),
        ("ghrelease", "GitHub draft release via authenticated gh"),
        ("rsync", "rsync landing directory, optionally over noninteractive SSH")), "local")
    options: dict = {}
    sides: dict = {}
    if adapter == "local":
        p.say("Both hosts must see the same relay contents (adjust root per host if needed).")
        options["root"] = p.ask("Relay root", validate=_host_path)
    elif adapter == "baidupcs":
        options["binary"] = p.ask("BaiduPCS-Go executable", "BaiduPCS-Go", _binary)
    elif adapter == "rclone":
        options["remote"] = p.ask("Configured remote (e.g. gdrive:)", validate=_rclone_remote)
        p.say("If endpoint inference fails, set remote.options.endpoints before running doctor.")
    elif adapter == "ghrelease":
        options["repo"] = p.ask("GitHub repository (owner/repository)", validate=_repo)
        options["tag"] = p.ask("Dedicated draft release tag", f"ferry-{name}")
        p.say("Ferry creates a missing draft during transfers and refuses published releases.")
    else:
        options["host"] = p.ask("SSH host (blank for a local landing directory)", "", _ssh_host)
        options["root"] = p.ask("Absolute landing root (on the target host)",
                                validate=_absolute_path)
        if options["host"] and p.confirm("Will the receiver run on that host and read locally?",
                                          default=True):
            sides["receiver"] = {"adapter": "local", "options": {"root": options["root"]}}
    remote = {"adapter": adapter, "prefix": p.ask("Dedicated relay prefix", f"/ferry/{name}",
                                                _prefix),
              "egress": "direct", "options": options}
    if sides:
        remote["sides"] = sides
    return {"name": name, "state_dir": state, "units": units,
            "sender": {"stage_dir": stage, "delete_source": "never"},
            "receiver": {"dest": dest, "extract": extract}, "remote": remote}


def _toml_value(value: str | bool | list) -> str:
    if isinstance(value, str):
        # JSON's Unicode scalar strings also work in TOML, except literal DEL.
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ValueError(f"unsupported wizard value: {type(value).__name__}")


def render(data: dict) -> str:
    """Render the wizard's fixed schema, not arbitrary TOML keys or value types."""
    lines = ["# Generated by ferry init; authentication stays in your backend tools."]

    def table(values: dict, prefix: str = "") -> None:
        if prefix:
            lines.extend(("", f"[{prefix}]"))
        for key, value in values.items():
            if not isinstance(value, dict):
                lines.append(f"{key} = {_toml_value(value)}")
        for key, value in values.items():
            if isinstance(value, dict):
                table(value, f"{prefix}.{key}" if prefix else key)

    table(data)
    return "\n".join(lines) + "\n"


def guidance(p: Prompts, job: Job) -> None:
    side = p.choice("Which side will run on this host?", (
        ("sender", "read, pack and upload source units"),
        ("receiver", "download and verify units")), "sender")
    method = p.choice("How will this side run? Nothing will be installed or started.", (
        ("manual", "print the command to run yourself"),
        ("systemd", "preview a Linux systemd user service"),
        ("cron", "preview a periodic crontab entry"),
        ("pueue", "show queue guidance; no task submission")), "manual")
    p.say("\nAfter saving, inspect the job and backend access explicitly:")
    p.say(shlex.join(["ferry", "doctor", str(job.path), "--side", side]))
    p.say("Doctor can contact the backend and, with rsync, create a landing directory.")
    if side == "sender":
        p.say(shlex.join(["ferry", "plan", str(job.path)]))
    if method in ("systemd", "cron"):
        args = ["ferry", "service", str(job.path), "--side", side, "--kind", method]
        if method == "systemd":
            p.say("\nSystemd preview (Linux; verify backend PATH/authentication and linger):")
            p.say(service.systemd_unit(job, side, poll=None))
        else:
            schedule = p.ask("Cron schedule (five fields)", "0 * * * *", _schedule)
            p.say("\nCrontab preview (single pass, not a guaranteed delivery deadline):")
            p.say(service.cron_line(job, side, schedule, poll=None))
            args += ["--schedule", schedule]
        p.say("Review first. This separate command installs the entry and may start transfers:")
        p.say(shlex.join([*args, "--install"]))
        p.say("To remove that entry later:")
        p.say(shlex.join([*args, "--uninstall"]))
    elif method == "pueue":
        p.say("\nUse a separately supervised receive --follow while queueing non-follow send.\n"
              "Do not queue a receiver after a sender that needs ACKs or relay space, or\n"
              "put both into a shared one-slot queue. Do not queue --follow in a finite chain.\n"
              "Do not let a service and pueue own the same job/side simultaneously.")
        if side == "sender":
            p.say("Queue this command on the sender host using the guide's labeled group setup:")
            p.say(shlex.join(service.command(job, side, follow=False, poll=None)))
        else:
            p.say("Keep the receiver running independently under a service manager:")
            p.say(shlex.join(service.command(job, side, follow=True, poll=None)))
        p.say(f"Guide: {DOCS}pueue/")
    else:
        p.say("\nRun explicitly when ready (receiver --once only scans once):")
        p.say(shlex.join(service.command(job, side, follow=False, poll=None)))
    p.say(f"Advanced limits, read windows and policies: {DOCS}configuration/\n"
          f"Supervision, resume and delivery checks: {DOCS}operations/")


def _write_new(path: Path, text: str) -> None:
    created = None
    try:
        with path.open("x", encoding="utf-8") as f:
            created = os.fstat(f.fileno())
            f.write(text)
    except BaseException:
        if created is not None:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    path.unlink()
            except OSError:
                pass
        raise


def _output_path(value: str) -> Path:
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("output must be a non-empty path without control characters")
    return Path(value).expanduser().absolute()


def default_output(name: str) -> Path:
    """One job per file; relative XDG_CONFIG_HOME values are invalid per XDG."""
    _name(name)
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".config"
    return _output_path(str(root / "ferry" / "jobs" / f"{name}.toml"))


def _check_output(path: Path, *, managed: bool) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite {path}; choose a new job name or --output path")
    if not managed and not path.parent.is_dir():
        raise ValueError(f"output parent directory does not exist: {path.parent}")


def run(output: str | None = None, *, to_stdout: bool = False,
        stdin: TextIO | None = None, stdout: TextIO | None = None,
        stderr: TextIO | None = None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    p = Prompts(stdin, stderr)
    if not stdin.isatty():
        p.say(f"ferry init: interactive stdin is required; use a terminal or the TOML examples\n"
              f"at {DOCS}getting-started/. --stdout still requires interactive answers.")
        return 2
    try:
        path = _output_path(output) if output is not None else None
        if path is not None and not to_stdout:
            _check_output(path, managed=False)
        p.say("Create a source-preserving job. This does not contact backends or start transfers.\n"
              "Enter paths without shell quotes. Source paths belong to the sender; destination\n"
              "paths belong to the receiver. ~/ is expanded on the host using each path.\n"
              "Use separate, persistent state/staging and dedicated relay/destination directories.")
        name = p.ask("Job name (unique for each sync job)", "my-job", _name)
        if path is None:
            path = default_output(name)
            if not to_stdout:
                _check_output(path, managed=True)
        p.say(f"Job file: {path}")
        data = collect(p, name)
        text = render(data)
        job = job_from_dict(tomllib.loads(text), path)
        p.say("\nConfiguration preview:\n" + text)
        if to_stdout:
            p.say(f"No file will be written. Save the TOML at {path} before using these commands.")
        guidance(p, job)
        action = "Print this configuration to stdout?" if to_stdout else f"Save to {path}?"
        if not p.confirm(action):
            p.say("Cancelled; no configuration written.")
            return 0
        if to_stdout:
            stdout.write(text)
        else:
            if output is None:
                path.parent.mkdir(parents=True, exist_ok=True)
            _write_new(path, text)
            p.say(f"Saved {path}. No transfer or service was started.")
        return 0
    except EOFError:
        p.say("\nferry init: input ended; setup cancelled.")
        return 2
    except KeyboardInterrupt:
        p.say("\nferry init: interrupted; setup cancelled.")
        return 130
    except (OSError, ValueError) as e:
        p.say(f"ferry init: {e}")
        return 2
