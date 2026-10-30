---
name: zensical-site-bootstrap
description: 'Bootstrap and maintain English Zensical documentation with locked uv docs dependencies, native llms.txt and Copy as Markdown, safe existing-content handling, and build-only GitHub Actions. Use when explicitly setting up Zensical, dogfooding a new docs site, checking LLM exports, or maintaining a Zensical project. Not a MkDocs migration or multilingual fallback solution; publishing requires separate consent.'
---

# Zensical site bootstrap

Build an English documentation site without adding application runtime
dependencies or publishing anything. This is a single-language pilot, separate
from `mkdocs-site-bootstrap`; existing MkDocs projects retain their old workflow.

Requires Python 3.11+ and `uv`. The tested builder is **Zensical 0.0.73**.

## When to use

- The user explicitly chooses Zensical for a new documentation site.
- A project is dogfooding Zensical before installing the published skill.
- A Zensical build succeeds but llms exports or Copy as Markdown are missing.
- The user wants to add pages or verify an existing Zensical site's artifacts.

## When not to use

- The user only asks for one Markdown document: do not scaffold a site.
- Existing MkDocs configuration, arbitrary plugins, hooks, or content-i18n
  requirements need migration analysis, not this initializer.
- English plus selected zh-TW, fallback, or contextual language switching is
  required now: prefer the existing MkDocs skill until that contract is tested.
- A generic request to "set up docs" is not permission to replace an existing
  engine. Inspect it and establish the desired scope first.

## Workflow

### 1. Confirm scope and inspect the project

Read project instructions, README, existing docs, `pyproject.toml`, task runner,
CI, `.gitignore`, and any recorded docs preferences. Keep the application's
runtime dependency contract. Do not reuse the MkDocs preference namespace or
change an explicit opt-out without the user's request.

Confirm an **English-only** initial site and which files are publishable. Do not
point `docs_dir` at the repository root or copy `backlog/`, agent history,
credentials, or runtime state into the site. A public repository is not consent
to enable Pages. Do not translate content automatically.

Read `references/pilot-and-migration.md` when comparing engines, planning later
translations, or replacing a local dogfood source with an installed skill.

### 2. Preview, then initialize

Use this skill's actual installed directory for the script path. Pass the
project root explicitly; never infer it from the agent's current directory.

```bash
python3 "$SKILL_DIR/scripts/init-docs-site.py" \
  --target-dir /path/to/project \
  --site-name 'My Project' \
  --repo-slug owner/project \
  --site-url https://owner.github.io/project/ \
  --ci --dry-run
```

After reviewing the plan and obtaining consent, repeat without `--dry-run`.
`--ci` adds a **build-only** workflow; omitting it creates no workflow.
For a reviewed nonempty `docs/`, add `--existing keep`. It requires an existing
`docs/index.md` and preserves the whole tree; it does not reorganize content.

The initializer refuses existing MkDocs/Zensical config, symlinks, and collisions
with generated paths. There is no `--force` mode. Re-running on a configured
project stops without changes: switch to maintenance instead.

### 3. Add development-only dependencies and build

Existing `pyproject.toml` is left untouched; follow the initializer's JSON
`next_steps` after reviewing the proposed dependency group:

```bash
uv add --group docs 'zensical==0.0.73'
uv sync --locked --only-group docs
uv run --no-sync zensical build --strict
uv run --no-sync python scripts/check-docs-site.py
```

A docs-only project without a prior `pyproject.toml` receives a non-packaged uv
project with Python >=3.11 and a docs group; run `uv lock` first instead of `uv add`.
Keep `uv.lock` under version control. Inspect its diff: do not silently upgrade
unrelated dependencies or replace an existing package index.

`--only-group docs` is suitable for CI. For local work that also needs the
application/dev dependencies, use `uv sync --locked --group docs` instead.
Do not put Zensical in `[project].dependencies` merely to make a build work.

The project-local checker verifies root and nested pages, full text, Markdown
counterparts, Copy as Markdown targets, project-subpath links, and a nonempty
search artifact. Fix failures instead of dropping strict mode. Its contract is
all `docs/**/*.md` pages; custom exclusions, output layouts, or plugins require
reviewing and adapting the checker as part of the same change.

### 4. Maintain project-owned documentation

