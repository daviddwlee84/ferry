---
name: python-project-best-practice
description: 'Modern Python project conventions for the agentic-coding era: uv + src layout, Tyro CLIs with shell completion, marimo notebooks that run as scripts, loguru, and ruff/type/pytest gates behind a Justfile, plus an AGENTS.md contract agents can drive. Use when starting a new Python project or package, scaffolding a pyproject.toml, choosing a CLI/test/lint/logging stack, making a tool installable with `uv tool install`, or modernizing a legacy setup.py / requirements.txt / poetry / conda repo.'
---

# Python Project Best Practice

An opinionated baseline for Python projects that both people and agents work
in. Two entry points: scaffold a new project (Workflow A), or score and
modernize an existing one (Workflow B). The same conventions drive both.

The bias throughout: **every operation has one canonical, discoverable,
non-interactive command**. That is what makes a project workable by an agent,
and it happens to be what makes it pleasant for humans.

## When to use

- "Start a new Python project / package / CLI tool"
- "Set up pyproject.toml", "which should I use, uv or poetry / pip-tools?"
- "How do I make this installable so someone can just run it?"
- "Modernize this repo" — setup.py, requirements.txt, poetry, conda, flat layout
- Choosing between ruff/black, mypy/ty, extras/dependency-groups, just/taskipy
- Adding a CLI, notebooks, an HTTP surface, or a Rust extension to a Python repo

## When NOT to use

These skills own their territory. Delegate rather than restate:

| Topic | Skill |
|---|---|
| `--help` / `--dry-run` / `--print-config` / exit-code design and verification | `verifiable-surfaces` |
| marimo dual-mode notebooks, sweeps, tracking | `marimo-batch-mlflow`; format rules in `marimo-notebook`; widgets in `anywidget` |
| Production FastAPI internals | `fastapi-ai-patterns`, `fastapi-ai-scaffold` |
| Docs site, GitHub Pages, llms.txt | `mkdocs-site-bootstrap` |
| Releasing a compiled Go/Rust binary | `cli-release-distribution` (Python distribution stays here) |
| pre-commit setup, secret remediation | `agent-history-hygiene` |
| TDD discipline itself | `engineering-fundamentals/tdd` |
| Building an MCP server | `mcp-builder` (read `references/api-and-services.md` first — usually you want a subcommand) |

Also not for: a one-file script (use a PEP 723 header and stop), or a
maintenance-only repo where a migration buys nothing.

## Authoritative sources

Fetch the page rather than guessing — this toolchain moves fast, and several
of these projects change CLI surface between minor versions.

| Thing | Where |
|---|---|
| uv | <https://docs.astral.sh/uv/> · CLI reference <https://docs.astral.sh/uv/reference/cli/> |
| ruff rules | <https://docs.astral.sh/ruff/rules/> |
| ty | <https://github.com/astral-sh/ty> (0.0.x — check the version before quoting behavior) |
| Tyro | <https://brentyi.github.io/tyro/> · completion <https://brentyi.github.io/tyro/tab_completion/> |
| loguru | <https://loguru.readthedocs.io/> |
| pydantic-settings | <https://docs.pydantic.dev/latest/concepts/pydantic_settings/> |
| PyO3 / maturin | <https://pyo3.rs/> · <https://www.maturin.rs/> |
| Python versions / EOL | <https://devguide.python.org/versions/> (3.10 EOL Oct 2026; default floor `>=3.11`) |
| Packaging specs | <https://packaging.python.org/> · PEP 735 (dependency groups), PEP 723 (script deps) |

## The canonical layout

```
pyproject.toml          [project] + [dependency-groups] + tool config. One file.
uv.lock                 committed. Pins YOUR dev env, never your users'.
.python-version         dev interpreter (uv python pin). Not the same as requires-python.
Justfile                every supported operation. `just --list` is the surface.
AGENTS.md               the agent contract. CLAUDE.md is a symlink to it.
README.md               getting started for humans: uv sync, just check.
.envrc / .env.example   direnv activation; the tracked contract for .env (which is ignored).

src/my_tool/            src layout: tests import the INSTALLED package
  core.py               domain logic. stdlib logging. no side effects at import.
  _log.py               loguru setup. called by entry points only.
  settings.py           pydantic-settings: defaults < .env < environment.
  cli/                  one frozen dataclass per subcommand; run() -> exit code
  py.typed              without this your annotations are invisible to consumers
tests/                  mirrors src/ module for module
scripts/                repo helpers, NOT importable from the package
notebooks/              marimo notebooks. import the package; nothing imports them.
.agents/skills/my-tool/ the package's own skill, shipped with the code
.github/workflows/ci.yml  runs the same gate as `just check`
```

