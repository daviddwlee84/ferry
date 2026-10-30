# TODO

## P2

- [ ] **[M] Parallel units on the push side** — one uploader per job today; useful when a single stream does not saturate the uplink (a Baidu upload was measured flat at ~10 MB/s with 1 or 2 streams on one host).
- [ ] **[S] `ferry gc`** — remove orphan remote unit dirs / stale `.tmp` staging after aborted jobs, with a dry run.
- [ ] **[M] Restore-on-demand** — `ferry fetch` of selected keys from an archive-only destination back to a working host.

## P3

- [ ] **[S] More adapters** — WebDAV/HTTP PUT, S3 presigned URLs without rclone.

## Done
