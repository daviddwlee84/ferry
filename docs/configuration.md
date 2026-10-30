# Configuration

A job is a TOML file passed to each CLI command. Start with the complete
[local quickstart](getting-started.md), then change one setting at a time.
Use absolute filesystem paths for predictable behavior under supervisors.

## Job identity and units

`name` identifies the job; use letters, digits, dots, underscores, and hyphens,
starting with a letter or digit. `state_dir` defaults to
`~/.local/state/ferry/<name>`. Keep it persistent and separate from source data.
Use a dedicated remote prefix for each job, not a shared production directory.

`[units]` requires `root` and exactly one selection method:

- `glob = "exports/*.parquet"`: each match is a unit, keyed from its basename.
  Avoid matches whose names collapse to the same key.
- `keys_from = "_manifests/*.json"` plus
  `members = ["data/{key}", "_manifests/{key}.json"]`: derive each key from a file
  stem or directory name, then collect existing paths from the templates.
  Missing member paths are omitted; inspect `ferry plan` before sending.

`pack = "tar"` is the default. `pack = "none"` requires exactly one regular file
per unit. Empty raw payloads are rejected; use tar when transporting empty files.
Keep unit paths non-overlapping, especially when extracting or deleting sources.

| Optional unit setting | Behavior |
| --- | --- |
| `settle = "6h"` | Skip units whose newest file is younger than this; default is no delay. |
| `resend_changed = true` | Default; resend changed fingerprints when source deletion is `never`. |
| `recheck_recent = 30` | In follow mode, limit repeat checks to the last 30 sorted keys; unset checks all. |

Fingerprints use relative paths and file sizes, **not content hashes**. Same-size
edits can go unnoticed. Restarting the sender performs a full fingerprint scan,
not a full content comparison. Settling is a heuristic, not a filesystem snapshot.

## Sender and receiver

| Section / setting | Purpose |
| --- | --- |
| `sender.stage_dir` | Local staging; defaults to `<state_dir>/stage`. |
| `sender.stage_cap = "50GB"` | Pause new packing when staged data reaches the cap; not a hard disk quota. |
| `sender.read_blocked = ["Mon-Fri 09:15-15:30 Asia/Shanghai"]` | Block starting new packing during these windows; uploads continue. |
| `sender.delete_source = "never"` | Default; retain sources. See [deletion policies](operations.md#source-deletion). |
| `receiver.dest` | Required for receiving; supports `{key}` and slices such as `{key[0:4]}`. |
| `receiver.extract = false` | Default; keep tar archives. Raw files are placed under their payload filenames. |
| `receiver.min_free = "20GB"` | Require this floor plus about twice the payload size before receiving. |

With `extract = true`, tar member paths are extracted beneath `dest`; affected
unit paths are replaced wholesale. Use a dedicated destination, not directories
containing unrelated files. Leave room beyond the staging cap for a whole unit.

## Relay and adapter selection

`[remote]` requires `adapter` and `prefix`. Sizes accept strings such as `50GB`
or `3900MiB`. Optional `part_size` controls splitting; defaults depend on the
adapter. `min_free` is enforced only when the adapter can report free space.
`max_inflight` limits outstanding payload bytes tracked for the job; allow at
least the largest whole unit, or sending can wait indefinitely.

| Adapter | Main `options` | Requirements and limits |
| --- | --- | --- |
| `local` | `root` | Existing directory; no backend CLI. Both sides must see the same relay contents. |
| `baidupcs` | `binary`, `upload_threads`, `download_threads` | Authenticated BaiduPCS-Go; default parts are 3900 MiB, download threads 4. |
| `rclone` | `remote`, `flags`, `endpoints` | Configured rclone remote, e.g. `gdrive:`. Set endpoint hostnames if inference fails. |
| `ghrelease` | `repo`, `tag` | Authenticated `gh`; creates a missing draft release and refuses a published one. Default parts 1900 MiB, each strictly below 2 GiB. |
| `rsync` | `host`, `root`, `ssh_options`, `flags` | rsync and, for a remote host, working noninteractive SSH; empty `host` means local. |

Ferry requires non-empty remote objects and uses its own SHA-256 verification,
not backend checksums. Backend quotas and permissions still apply.

`[remote.sides.sender]` and `[remote.sides.receiver]` can override `adapter` and
`options`. An overridden options table **replaces**, rather than merges with,
the base options. For direct SSH delivery, send via rsync and receive locally:

```toml
[remote]
adapter = "rsync"
prefix = "/inbox"
options = { host = "nas", root = "/srv/landing" }

[remote.sides.receiver]
adapter = "local"
options = { root = "/srv/landing" }
```

This is a fragment, not a complete job. The receiver runs on `nas` and reads the
same landing directory. `egress = "direct"` is the default; see
[egress controls](operations.md#egress) before accepting proxy routes.

## Example jobs

Adapt paths and review policies before using these public repository examples:

- [Incremental mirror](https://github.com/daviddwlee84/ferry/blob/main/examples/mirror-incremental.toml): retains sources and extracts at the destination.
- [Baidu archive](https://github.com/daviddwlee84/ferry/blob/main/examples/daily-archive-baidu.toml): **deletes sources after remote size checks**; change to `never` for a pilot.
- [Google Drive](https://github.com/daviddwlee84/ferry/blob/main/examples/gdrive-via-proxy.toml): explicitly accepts non-direct egress.
- [GitHub draft release](https://github.com/daviddwlee84/ferry/blob/main/examples/github-draft-release.toml): raw-file units; replace the repository placeholder.
