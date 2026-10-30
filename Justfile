# Show supported development commands.
default:
    @just --list

# Install locked development dependencies.
setup:
    uv sync --locked

test:
    uv run pytest

lint:
    uv run ruff check src tests

check: lint test

# Documentation dependencies are separate from the dependency-free CLI runtime.
docs-setup:
    uv sync --locked --group docs

docs-build:
    uv run --locked --group docs zensical build --strict
    uv run --locked --group docs python scripts/check-docs-site.py

# Optional port, then host; stay on loopback unless sharing is intentional.
docs-serve port="8000" host="127.0.0.1":
    uv run --locked --group docs zensical serve --dev-addr "{{host}}:{{port}}"

# Forward arguments to the CLI.
cli *args:
    uv run ferry {{args}}