Add English `.md` pages under `docs/` and keep relative links correct. The
initial config uses automatic navigation and llmstxt globs for root and nested
pages; no separate page helper is needed. If the user wants an explicit `nav`,
maintain it alongside the pages. Do not create locale-suffixed placeholders.

Preserve the README as a useful repository entrypoint. Link it to `docs/index.md`
until a site is actually published; a future `site_url` is not a live website.
Add commands to the project's existing Makefile/Justfile rather than replacing
its task runner. Run its normal checks as well as the documentation checks.

For preview:

```bash
uv run --no-sync zensical serve --dev-addr 127.0.0.1:8000
# If that port is occupied, choose another without stopping the existing service:
uv run --no-sync zensical serve --dev-addr 127.0.0.1:8769
```

Expose the address in the project's task runner instead of hard-coding port 8000.
For Just, use an optional port followed by an optional host:

```just
docs-serve port="8000" host="127.0.0.1":
    uv run --locked --group docs zensical serve --dev-addr "{{host}}:{{port}}"
```

`just docs-serve 8769` stays on loopback. A host such as `0.0.0.0` exposes the
preview to other machines; use it only when sharing is intentional.

The template enables Mermaid fences through `pymdownx.superfences.custom_fences`.
For process diagrams, keep node/edge labels explicit and supply a text equivalent.
Check rendered SVGs in light and dark mode; static build success does not prove
Mermaid syntax/layout or browser rendering.

In a browser, test navigation, search, and Copy as Markdown, including a nested
page. Check the clipboard content, not only that a button exists. Building the
site is not proof of interactive behavior.

### 5. Publish only after separate approval

The bundled workflow has no Pages permissions, deployment job, or public
preview. Do not enable Pages, dispatch deployment, or push changes merely
because initialization succeeded.

When explicitly asked to publish, read the current official
[Pages guide](https://zensical.org/docs/publish-your-site/). Adapt its artifact
flow to the existing locked docs group and checker. Keep PRs build-only; deploy
only approved default-branch events. Scope `pages: write` and `id-token: write`
to the deploy job, preserve project-subpath URLs, and confirm repo visibility.

## Available scripts

- `scripts/init-docs-site.py` — stdlib-only, no install/network/deploy operations.
  Flags: `--target-dir`, `--site-name`, `--site-url`, `--repo-slug`,
  `--existing keep`, `--ci`, `--dry-run`, `--help`.
  JSON stdout; diagnostics on stderr. Exit 0 success/preview, 2 blocked input.
- Project-local `scripts/check-docs-site.py` — copied from the asset below.
  Flags: `--target-dir` (defaults to CWD), `--help`. JSON stdout, diagnostics on
  stderr; exit 0 validated, 1 failed. It does not rebuild or publish anything.

## Bundled assets

- `assets/zensical.toml.template` — English theme, native copy/llmstxt, root and
  nested Markdown selection; no custom MkDocs plugin lifecycle.
- `assets/index.md.template` — starter page only for an empty docs tree.
- `assets/docs-check.yml` — locked build and artifact checks, no deployment.
- `assets/check-docs-site.py` — project-owned output-contract checker.

## Reference files

- `references/pilot-and-migration.md` — read before dogfooding, upgrading the
  builder, switching to a distributed skill, or considering multilingual docs.

## Gotchas

- **`**/*.md` alone can silently omit root pages.** The tested native llmstxt
  implementation needs `*.md` as well. Strict build can pass with empty exports.
- **Mermaid's rendered SVG lives in a closed shadow root in the tested release.**
  `pre.mermaid svg` / `.mermaid svg` selectors can time out even when the diagram
  is visible. Screenshot the `.mermaid` host and inspect its layout in both
  themes; host dimensions alone are not proof of correct rendering.
- **UI translation is not content i18n.** An alternate-language selector does
  not create translated pages or implement missing-translation fallback.
- **Zensical does not execute arbitrary MkDocs plugins.** Unsupported settings
  may be ignored; this pilot's checker allows only search and llmstxt plugins.
- **Upstream `zensical new` includes a deployment workflow with unpinned install.**
  Do not run it blindly inside a real project; this skill uses reviewed,
  build-only templates instead.
- **Installing/updating a skill does not migrate generated project files.**
  Keep the docs, config, checker and CI self-contained. Compare explicit changes
  after an update; never rerun initialization with an imagined force flag.
- **A local preview cannot prove GitHub Pages configuration.** Report local
  validation separately from installation, remote CI, and publication.
