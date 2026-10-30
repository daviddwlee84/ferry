# English pilot, dogfooding, and later installation

## Scope and ownership

This skill starts with English, static navigation/search, code blocks, native
LLM exports and Copy as Markdown. It does not promise API-generator integration,
MkDocs plugin compatibility, bilingual content/fallback, or versioned publishing.
Do not add a multi-build locale workaround merely to call the pilot bilingual.

For a local dogfood project, run the canonical skill script with an explicit
`--target-dir`. The project owns its resulting `docs/`, `zensical.toml`,
`scripts/check-docs-site.py`, dependency lock, and optional build workflow. It
must build without a checkout of the skill repository or an installed agent.
Do not commit cross-repository symlinks to make this work.

The first dogfood target is ferry: preserve its dependency-free runtime, existing
README, dev checks, package index, and backend/credential boundaries. The docs
group is a development toolchain, not a new requirement for transfer hosts.
Pages activation is separate from building the site locally.

## Install the published skill later

After the skill is merged and available remotely, run in the consuming repo:

```bash
npx skills@latest add daviddwlee84/agent-skills/skills \
  --skill zensical-site-bootstrap
```

This consumer command intentionally tracks the published CLI. For reproducible
installation smoke tests, pin the tested CLI version (currently `skills@1.5.25`)
and use an isolated target directory. Review agent selection, installed files,
and `skills-lock.json`; do not claim an unpublished remote skill is installable.

Installing the skill provides future agent instructions and helpers. It must not
rebootstrap the website or overwrite the project-owned checker. Keep framework
version/lock upgrades in explicit changes with artifact and browser validation.
Avoid locally editing installed skill copies: improve the canonical source,
then reinstall/update the distribution copy.

## Revalidate on upgrades

The initial pin is Zensical **0.0.73**, not a permanent guarantee. Test:

1. Initialization dry-run, nonempty-doc consent, conflicts, and symlink refusal.
2. Fresh and curated existing docs; root and deeply nested source pages.
3. Exact llms inventory, nonempty full text, Markdown pairs, and copy targets.
4. GitHub project subpaths, navigation, search, images, and relative links.
5. Project's normal lint/tests and absence of application runtime dependencies.
6. Browser search and clipboard behavior, followed by build-only CI.

To consider English plus selected zh-TW, additionally prove same-page switching,
missing-translation English fallback, translated navigation/search, source links,
and default-language LLM outputs. Until then, use the existing MkDocs skill for
that requirement. A language dropdown is not sufficient evidence.

## Sources

Verified on 2026-10-30; revisit these pages when changing the pin:

- [Zensical 0.0.73](https://github.com/zensical/zensical/releases/tag/v0.0.73)
- [Native plugin compatibility](https://zensical.org/docs/compatibility/mkdocs/plugins/)
- [Language settings](https://zensical.org/docs/setup/language/)
- [Roadmap](https://zensical.org/roadmap/)
- [GitHub Pages deployment](https://zensical.org/docs/publish-your-site/)
