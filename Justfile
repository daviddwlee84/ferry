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

# Forward arguments to the CLI.
cli *args:
    uv run ferry {{args}}
