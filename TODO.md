# TODO

## P2

- [ ] **[M] Batch-aware receiver completion** — `_sender_done.json` remains after a batch; a non-follow receiver started before the next sender can see an empty relay and exit early. Define a compatible completion/reset lifecycle and add repeated-batch tests before recommending that launch order.
- [ ] **[S] Interrupted transfer exit status** — CLI SIGTERM sets the stop event, but send/receive can return no failures and exit 0 with work still incomplete. Return a distinct interruption status and test it without changing resumable state or treating shutdown as delivery success.
- [ ] **[M] Parallel units on the push side** — one uploader per job today; useful when a single stream does not saturate the uplink (a Baidu upload was measured flat at ~10 MB/s with 1 or 2 streams on one host).
- [ ] **[S] `ferry gc`** — remove orphan remote unit dirs / stale `.tmp` staging after aborted jobs, with a dry run.
- [ ] **[M] Restore-on-demand** — `ferry fetch` of selected keys from an archive-only destination back to a working host.

## P3

- [ ] **[S] More adapters** — WebDAV/HTTP PUT, S3 presigned URLs without rclone.

## Done

- ✅ [2026-10-30] [P2/S] Per-job XDG config defaults — `ferry init` defaults to a named file under `$XDG_CONFIG_HOME/ferry/jobs/` (fallback `~/.config/ferry/jobs/`); explicit `--output` supports project-local configs. Managed directories are created only after confirmation, existing files are never overwritten or migrated, and tests cover multiple jobs and XDG edge cases.
