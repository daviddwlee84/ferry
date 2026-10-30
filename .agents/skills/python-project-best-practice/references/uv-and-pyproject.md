# uv and pyproject.toml

Load this when writing or repairing `pyproject.toml`, deciding what goes in a
lockfile, choosing a build backend, pinning the interpreter, or publishing.

uv's CLI surface changes between minor versions. When you are unsure of a flag,
fetch <https://docs.astral.sh/uv/reference/cli/> rather than guessing.

## The commands that matter

| Goal | Command |
|---|---|
| Start a project | `uv init --package --lib` (or `--app`) |
| Add a runtime dependency | `uv add httpx` |
| Add dev tooling | `uv add --dev pytest ruff` |
| Add to a named group | `uv add --group docs mkdocs-material` |
| Add a published extra | `uv add --optional postgres asyncpg` |
| Recreate the env from the lock | `uv sync` |
| Fail instead of re-resolving | `uv sync --locked` (use this in CI) |
| Check the lock is current | `uv lock --check` |
| Pin the interpreter | `uv python pin 3.13` |
| Run anything | `uv run <cmd>` |
| Bump the version | `uv version --bump minor` |
| Build + publish | `uv build && uv publish` |

`uv run` re-syncs before running. In a hot loop where you know the environment
is current, `uv run --no-sync` skips it.

## Groups vs extras

This is the distinction people get wrong, and getting it wrong ships your test
runner to your users.

```toml
[project.optional-dependencies]   # PUBLISHED. `pip install yourpkg[postgres]`
postgres = ["asyncpg>=0.30"]

[dependency-groups]               # LOCAL ONLY. PEP 735. Never published.
dev = ["pytest>=8.3", "ruff>=0.9"]
docs = ["mkdocs-material>=9.5"]
```

`dev` is special-cased: `uv sync` and `uv run` include it by default. Other
groups need `--group docs`. `--no-default-groups` gives a runtime-only env.

A dev tool in `optional-dependencies` is a real defect: it appears in your
package metadata, resolves into your users' dependency graph if they ever
install the extra, and cannot be removed without a release.

> Interop note: some tooling still generates a `docs` **extra** — for example
> the `mkdocs-site-bootstrap` skill writes `[project.optional-dependencies]
> docs`. That keeps working (`uv sync --extra docs`); it is not worth a
> migration unless you are publishing the package.

## Lockfile policy

Commit `uv.lock`. For applications and CLIs this is obvious. For libraries it
is also right, and the usual objection is based on a misunderstanding:

**`uv.lock` never constrains your users.** When someone installs your package
as a dependency, their resolver reads `[project.dependencies]` and ignores your
lock entirely. The lock pins *your* development and CI environment, which is
exactly what you want reproducible.

What the lock does not do is prove your declared ranges are honest. If you
publish a library and want to verify that your declared `requires-python` and
`[project.dependencies]` floors actually pass tests, run an on-demand isolated
check (using `--isolated` so it never downgrades your working `.venv`):

```bash
uv run --isolated --python 3.11 --resolution lowest-direct pytest
```

## Interpreter pinning vs `requires-python` compatibility

`.python-version` and `requires-python` solve opposite problems. Conflating
them (which `uv init` does by default) either slows down local setup or locks
out downstream users:

| Setting | Purpose | How to choose |
|---|---|---|
| `.python-version` (`--python-pin`) | Local dev `.venv` interpreter | Match the user's preferred local Python (or let `--python-pin auto` pick an already-installed `3.11+` interpreter) so `uv sync` skips downloading Python and reuses the local `uv` wheel cache. |
| `requires-python` (`--python-floor`) | Downstream package compatibility floor | Set to the **oldest non-EOL Python (`>=3.11`)** unless you genuinely use newer language/stdlib features or must target a legacy host. |

Put both ends in the CI matrix (`[python_floor, python_pin]`). `.python-version`
is also read by pyenv; on a machine with both, a stale value changes which
interpreter builds the venv with no warning.

### Choosing `requires-python` by actual features used

