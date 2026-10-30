# Tyro CLIs

Load this when building or restructuring a CLI, splitting subcommands into
modules, or wiring shell completion.

Reference: <https://brentyi.github.io/tyro/>. Tyro derives the parser from
types, so the CLI and the code cannot drift.

## Commands are frozen dataclasses

```python
@dataclasses.dataclass(frozen=True)
class Hello:
    """Print a greeting."""          # becomes the subcommand's help text

    name: str = "world"
    """Who to greet."""              # becomes --name's help text

    dry_run: bool = False            # becomes --dry-run / --no-dry-run
    """Report what would happen; change nothing."""

    def run(self) -> int:            # returns the process exit code
        ...
```

Field docstrings become flag help, so the `--help` output and the code are the
same artifact. Underscores become hyphens: `dry_run` is `--dry-run`.

Why data rather than callbacks: tests construct `Hello(times=0)` and call
`.run()` directly. No argv, no monkeypatching, no subprocess.

## Subcommands

A union of dataclasses becomes a subcommand group, named after the kebab-case
class name:

```python
Command = Hello | Info          # -> `tool hello`, `tool info`

def main(argv: list[str] | None = None) -> int:
    return tyro.cli(Command, args=argv, prog="tool").run()
```

`args=argv` (rather than reading `sys.argv` implicitly) is what makes
`main(["hello", "--name", "ada"])` testable.

To override a name or add per-subcommand config, annotate:

```python
Command = Annotated[Hello, tyro.conf.subcommand("greet")] | Info
```

### Splitting into modules

One file per command once `commands.py` passes a couple of hundred lines:

```
cli/
  __init__.py     the Command union, main(), the exit-code contract
  __main__.py     python -m pkg.cli
  hello.py        one dataclass
  info.py         one dataclass
```

Keep the union in `__init__.py`. It is the registry: one place to look for
"what commands exist", and one place to edit when adding one.

## Positional arguments

Dataclass fields become flags by default. For a positional:

```python
target: tyro.conf.Positional[str]
```

## Shell completion

Built in. Do not add shtab, argcomplete, or a hand-written `completion`
subcommand — tyro extends shtab internally and exposes it as a flag:

```bash
# zsh
mkdir -p ~/.zfunc
my-tool --tyro-write-completion zsh ~/.zfunc/_my-tool
# ~/.zshrc:  fpath+=~/.zfunc && autoload -Uz compinit && compinit

# bash — the filename must match the command name
dir="${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions"
my-tool --tyro-write-completion bash "$dir/my-tool"
```

Gotchas, in the order people hit them:

- `--tyro-print-completion` is **deprecated**; it is easy to corrupt the output
  with a stray `print()` or log line. Use `--tyro-write-completion <shell>
  <path>`, which writes the file directly.
- For bash, the completion filename must equal the command name.
- Completion is documented as experimental. Verify it in a fresh shell before
  telling users it works.
- No package manager installs completions for you. Say so in your README, and
  see the `cli-release-distribution` skill for the full story.

## Config objects, not just flags

`tyro.cli` fills nested dataclasses from a flat flag namespace, which is why
the same object can come from a form, a config file, or argv:

```python
@dataclasses.dataclass(frozen=True)
class TrainConfig:
    model: ModelConfig      # --model.layers, --model.dropout
    data: DataConfig        # --data.batch-size
```

This is the mechanism behind the marimo dual-mode notebook — see the
`marimo-batch-mlflow` skill.

## The surfaces every command owes you

`--help`, `--dry-run` before any side effect, a `--print-config`-equivalent, a
documented exit-code contract, data on stdout and diagnostics on stderr. That
checklist and how to verify it belong to the `verifiable-surfaces` skill; read
it before adding a command that writes, deletes, or calls out to a network.
