# The agent interface: AGENTS.md, the self-skill, and the drift gate

Load this when writing AGENTS.md, adding the package's own skill, or wiring the
check that stops those docs from going stale.

## Three artifacts, three audiences

| File | Audience | Answers |
|---|---|---|
| `README.md` | a human evaluating or installing | what is this, how do I install and run it |
| `AGENTS.md` | an agent working *in* this repo | how do I change this code correctly |
| `.agents/skills/<name>/SKILL.md` | an agent *using* the tool elsewhere | how do I drive this CLI |

They overlap in content and differ in purpose. Do not merge them: the README
gets read by someone deciding whether to try the tool, AGENTS.md by something
about to edit it.

## AGENTS.md ↔ CLAUDE.md

Write one real file and symlink the other:

```bash
ln -sf AGENTS.md CLAUDE.md
```

`AGENTS.md` is the cross-agent convention (Codex, Cursor, OpenCode, Claude Code
all read it or its symlink), so make that the real file. The direction is
arbitrary as long as exactly one is a symlink — *this* skills repo happens to
use the reverse.

Never replace the symlink with a real file. Git stores it as mode `120000`;
two real files drift within a week and only one of them will be right. On a
Windows checkout without symlink support the link materializes as a text file
containing the path `AGENTS.md`, which looks like corruption — that is what it
is, and re-creating the symlink fixes it.

## What belongs in AGENTS.md

Only things an agent would otherwise get wrong. Not a tutorial.

- **The command surface.** "Use `just`; `just --list` is complete." An agent
  that has to infer commands from CI config will infer them wrong.
- **Prohibitions with reasons.** "Never `pip install` — `uv sync` erases it."
  A rule without a reason gets rationalized away.
- **Layout, one line per directory**, including which direction imports flow.
- **Non-obvious conventions.** Where logging is configured and why library
  modules must not; the exit-code contract; where a new setting has to be
  added (`settings.py` *and* `.env.example`).
- **The definition of done.** `just check` passes.

Leave out anything the code already states plainly. Every line costs context on
every request.

## The package's own skill

Ship `.agents/skills/<slug>/SKILL.md` inside the repo. Two payoffs: agents
working in *this* repo pick it up from the filesystem, and downstream users get
it in the same install as the code — the tool and its instructions version
together, so an agent never reads last release's flags.

It should carry what a caller needs and nothing more: install line, exit-code
table, the stdout/stderr split, how to add a subcommand, and the generated CLI
reference.

Frontmatter `description` is always-on context for every agent that loads the
skill. Make it name concrete trigger situations, keep it in the 120–500
character range, and **quote it** if it contains `:` or `#` — an unquoted YAML
scalar containing `": "` makes loaders drop the skill silently. See the
`skill-author` skill for the full authoring rules.

## The drift gate

Prose about a CLI rots the first time someone renames a flag. Generate it
instead:

```bash
just docs-sync     # rewrite the block between the CLI markers from --help
just docs-check    # exit 1 if the committed block is stale  (part of `just check`)
```

`scripts/sync_agent_docs.py` runs `python -m <pkg>.cli --help` plus each
subcommand's `--help`, strips ANSI, and splices the result between
`<!-- BEGIN CLI -->` and `<!-- END CLI -->` in every target file. Because
`--check` is in `just check` and in CI, a flag rename that skips the docs fails
the build. The docs become a build artifact with a test, rather than prose
someone has to remember.

Two details that make it deterministic: force `NO_COLOR=1` and a fixed
`COLUMNS`, or the block churns depending on whose terminal ran it.

## Recommended companion skills

Print these in the scaffolder's `next_steps[]` and list them in AGENTS.md.
Do not vendor copies into the project — copies go stale and there is no update
path. One install line covers all of them:

```bash
npx skills@latest add daviddwlee84/agent-skills/skills
```

| Always | Why |
|---|---|
| `project-knowledge-harness` | `TODO.md` / `backlog/` / `pitfalls/` so deferred work and past traps survive the session |
| `agent-history-hygiene` | commit agent transcripts and plans with the diff, without leaking `.env` |
| `mkdocs-site-bootstrap` | GitHub Pages docs for humans, `llms.txt` for agents |
| `verifiable-surfaces` | the `--help` / `--dry-run` / exit-code discipline this layout assumes |
| `git-workflow` | Conventional Commits and AI-provenance trailers |

| By project type | |
|---|---|
| ML | `mlflow-tracking`, `dvc-ml-workflow`, `experiment-knowledge-harness`, `marimo-batch-mlflow` |
| HTTP service | `fastapi-ai-patterns`, `fastapi-ai-scaffold` |
| Library for others | `cli-release-distribution` (its Python section is `uv tool install` / PyPI) |