Per <https://devguide.python.org/versions/>, Python **3.10 reached End-of-Life
in October 2026**. Default to `>=3.11` and only raise the floor when runtime
code actually needs a newer feature (remember: `from __future__ import
annotations` makes newer type-annotation syntax work on older runtimes without
raising `requires-python`):

| Floor | Raise to this only when runtime code uses |
|---|---|
| `>=3.10` (EOL) | Legacy deployment target only (e.g. unupgraded Ubuntu 22.04 host). Supports runtime `X \| Y` unions (PEP 604) and `match`/`case`. |
| `>=3.11` **(default)** | Oldest non-EOL Python. Stdlib `tomllib`, `enum.StrEnum`, `typing.Self`, `ExceptionGroup` / `except*`, `asyncio.TaskGroup`. |
| `>=3.12` | `type X = ...` statements and PEP 695 generic syntax (`def f[T]()`), `@typing.override`, `itertools.batched`, `Path.walk`. |
| `>=3.13` | PEP 696 `TypeVar` defaults (`T = int`), `warnings.deprecated`, free-threaded / JIT experimentation. |

### Upgrading a project off an EOL Python (`<=3.10`)

When a user's local environment or repository is still on Python `<=3.10`,
recommend and execute these four steps together:

```bash
# 1. Install and pin a modern dev interpreter (3.13 or user's preferred 3.11+)
uv python install 3.13 && uv python pin 3.13

# 2. In pyproject.toml, set:
#    requires-python = ">=3.11"
#    [tool.ruff] target-version = "py311"

# 3. Let ruff's pyupgrade (UP) rules modernize legacy syntax/typing automatically
uv run ruff check --select UP --fix

# 4. Re-lock and run the full gate
uv lock && uv sync && just check
```

## Dependency bounds for installable packages

When someone installs your package (`pip install`, `uv add`, `uv tool install`),
their resolver intersects your `[project.dependencies]` with everything else in
their environment. Two habits keep your package easy to install:

1. **Relax `uv add`'s lower bound.** `uv add foo` writes
   `foo>=<version-released-today>` (e.g. `pydantic-settings>=2.10.1`). If your
   package only uses APIs present since `2.0`, relax the entry in
   `[project.dependencies]` to `"pydantic-settings>=2.0"`. `uv.lock` still keeps
   your dev and CI environment on `2.10.1`.
2. **Do not add upper caps (`<`, `~=`, `==`) in `[project.dependencies]`.**
   Upper-capping `"pydantic>=2.0,<2.11"` or `"tyro~=0.9"` forces solver deadlocks
   for consumers the day a compatible minor version ships. Only add an upper
   exclusion (`!=x.y.z` or `<major`) after a specific release is proven broken
   with your code.

## Build backends

| Backend | When |
|---|---|
| `hatchling` | default. Plugin ecosystem (`hatch-vcs` for git-tag versions) |
| `uv_build` | lean, fast, no plugins. Fine for a pure-Python package |
| `maturin` | any Rust/PyO3 extension |
| `scikit-build-core` | C/C++/CMake extensions |

With a src layout and a hyphenated project name, name the package directory
explicitly so the backend does not have to guess:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/my_tool"]
```

## Distribution

Tier 0, and enough for most internal tools — no PyPI account needed:

```bash
uv tool install git+https://github.com/owner/repo@v0.1.0
uvx --from git+https://github.com/owner/repo my-tool --help   # run once
```

Both require `[project.scripts]`. A package that can only be run as
`python -m pkg` gives the installer no command on `PATH`.

For PyPI, use Trusted Publishing (OIDC): configure the publisher on PyPI for
`owner/repo` + workflow filename, then give the job `id-token: write`. No API
token in Actions secrets, nothing to rotate or leak.

```yaml
permissions:
  id-token: write
steps:
  - run: uv build
  - run: uv publish        # picks up the OIDC token automatically
```

## Workspaces

For a monorepo of related packages, one lockfile and one venv for all of them:

```toml
[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
my-core = { workspace = true }
```

Members share the resolution, so a version conflict between two members is a
hard error rather than two silently different environments. If the packages
genuinely need different dependency versions, they are not a workspace.
