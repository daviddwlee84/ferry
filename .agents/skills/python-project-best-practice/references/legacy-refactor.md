# Modernizing an existing project

Load this for Workflow B: an existing repo that needs to reach the conventions
in this skill, and reading the audit scorecard.

## Rules of engagement

1. **The audit is read-only, and so is the first conversation.** Run
   `audit-python-project.py --format table`, show the user, agree on scope.
   Nobody asked you to rewrite their build system.
2. **One rung per pull request.** The migration plan is ordered because each
   rung depends on the ones above it. A single "modernize tooling" PR touching
   layout, packaging, lint, and CI at once cannot be reviewed, cannot be
   bisected, and will be reverted wholesale when something breaks.
3. **The test suite is the safety net; if there isn't one, that is the first
   rung with real content.** Migrating layout and packaging without tests is
   moving furniture in the dark.
4. **Do not "fix" warnings the user did not ask about.** Report them and move
   on.

## Reading the scorecard

| Status | Meaning |
|---|---|
| `fail` | actively causes wrong behavior or blocks the rest of the ladder |
| `warn` | legacy or missing, but nothing is broken today |
| `n/a` | not applicable to this project shape |

`--fail-on warn` makes the audit a CI gate once the project is clean. Exit code
`4` means something at or above the threshold.

The `migration_plan` array omits rungs with nothing outstanding, so a partially
modern project gets a short plan rather than the whole ladder.

## The ladder

### 1. uv as the single source of truth

Get `pyproject.toml` + `uv.lock` + `.python-version` in place first; everything
else depends on being able to reproduce the environment.

**Do not `uv add -r requirements.txt`.** A pip freeze is a flattened graph:
that command promotes every transitive pin to a direct dependency, and you
inherit a hundred constraints you did not choose and cannot upgrade. Instead:

```bash
uv init --package                 # or hand-write [project] in pyproject.toml
uv add <the packages you import>  # imports only; let the resolver do the rest
# then relax [project.dependencies] floors from uv add's >=<today> default to
# >=<lowest-compatible-minor>, and set requires-python = ">=3.11"
uv lock && uv sync
uv run pytest                     # then reconcile what actually broke
```

If the project is still on an EOL Python (`<=3.10` per
<https://devguide.python.org/versions/>), upgrade `.python-version` (`uv python
pin 3.13`), bump `requires-python = ">=3.11"` and `ruff.target-version =
"py311"`, and run `uv run ruff check --select UP --fix` once the ruff rung is in
place.

Read the imports, not the file. `grep -rhoE '^(from|import) [a-z_]+' src/ |
sort -u` is a faster first pass than reading `requirements.txt`.

From poetry: convert `[tool.poetry]` to a PEP 621 `[project]` table first, then
uv takes over. From conda: port everything that is a Python package, and keep
conda only for system libraries uv genuinely cannot supply (some CUDA, GDAL,
proprietary solvers). Say which ones in the README.

Keep `setup.py` until the wheel builds and installs identically, then delete it
in its own commit.

### 2. src layout

```bash
git mv oldpkg src/oldpkg
```

Then `uv sync && uv run pytest` and expect failures — that is the point.
Anything that breaks here was previously passing only because the working
directory was on `sys.path`: missing `__init__.py`, package data referenced by
relative path, a test importing a module that was never packaged. Every one of
those was already broken for anyone installing your wheel.

### 3–7. Dependency groups, ruff, types, tests, task runner + CI

Mechanical; see [`quality-gates.md`](quality-gates.md). Two sequencing notes:

- Land `ruff format` as a **separate, formatting-only commit** with no logic
  changes, and record its SHA in `.git-blame-ignore-revs`. Otherwise you lose
  `git blame` across the whole codebase.
- Turn `select` rules on in batches, not all at once. `["E", "F", "I"]` first,
  fix, commit; then `["UP", "B"]`; then the rest. A 4,000-violation first run
  gets `--fix --unsafe-fixes`'d in frustration and reviewed by nobody.
- If you drop black for `ruff format`, remove black in the same commit. Two
  formatters is worse than either.

### 8. Console script

If the project has a CLI module but no `[project.scripts]`, users have no
command after install. Adding the entry point is usually three lines and the
highest-visibility change on this list.

### 9. Logging layering

The audit flags `loguru` imported outside entry points. The fix is
mechanical — swap those modules to `logging.getLogger(__name__)` and move the
`logger.add()` call into `_log.configure()` — but it changes log output, so do
it alone and eyeball the result. See [`logging-and-config.md`](logging-and-config.md).

### 10. Agent contract

`AGENTS.md` + the symlink + the package's own skill. See
[`agent-interface.md`](agent-interface.md).

### 11. Secrets

`env-not-tracked` is the only check that can mean an active incident. If `.env`
is tracked, the order is **rotate first, rewrite later**:

1. Rotate every credential in that file. The history is already on every clone,
   every fork, and every CI cache; a force-push does not un-leak it.
2. `git rm --cached .env`, add it to `.gitignore`, commit.
3. Only then discuss history rewriting, and only with the user's explicit
   go-ahead — it breaks every open PR and every existing clone.

The `agent-history-hygiene` skill owns this remediation path, including
pre-commit secret scanning so it does not recur. Hand off to it.

## What not to migrate

- A project in maintenance with no active development. The migration has a
  cost and no payoff.
- Notebooks with published output that people cite. Convert new work to marimo;
  leave the archive alone.
- A conda environment that exists because of non-Python system libraries. uv
  does not replace that, and pretending otherwise breaks the build on someone
  else's machine.
