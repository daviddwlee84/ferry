# ferry — agent instructions

Standalone, stdlib-only Python (>=3.11) tool. Keep it dependency-free: target hosts are
often old (CentOS 7 via uv-managed Python), without root, with restricted egress.

- Checks: `just check` (ruff + pytest). Tests that touch real clouds are marked
  `network` and skipped by default (`uv run pytest -m network`).
- Every backend command must go through `ferry.proc.run`/`run_transfer` so proxy
  variables are stripped (`egress.clean_env`). Never call `subprocess` directly.
- Never trust backend checksums; verify with our own sha256 and sizes.
- Every remote object must be non-empty (Baidu drops empty files).
- Never read, print or log backend credentials/session files.
- Ledger formats (`sender.jsonl`, `receiver.jsonl`) and the manifest are compatibility
  surfaces: bump `MANIFEST_VERSION` and handle old records when changing them.
- Record backend traps in `pitfalls/` (titled by symptom); future work in `TODO.md`.
- Do not commit or push without an explicit request.
- Documentation uses an English Zensical pilot: `just docs-setup`, `just docs-build`,
  `just docs-serve`. Keep Zensical in the `docs` dependency group, never runtime deps.
  Preserve `docs/` and `zensical.toml`; do not apply the MkDocs recipe from installed
  generic Python skills. The workflow checks pull requests and publishes GitHub
  Pages from `main`; Pages publishing was explicitly authorized by the user.
  No multilingual fallback is implemented in this pilot.
