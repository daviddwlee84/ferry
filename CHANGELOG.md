# Changelog

All notable changes to ferry. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) (0.x: config, ledger and manifest
formats may still change between minor versions).

## Unreleased

## [0.2.0] - 2026-11-04

### Added
- `[remote] stall_timeout` kills a backend transfer that prints nothing for that long and
  retries the part; rclone gets `--stats` and rsync `--info=progress2` when it is set.
- Ledger event `paused` (sender and receiver): a transfer stopped by a closing window.
- Ledger event `failed` with `stage = "delete_source"`: a source delete that was refused.
- GitHub Actions workflow running ruff and pytest on pull requests and `main`.

### Changed
- Fingerprints use `lstat`: a symlink contributes its own path and size, never its
  target's. **Units that contain symlinks get a new fingerprint**; with
  `resend_changed = true` and `delete_source = "never"` the sender re-sends them once
  after upgrading. Units without symlinks are unaffected. Upgrade both sides together
  when `ferry inventory` is used.
- The receiver retries a failing unit with exponential backoff (1 min to 1 h) and gives
  up after 8 attempts until restart, instead of re-hashing it on every poll.
- A closing transfer window no longer records a failure or triggers the retry backoff.
- `ferry send` / `ferry receive` print a text summary; `--json` selects JSON.
- Python 3.11.4 or newer is required (tarfile extraction filters).

### Fixed
- A dangling symlink in the source crashed the sender (and crash-looped under systemd).
- A symlink to a directory inside a unit made the receiver fail verification forever
  (member count mismatch); extraction now keeps link targets verbatim.
- The receiver trusted `key`, `filename` and member paths from the relay; they are now
  validated and cannot escape the destination.
- An acknowledgement is honoured only when it names the sha256 that was pushed, so a
  stale ack can no longer delete a source.
- A source delete refused by the path guard re-pushed the unit and duplicated ledger
  records; it is now recorded once and skipped.
- The ledger was re-parsed from disk on every query (quadratic over a long job); it is
  now indexed in memory.

## [0.1.0] - 2026-10-31

Initial release: resumable unit transfer through Baidu Pan, rclone remotes, GitHub draft
releases, rsync or a shared directory; sha256 verification of every part and payload;
source deletion policies; direct-egress guard; per-side bandwidth caps; host-wide
budget, transfer windows and daily quotas; configuration wizard; systemd/cron service
generation; documentation site.

[0.2.0]: https://github.com/daviddwlee84/ferry/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/daviddwlee84/ferry/releases/tag/v0.1.0
