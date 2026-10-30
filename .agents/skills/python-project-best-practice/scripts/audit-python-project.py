#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["tyro>=1.0"]
# ///
"""Score an existing Python project against modern-project conventions.

Read-only: this never edits, formats, or installs anything. It reports what is
wrong, why it matters, and the command that fixes it, then orders the findings
into a migration plan you can apply one rung per pull request.

There is deliberately no --fix. Migrating a real project is a sequence of
reviewable changes (layout, then packaging, then gates), and a script that
did all of it at once would produce a diff nobody can review.

Examples:
    audit-python-project.py --help           # every flag, with defaults
    audit-python-project.py                  # audit the current directory
    audit-python-project.py ../legacy-repo
    audit-python-project.py --format json | jq '.checks[] | select(.status=="fail")'
    audit-python-project.py --fail-on warn   # stricter gate for CI

Output:
    JSON (default) or a text table on stdout; diagnostics on stderr.

Exit codes:
    0  nothing at or above the --fail-on threshold
    1  unexpected runtime error
    2  usage error
    3  the path is not a directory
    4  one or more checks are at or above the --fail-on threshold
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Literal

import tyro

Status = Literal["pass", "warn", "fail", "n/a"]
RANK = {"pass": 0, "n/a": 0, "warn": 1, "fail": 2}


@dataclasses.dataclass
class Check:
    id: str
    status: Status
    evidence: str
    fix: str


# Per https://devguide.python.org/versions/, Python 3.10 reached EOL in Oct 2026.
MIN_ACTIVE_PYTHON = (3, 11)
_VER_RE = re.compile(r"(\d+)\.(\d+)")


def _parse_ver_tuple(text: str) -> tuple[int, int] | None:
    match = _VER_RE.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


# Ordered migration rungs. Each names the check ids it resolves; a rung with
# nothing outstanding is dropped from the plan.
RUNGS: list[tuple[str, tuple[str, ...]]] = [
    ("Adopt uv as the single source of truth for the environment",
     ("pyproject-exists", "requires-python", "build-backend", "uv-lock",
      "python-version-pin", "dependency-bounds", "legacy-setup-py",
      "legacy-requirements-txt", "legacy-poetry", "legacy-conda")),
    ("Move the package under src/ so tests exercise the installed artifact",
     ("src-layout", "py-typed")),
    ("Split dev tooling out of published extras into [dependency-groups]",
     ("dependency-groups",)),
    ("Adopt ruff for both lint and format, and drop the overlapping formatter",
     ("ruff-configured", "black-conflict")),
    ("Add a type checker to the gate", ("type-checker",)),
    ("Establish the test gate", ("tests-dir", "pytest-config")),
    ("Put every operation behind a task runner, then run it in CI",
     ("task-runner", "ci-workflow", "ci-pinned-actions")),
    ("Expose a console script so the tool is installable", ("console-script",)),
    ("Fix logging layering: loguru at entry points, stdlib in library code",
     ("loguru-layering",)),
    ("Write the agent contract", ("agents-md", "agents-md-symlink")),
    ("Secrets hygiene", ("env-not-tracked", "gitignore-basics")),
]


def read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def git_tracked(root: Path, pattern: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", pattern],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def package_dirs(root: Path) -> list[Path]:
    src = root / "src"
    if src.is_dir():
        return [p for p in src.iterdir() if p.is_dir() and (p / "__init__.py").exists()]
    return [
        p for p in root.iterdir()
        if p.is_dir()
        and (p / "__init__.py").exists()
        and p.name not in {"tests", "test", "docs", "examples", "scripts", "notebooks"}
    ]


def audit(root: Path) -> list[Check]:
    pyproject_path = root / "pyproject.toml"
    data = read_toml(pyproject_path)
    project = data.get("project", {})
    tool = data.get("tool", {})
    checks: list[Check] = []
    add = lambda *a: checks.append(Check(*a))  # noqa: E731 - terse on purpose

    # --- packaging -----------------------------------------------------------
    if pyproject_path.exists():
        add("pyproject-exists", "pass", "pyproject.toml present", "")
    else:
        add("pyproject-exists", "fail", "no pyproject.toml",
            "uv init --package  (then move metadata out of setup.py/setup.cfg)")

    requires = project.get("requires-python")
    pin_file = root / ".python-version"
    pin_text = pin_file.read_text(encoding="utf-8", errors="replace").strip() if pin_file.exists() else ""
    pin_ver = _parse_ver_tuple(pin_text)
    req_ver = _parse_ver_tuple(requires) if isinstance(requires, str) else None

    if not requires:
        add("requires-python", "fail", "no [project] requires-python",
            'set requires-python = ">=3.11" in [project]')
    elif any(op in requires for op in ("==", "~=")):
        add("requires-python", "warn",
            f"requires-python = {requires!r} uses a narrow pin",
            'use an open floor (requires-python = ">=3.11"); .python-version pins '
            "your dev venv while requires-python defines who can install the package")
    elif req_ver and req_ver < MIN_ACTIVE_PYTHON:
        add("requires-python", "warn",
            f"requires-python = {requires!r} targets EOL Python (<3.11)",
            "Python <=3.10 is past End-of-Life (https://devguide.python.org/versions/); "
            'bump requires-python = ">=3.11", set [tool.ruff] target-version = "py311", '
            "and run `uv run ruff check --select UP --fix`")
    elif req_ver and pin_ver and req_ver >= (3, 12) and req_ver == pin_ver:
        add("requires-python", "warn",
            f"requires-python = {requires!r} matches .python-version ({pin_text})",
            'uv init sets requires-python to your local interpreter; lower it to ">=3.11" '
            "unless the code genuinely needs 3.12+ features so consumers on older active "
            "Pythons can install the package")
    else:
        add("requires-python", "pass", f"requires-python = {requires!r}", "")

    backend = data.get("build-system", {}).get("build-backend")
    modern = {"hatchling.build", "uv_build", "maturin", "flit_core.buildapi", "pdm.backend",
              "scikit_build_core.build", "setuptools.build_meta"}
    add("build-backend", "pass" if backend in modern else "fail",
        f"build-backend = {backend!r}" if backend else "no [build-system] table",
        "" if backend in modern else 'add [build-system] with hatchling (or maturin for PyO3)')

    add("uv-lock", "pass" if (root / "uv.lock").exists() else "fail",
        "uv.lock present" if (root / "uv.lock").exists() else "no uv.lock",
        "" if (root / "uv.lock").exists() else
        "uv lock  (commit it: it pins YOUR dev env, not your users')")

    if not pin_file.exists():
        add("python-version-pin", "warn", "no .python-version",
            "uv python pin 3.13  (or your preferred local 3.11+ interpreter to reuse uv cache)")
    elif pin_ver and pin_ver < MIN_ACTIVE_PYTHON:
        add("python-version-pin", "warn",
            f".python-version = {pin_text!r} is past End-of-Life",
            "Python <=3.10 reached EOL (https://devguide.python.org/versions/); "
            "upgrade with `uv python install 3.13 && uv python pin 3.13`")
    else:
        add("python-version-pin", "pass", f".python-version = {pin_text!r}", "")

    raw_deps = project.get("dependencies", [])
    tight_deps = [
        dep for dep in raw_deps
        if isinstance(dep, str) and any(op in dep for op in ("==", "~=", "<"))
    ]
    add("dependency-bounds", "warn" if tight_deps else "pass",
        f"tight bounds in [project.dependencies]: {', '.join(tight_deps[:4])}"
        if tight_deps else "[project.dependencies] uses open >= floors",
        "relax [project.dependencies] to `>=<lowest-compatible-version>` and remove `<`/`==`/`~=`; "
        "uv.lock already pins exact versions for dev and CI, while tight metadata bounds "
        "cause solver conflicts for downstream users" if tight_deps else "")

    for name, marker in (
        ("legacy-setup-py", "setup.py"),
        ("legacy-requirements-txt", "requirements.txt"),
        ("legacy-conda", "environment.yml"),
    ):
        found = (root / marker).exists()
        add(name, "warn" if found else "pass",
            f"{marker} present" if found else f"no {marker}",
            {
                "legacy-setup-py":
                    "move metadata into [project] in pyproject.toml, then delete setup.py",
                "legacy-requirements-txt":
                    "uv add each DIRECT dependency by hand; do NOT `uv add -r requirements.txt`, "
                    "it promotes transitive pins to direct dependencies",
                "legacy-conda":
                    "port to uv; keep conda only for non-Python system libraries uv cannot supply",
            }[name] if found else "")

    poetry = "poetry" in tool or (root / "poetry.lock").exists()
    add("legacy-poetry", "warn" if poetry else "pass",
        "[tool.poetry] or poetry.lock present" if poetry else "no poetry config",
        "uv migrates cleanly from a PEP 621 [project] table; convert [tool.poetry] first"
        if poetry else "")

    # --- layout --------------------------------------------------------------
    packages = package_dirs(root)
    in_src = bool(packages) and packages[0].parent.name == "src"
    add("src-layout", "pass" if in_src else "fail" if packages else "warn",
        f"package(s) at {', '.join(str(p.relative_to(root)) for p in packages)}"
        if packages else "no importable package found",
        "" if in_src else
        "git mv <pkg> src/<pkg>. A flat layout imports the source dir instead of the "
        "installed package, so missing __init__.py or package data passes tests and "
        "breaks users")

    if packages:
        typed = any((p / "py.typed").exists() for p in packages)
        add("py-typed", "pass" if typed else "warn",
            "py.typed marker present" if typed else "no py.typed marker",
            "" if typed else
            "touch src/<pkg>/py.typed - without it your annotations are invisible to "
            "consumers' type checkers")
    else:
        add("py-typed", "n/a", "no package to check", "")

    # --- dependency declaration ---------------------------------------------
    groups = data.get("dependency-groups", {})
    extras = project.get("optional-dependencies", {})
    devish = sorted(name for name in extras if name in {"dev", "test", "tests", "lint", "typing"})
    if groups:
        status, evidence = "pass", f"[dependency-groups]: {', '.join(sorted(groups))}"
    elif devish:
        status, evidence = "warn", f"dev tooling in extras: {', '.join(devish)}"
    else:
        status, evidence = "warn", "no dev dependencies declared"
    add("dependency-groups", status, evidence,
        "" if status == "pass" else
        "uv add --dev <pkg>. An extra is published to PyPI and installable by your users; "
        "a PEP 735 group is not")

    # --- quality gates -------------------------------------------------------
    ruff_cfg = "ruff" in tool or (root / "ruff.toml").exists() or (root / ".ruff.toml").exists()
    add("ruff-configured", "pass" if ruff_cfg else "fail",
        "[tool.ruff] configured" if ruff_cfg else "no ruff configuration",
        "" if ruff_cfg else 'uv add --dev ruff, then add [tool.ruff] with select = [...]')

    text = pyproject_path.read_text(encoding="utf-8") if pyproject_path.exists() else ""
    black = "black" in tool or re.search(r'"black[<>=~ ]', text) is not None
    add("black-conflict", "fail" if (black and ruff_cfg) else "pass",
        "black configured alongside ruff" if (black and ruff_cfg)
        else "no black/ruff formatter overlap",
        "drop black: `ruff format` is a black reimplementation, and running both makes "
        "them fight over magic trailing commas" if (black and ruff_cfg) else "")

    checker = next(
        (name for name in ("ty", "mypy", "pyright", "basedpyright")
         if name in tool or re.search(rf'"{name}[<>=~ ]', text)),
        None,
    )
    add("type-checker", "pass" if checker else "warn",
        f"{checker} configured" if checker else "no type checker found",
        "" if checker else "uv add --dev ty (pre-1.0: pin exactly) or mypy, and add it to the gate")

    tests = root / "tests"
    has_tests = tests.is_dir() and any(tests.rglob("test_*.py"))
    add("tests-dir", "pass" if has_tests else "fail",
        "tests/ contains test_*.py" if has_tests else "no tests/ with test_*.py",
        "" if has_tests else "mkdir tests && write one failing test first")

    pytest_cfg = "pytest" in tool or (root / "pytest.ini").exists() or (root / "tox.ini").exists()
    add("pytest-config", "pass" if pytest_cfg else "warn",
        "pytest configured" if pytest_cfg else "no pytest configuration",
        "" if pytest_cfg else 'add [tool.pytest.ini_options] with testpaths = ["tests"]')

    # --- surfaces ------------------------------------------------------------
    scripts = project.get("scripts", {})
    has_cli_module = any((p / "cli").exists() or (p / "cli.py").exists() for p in packages)
    if scripts:
        add("console-script", "pass", f"[project.scripts]: {', '.join(scripts)}", "")
    elif has_cli_module:
        add("console-script", "fail", "a cli module exists but [project.scripts] is empty",
            'add [project.scripts] name = "pkg.cli:main" - without it `uv tool install` '
            "gives the user no command")
    else:
        add("console-script", "warn", "no [project.scripts]",
            "expose a CLI: it is the surface an agent can actually exercise")

    runner = next(
        (name for name in ("Justfile", "justfile", ".justfile", "Makefile")
         if (root / name).exists()),
        None,
    ) or ("taskipy" if "taskipy" in tool else None)
    add("task-runner", "pass" if runner else "warn",
        f"{runner} present" if runner else "no task runner",
        "" if runner else
        "add a Justfile with setup/check/fmt/lint/types/test so `just --list` is the "
        "discoverable command surface for humans and agents")

    workflows = list((root / ".github" / "workflows").glob("*.y*ml"))
    add("ci-workflow", "pass" if workflows else "warn",
        f"{len(workflows)} workflow(s)" if workflows else "no GitHub Actions workflows",
        "" if workflows else "add .github/workflows/ci.yml running the same gate as `just check`")

    floating = []
    for workflow in workflows:
        for line in workflow.read_text(encoding="utf-8", errors="replace").splitlines():
            if match := re.search(r"uses:\s*([\w./-]+)@(\S+)", line):
                if match.group(2) in {"main", "master", "latest"}:
                    floating.append(f"{workflow.name}: {match.group(0).strip()}")
    add("ci-pinned-actions", "warn" if floating else "pass",
        "; ".join(floating) if floating else "no floating action refs",
        "pin actions to a release tag or commit SHA" if floating else "")

    # --- logging layering ----------------------------------------------------
    offenders = []
    for package in packages:
        for module in package.rglob("*.py"):
            rel = module.relative_to(root)
            if any(part in {"cli", "api", "__main__.py"} for part in rel.parts):
                continue
            if module.name in {"_log.py", "log.py", "logging_setup.py"}:
                continue
            body = module.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^\s*from loguru import|^\s*import loguru", body, re.M):
                offenders.append(str(rel))
    if not packages:
        add("loguru-layering", "n/a", "no package to check", "")
    else:
        add("loguru-layering", "warn" if offenders else "pass",
            f"loguru imported in library modules: {', '.join(offenders[:5])}"
            if offenders else "loguru confined to entry points",
            "library modules use logging.getLogger(__name__); only entry points call "
            "loguru's add()/remove(). A library that configures loguru hijacks the logging "
            "of every program that imports it" if offenders else "")

    # --- agent contract ------------------------------------------------------
    agents, claude = root / "AGENTS.md", root / "CLAUDE.md"
    add("agents-md", "pass" if (agents.exists() or claude.exists()) else "warn",
        "AGENTS.md or CLAUDE.md present" if (agents.exists() or claude.exists())
        else "neither AGENTS.md nor CLAUDE.md",
        "" if (agents.exists() or claude.exists()) else
        "write AGENTS.md: commands, layout, conventions, and a CLI reference block")

    if agents.exists() and claude.exists():
        linked = agents.is_symlink() or claude.is_symlink()
        add("agents-md-symlink", "pass" if linked else "warn",
            "one file, one symlink" if linked else "AGENTS.md and CLAUDE.md are both real files",
            "" if linked else
            "ln -sf AGENTS.md CLAUDE.md - two real files drift, and only one of them "
            "will be right")
    else:
        add("agents-md-symlink", "n/a", "only one of the two exists", "")

    # --- secrets -------------------------------------------------------------
    tracked_env = git_tracked(root, ".env")
    add("env-not-tracked", "fail" if tracked_env else "pass",
        ".env is tracked by git" if tracked_env else ".env not tracked",
        "git rm --cached .env, add it to .gitignore, ROTATE every credential it held, "
        "and only then consider rewriting history" if tracked_env else "")

    ignore = (root / ".gitignore").read_text(encoding="utf-8", errors="replace") \
        if (root / ".gitignore").exists() else ""
    missing = [entry for entry in (".venv", ".env") if entry not in ignore]
    add("gitignore-basics", "warn" if missing else "pass",
        f"not ignored: {', '.join(missing)}" if missing else ".venv and .env ignored",
        f"add {' and '.join(missing)} to .gitignore" if missing else "")

    return checks


def plan(checks: list[Check]) -> list[dict]:
    by_id = {check.id: check for check in checks}
    steps = []
    for title, ids in RUNGS:
        outstanding = [i for i in ids if i in by_id and by_id[i].status in {"warn", "fail"}]
        if outstanding:
            steps.append({"step": len(steps) + 1, "action": title, "resolves": outstanding})
    return steps


def render_table(checks: list[Check], steps: list[dict]) -> str:
    mark = {"pass": "ok  ", "warn": "WARN", "fail": "FAIL", "n/a": "-   "}
    width = max(len(check.id) for check in checks)
    lines = [f"{mark[c.status]}  {c.id.ljust(width)}  {c.evidence}" for c in checks]
    if steps:
        lines.append("")
        lines.append("Migration plan (one rung per pull request):")
        for step in steps:
            lines.append(f"  {step['step']}. {step['action']}")
            lines.append(f"     resolves: {', '.join(step['resolves'])}")
    return "\n".join(lines)


@dataclasses.dataclass(frozen=True)
class Args:
    """Score a Python project against modern-project conventions. Read-only."""

    path: tyro.conf.Positional[str] = "."
    """Project root to audit."""

    format: Literal["json", "table"] = "json"
    """json for machines, table for humans."""

    fail_on: Literal["fail", "warn", "never"] = "fail"
    """Lowest status that makes this exit non-zero."""


def main(args: Args) -> None:
    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        raise SystemExit(3)

    checks = audit(root)
    steps = plan(checks)
    counts = {status: sum(1 for c in checks if c.status == status)
              for status in ("pass", "warn", "fail", "n/a")}

    if args.format == "table":
        print(render_table(checks, steps))
    else:
        print(json.dumps({
            "path": str(root),
            "summary": counts,
            "checks": [dataclasses.asdict(check) for check in checks],
            "migration_plan": steps,
        }, indent=2))

    print(
        f"{counts['pass']} pass, {counts['warn']} warn, {counts['fail']} fail "
        f"({counts['n/a']} n/a)",
        file=sys.stderr,
    )
    threshold = {"fail": 2, "warn": 1, "never": 99}[args.fail_on]
    if any(RANK[check.status] >= threshold for check in checks):
        raise SystemExit(4)


if __name__ == "__main__":
    os.environ.setdefault("COLUMNS", "100")
    try:
        main(tyro.cli(Args, prog="audit-python-project.py", description=__doc__))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - top-level guard, message is the contract
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