Two arrows to keep straight: `notebooks/` and `scripts/` may import the
package; the package imports neither.

## Decision: which profile?

Start at the smallest row that fits and add later — profiles are additive, so
"agentic later" costs nothing now.

| Profile | Use when | Adds | Read |
|---|---|---|---|
| `minimal` | a library nobody runs from a shell yet | package, tests, Justfile, CI, AGENTS.md | `uv-and-pyproject`, `quality-gates` |
| `cli` **(default)** | almost everything | Tyro CLI, loguru, pydantic-settings, `[project.scripts]` | + `tyro-cli`, `logging-and-config` |
| `lib` | others will import it | publishing metadata, `py.typed` | + `uv-and-pyproject` §Distribution |
| `api` | it serves HTTP | FastAPI app, `/docs`, `/openapi.json` | + `api-and-services` |
| `ml` | experiments and notebooks | marimo notebook that is also a CLI | + `notebooks-and-widgets` |
| `rust` | a profiled, CPU-bound hot loop | maturin backend, PyO3 crate, `.pyi` stubs | + `rust-pyo3` |

Companion skills to recommend, by profile, are in
[`references/agent-interface.md`](references/agent-interface.md) and in the
scaffolder's `recommended_skills[]`.

## Workflow A — new project from zero

### 1. Decide the profile, name, and Python targets

Ask only what you cannot infer: what the project does, whether it needs a CLI,
HTTP, notebooks, or Rust, and whether the user has a preferred local Python
version for `.venv`. The slug is hyphen-case (`churn-scorer`); the package is
the underscore form (`churn_scorer`).

Keep the two Python version knobs separate:

- **`--python-pin` (`.python-version`, default `auto`)**: the local dev
  interpreter. `auto` detects the user's installed `3.11+` local Python so
  `uv sync` reuses their local interpreter and warm `uv` wheel cache instead of
  downloading a new Python. Pass `--python-pin 3.12` (etc.) if the user states a
  preference.
- **`--python-floor` (`requires-python`, default `3.11`)**: the package's
  downstream compatibility floor — set to the oldest non-EOL Python
  ([devguide](https://devguide.python.org/versions/): 3.10 reached EOL in Oct
  2026) unless the code genuinely needs `3.12+` features or must target a legacy
  `3.10` host. If the user's local Python is `<=3.10`, advise upgrading with
  `uv python install 3.13 && uv python pin 3.13`.

### 2. Generate

```bash
# preview first — writes nothing
uv run skills/local/python-project-best-practice/scripts/new-python-project.py \
    --dry-run --profile cli ./my-tool

uv run .../new-python-project.py --profile cli --owner <gh-user> \
    --description "One line." ./my-tool
```

JSON summary on stdout (`project`, `package`, `profile`, `files[]`,
`recommended_skills[]`, `next_steps[]`); progress on stderr.

### 3. Verify it actually works

```bash
cd ./my-tool
uv sync
just docs-sync     # fills the CLI block in AGENTS.md from --help
just check         # fmt + lint + types + tests + docs-drift
```

`just check` must be green before you hand the project over. If it is not,
that is a bug in this skill's template — fix the template, not the generated
copy.

### 4. Offer the companion skills

Print the install line and let the user choose:

```bash
npx skills@latest add daviddwlee84/agent-skills/skills
```

Do not vendor copies into the new repo — copies have no update path.

## Workflow B — modernize an existing project

### 1. Audit, read-only

```bash
uv run skills/local/python-project-best-practice/scripts/audit-python-project.py \
    --format table /path/to/repo
```

27 checks with evidence and a fix hint, plus an ordered `migration_plan[]`.
Exit `4` when something fails. It never writes, and there is deliberately no
`--fix`.

### 2. Show the user the plan and agree on scope

Nobody asked you to rewrite their build system. Confirm which rungs to do now.

### 3. One rung per pull request

The ladder is ordered by dependency: environment → layout → dependency
declaration → lint/format → types → tests → task runner + CI → console script
→ logging layering → agent contract → secrets. Each rung ends with the test
suite green.

Read [`references/legacy-refactor.md`](references/legacy-refactor.md) before
the first rung. The traps that ruin these migrations — `uv add -r
requirements.txt` flattening the dependency graph, a formatting commit
destroying `git blame`, a tracked `.env` needing rotation before any history
rewrite — are all there.

## Available scripts

- **`scripts/new-python-project.py <target-dir>`** — scaffold from the bundled
  template tree. Offline; writes nothing outside the target.
  - Flags: `--profile {minimal,cli,lib,api,ml,rust}`, `--name`, `--description`,
    `--author`, `--owner`, `--python-floor`, `--python-pin`, `--dry-run`,
    `--force`, `--no-git`, `--help`.
  - Output: JSON on stdout, progress on stderr.
  - Exit: `0` ok · `2` usage · `3` target exists without `--force` ·
    `4` template tree and `assets/manifest.toml` disagree.
- **`scripts/audit-python-project.py [path]`** — read-only scorecard +
  migration plan.
  - Flags: `--format {json,table}`, `--fail-on {fail,warn,never}`, `--help`.
  - Exit: `0` clean · `3` path is not a directory · `4` findings at or above
    the threshold.

Both are PEP 723 `uv run` scripts with inline dependencies — no environment
setup needed before calling them.

## Bundled assets

- `assets/project/` — the template tree. Every file ends `.tmpl`; the generator
  strips the suffix, substitutes placeholders, and drops
  `# __IF:profile,...__ … # __END__` blocks that the chosen profile does not
  want.
- `assets/manifest.toml` — maps each destination path to the profiles that get
  it. **A file not listed here is never copied**, and the generator fails
  (exit 4) if the manifest and the tree disagree in either direction.

## Reference files

Load on demand — do not read them all up front.

| File | Load when |
|---|---|
| [`uv-and-pyproject.md`](references/uv-and-pyproject.md) | writing `pyproject.toml`, lockfile policy, build backends, interpreter pinning, workspaces, publishing |
| [`tyro-cli.md`](references/tyro-cli.md) | building or splitting a CLI, subcommands, completion, config objects |
| [`quality-gates.md`](references/quality-gates.md) | configuring ruff, a type checker, pytest, coverage, pre-commit, CI |
| [`logging-and-config.md`](references/logging-and-config.md) | wiring loguru, pydantic-settings, `.env`, direnv |
| [`notebooks-and-widgets.md`](references/notebooks-and-widgets.md) | adding `notebooks/`, dual-mode notebooks, packaged widgets |
| [`api-and-services.md`](references/api-and-services.md) | the project serves HTTP, or someone asks "should this be an MCP?" |
| [`rust-pyo3.md`](references/rust-pyo3.md) | adding a compiled extension, or a Rust edit appears to do nothing |
| [`agent-interface.md`](references/agent-interface.md) | writing AGENTS.md, the package's own skill, the docs-drift gate |
| [`legacy-refactor.md`](references/legacy-refactor.md) | Workflow B — reading the scorecard, ordering the migration |

## Gotchas

- **`uv sync` deletes what is not in the lock.** A manual `uv pip install`
  disappears on the next sync with no warning. Change dependencies with
  `uv add` / `uv remove` so `pyproject.toml` and `uv.lock` move together.
- **Do not run black and `ruff format` together.** `ruff format` is a black
  reimplementation; two formatters fight over magic trailing commas and quoting
  and every commit reformats the last one's output. Pick ruff.
- **A flat layout imports your source directory, not the installed package.**
  Missing `__init__.py`, missing `py.typed`, unshipped package data — all pass
  the test suite and break the user's install. `src/` is what closes that gap.
- **`[dependency-groups]` is not `[project.optional-dependencies]`.** An extra
  is published and installable by your users; a PEP 735 group is local-only.
  Your test runner in an extra is a defect you cannot remove without a release.
  `uv sync` installs the `dev` group by default.
- **`uv.lock` does not constrain your users — `[project.dependencies]` and
  `requires-python` do.** Installed as a dependency or via `uv tool install`,
  your package is resolved from `pyproject.toml` and `uv.lock` is ignored.
  Commit `uv.lock` to pin *your* dev and CI environment, but keep
  `[project.dependencies]` floors as wide as your actual API usage allows (`>=`
  the lowest compatible release, no `<` or `~=` upper caps).
- **`uv init` and `uv add` over-constrain packages by default.** `uv init` sets
  `requires-python` to whatever interpreter you ran it with (e.g. `>=3.13`), and
  `uv add foo` writes `foo>=<version-released-today>`. If others install your
  package, both cause avoidable solver conflicts: lower `requires-python` to the
  oldest non-EOL Python your features actually need (`>=3.11`), and relax
  `[project.dependencies]` floors after `uv add`.
- **loguru in a library hijacks the importing program's logging.** Library
  modules use `logging.getLogger(__name__)`; only entry points call
  `_log.configure()`. Symptom: "my app's log format changed after I added a
  dependency."
- **`logger.add()` without `logger.remove()` prints everything twice.** loguru
  installs a default stderr sink at import. This is the most common loguru bug.
- **`diagnose=True` puts local variables in tracebacks** — a credential leak in
  production logs. Keep it off outside development.
- **`.python-version` is read by pyenv as well as uv.** A stale value silently
  changes which interpreter builds the venv. Write it with `uv python pin`.
  Use it for your preferred local interpreter (to reuse your local `uv` wheel
  cache); keep `requires-python` lower for consumer compatibility, and test both
  ends in CI. If either is on EOL Python (`<=3.10`), upgrade with `uv python pin
  3.13`, set `requires-python = ">=3.11"` + `ruff.target-version = "py311"`, and
  run `uv run ruff check --select UP --fix`.
- **`ruff`'s `target-version` should be your `requires-python` floor**, not
  your dev pin, or `UP` rewrites code to syntax your declared floor cannot run.
- **direnv cannot change `PS1`.** There is no `(.venv)` prompt even when the
  env is active — check `which python`, not the prompt. And `.envrc` needs
  `direnv allow` after every edit.
- **Tyro already has shell completion.** Do not add shtab, argcomplete, or a
  `completion` subcommand: `my-tool --tyro-write-completion zsh <path>`.
  `--tyro-print-completion` is deprecated (a stray `print()` corrupts it), the
  bash completion filename must equal the command name, and no package manager
  installs completions for you.
- **No `[project.scripts]` means `uv tool install` gives the user no command.**
  `python -m pkg` is not a substitute for something on `PATH`.
- **marimo notebooks are real `.py` files**, so ruff lints them: without
  `per-file-ignores` for `notebooks/*` (`E402`, `F401`, `B018`) the gate fails
  and the formatter can reorder cell contents.
- **A PEP 723 script runs in its own environment.** A helper in `scripts/`
  with an inline header cannot `import your_package` unless the package is in
  its own `dependencies`.
- **`uv sync` does not rebuild a Rust extension after you edit `rust/`.** It
  sees the package as installed, does nothing, and you keep running the stale
  binary — including the print statements you just added to debug it. Use
  `uv sync --reinstall-package <slug>` or `maturin develop --uv`. A compiled
  module also needs a hand-written `.pyi`, or type checkers report
  `has no member`.
- **Never replace the `AGENTS.md`↔`CLAUDE.md` symlink with a real file.** Git
  stores it as mode `120000`; two real files drift and only one is right. A
  Windows checkout without symlink support materializes a text file containing
  the path — that is the corruption, not the fix.
- **Do not `uv add -r requirements.txt` when migrating.** A pip freeze is a
  flattened graph; that command promotes every transitive pin to a direct
  dependency. Add the packages you actually import and let the resolver work.
- **uv does not supply non-Python system libraries.** Some scientific stacks
  still need conda or OS packages. Say which ones in the README instead of
  pretending the migration is complete.
- **A tracked `.env` is an incident, not a cleanup.** Rotate every credential
  first; history rewriting is a later, separately-approved step. Hand off to
  `agent-history-hygiene`.

## Related skills

`verifiable-surfaces` (CLI surface design — assumed by this layout) ·
`project-knowledge-harness` · `agent-history-hygiene` ·
`mkdocs-site-bootstrap` · `git-workflow` · `marimo-batch-mlflow` ·
`fastapi-ai-patterns` · `cli-release-distribution` · `mcp-builder`
