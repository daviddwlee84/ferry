# ferry

Resumable bulk transfer between hosts that cannot reach each other, relayed through cloud
storage (Baidu Pan, any rclone remote, a GitHub draft release, or a shared directory) — or
directly over ssh with rsync when the hosts can reach each other.

```
source host                         relay                          destination host
ferry send  ── pack ─▶ stage ─ push ─▶  prefix/<key>/part-*   ─ pull ─▶ verify ─▶ dest
                                         prefix/<key>/manifest.json  (READY, uploaded last)
```

- **Units**: one key = one tar (or one raw file) = one remote directory. Resume is per unit
  and per part; nothing is re-uploaded after a restart.
- **Integrity**: sha256 is computed while packing (single source read). The receiver checks
  every part, the reassembled payload and the tar member count. Backend checksums (e.g.
  Baidu MD5) are never trusted.
- **Backpressure**: staging cap on the sender, remote free-space floor and in-flight limit,
  so a small relay can move a much larger dataset.
- **Read windows**: packing (the heavy source read) can be blocked in given hours, e.g.
  `"Mon-Fri 09:15-15:30 Asia/Shanghai"`; uploads continue from staging.
- **Direct egress by default**: proxy variables are stripped from every backend process,
  backend-internal proxy settings are rejected, and endpoints resolving to clash/mihomo
  fake-ip or routed via a tun interface fail the preflight. See *Egress* below.
- **Source deletion policy**: `never`, `after_remote_size` (as soon as the relay holds a
  byte-identical-size copy; for regenerable data) or `after_receiver_ack` (after the
  destination verified sha256).

## Documentation

Read the [English documentation](https://daviddwlee84.github.io/ferry/) for a safe
local quickstart, interactive configuration wizard, configuration reference, and
operational guidance, including [recommended Pueue usage](docs/pueue.md). The
[Markdown sources](docs/index.md) are also available in this repository.

GitHub Actions checks documentation on pull requests and deploys the Zensical
site to GitHub Pages after documentation changes reach `main`. The workflow can
also be run manually on `main` to rebuild and deploy the site.

```bash
just docs-setup   # install the locked docs group alongside dev dependencies
just docs-build   # strict site build plus HTML/Markdown/LLM artifact checks
just docs-serve              # http://127.0.0.1:8000
just docs-serve 8769         # use another port if 8000 is occupied
just docs-serve 8769 0.0.0.0 # optional: expose preview on all interfaces
```

`docs-serve` takes an optional port followed by an optional host. Keep the
loopback default unless network access is intentional; binding `0.0.0.0` exposes
the preview to other hosts allowed by your firewall. Do not stop another service
just to reclaim port 8000.

Documentation tooling is not part of ferry's runtime dependencies.

## Install

```bash
uv tool install git+https://github.com/daviddwlee84/ferry   # CLI on PATH
# or as a dependency:  ferry @ git+https://github.com/daviddwlee84/ferry@v0.1.0
```

Python ≥ 3.11, no runtime dependencies. Backend CLIs are used as installed:
[BaiduPCS-Go](https://github.com/qjfoidnh/BaiduPCS-Go), `rclone`, `gh`.

## Job file

Create a config interactively without writing TOML by hand:

```bash
ferry init                         # preview, then save an XDG config named after the job
ferry init --output ./photos.toml   # opt into a project-local file
ferry init --stdout                # interactive prompts on stderr; TOML on stdout only
```

Each sync job has its own TOML. By default the wizard writes
`$XDG_CONFIG_HOME/ferry/jobs/<name>.toml`, using `~/.config/ferry/jobs/<name>.toml`
when `XDG_CONFIG_HOME` is unset, empty, or relative. The managed directory is
created only after you confirm saving. Explicit `--output` paths require an
existing parent directory. Use distinct job names, state/staging directories,
and relay prefixes for independent syncs; existing configs are not migrated.
Commands still take an explicit job path, as printed by the wizard.

The wizard supports all five backends and both unit-selection methods. It keeps
sources by default, refuses existing output files, and never reads credentials,
contacts a backend, starts transfers, or installs services. Systemd/cron choices
preview the existing service definitions and print explicit install commands.
See the [wizard walkthrough](docs/getting-started.md#create-a-job-interactively).

For manual configuration, the advanced example below demonstrates optional settings.
**It deletes sources after upload size checks**; use `delete_source = "never"` for
a pilot, as the wizard does, and review the [deletion policies](docs/operations.md#source-deletion).

```toml
name = "daily-archive"                        # also the default state dir name
# state_dir = "~/.local/state/ferry/<name>"   # ledgers, logs, lock

[units]
root = "/data/shared/dataset"
keys_from = "_manifests/*.json"               # key = file stem / dir name
members = ["order/{key}", "trans/{key}", "_manifests/{key}.json"]
# or: glob = "exports/*.parquet"  (each match is a unit); pack = "none" sends files as-is
settle = "6h"                                 # skip units with files newer than this
resend_changed = true                         # re-send units whose fingerprint changed
recheck_recent = 30                           # after the first pass, recheck newest 30 keys

[sender]
stage_dir = "~/.cache/ferry/daily-archive"
stage_cap = "300GB"
read_blocked = ["Mon-Fri 09:15-15:30 Asia/Shanghai"]
delete_source = "after_remote_size"           # never | after_remote_size | after_receiver_ack

[receiver]
dest = "/srv/archive/dataset/{key[0:4]}"      # {key} and slices like {key[0:4]}
# extract = true                              # unpack; each unit path is replaced whole
# min_free = "20GB"                           # wait while the destination is this full

[remote]
adapter = "baidupcs"                          # local | baidupcs | rclone | ghrelease
prefix = "/ferry/daily-archive"
part_size = "3900MiB"                         # default per adapter
min_free = "200GB"
max_inflight = "1.5TB"
egress = "direct"                             # direct | any
# trusted_interfaces = ["Meta"]               # TUN you verified routes this backend DIRECT
options = { download_threads = 4 }
```

Adapter options:

| adapter | options |
|---|---|
| `local` | `root` (directory used as relay) |
| `baidupcs` | `binary` (default: PATH or `~/.local/bin/BaiduPCS-Go`), `upload_threads`, `download_threads` (4) |
| `rclone` | `remote` (`"gdrive:"`, `"s3:bucket/relay"`), `flags`, `endpoints` (route check hosts) |
| `ghrelease` | `repo` (`owner/name`), `tag` (draft release, created if missing; refuses a published release) |
| `rsync` | `host` (ssh alias; `""` = local), `root` (landing dir on that host), `ssh_options`, `flags` |

`[remote.sides.sender]` / `[remote.sides.receiver]` override `adapter` and `options` for one
side. Direct mode uses this: the sender pushes with `rsync` into a landing directory on the
destination host, and the receiver there reads the same directory with `local`:

```toml
[remote]
adapter = "rsync"
prefix = "/inbox"
options = { host = "nas", root = "/srv/landing" }
[remote.sides.receiver]
adapter = "local"
options = { root = "/srv/landing" }
```

Compared with plain `rsync -a`, this adds read windows, many-small-files packing, sha256
verification of every unit, source-deletion policies, ledgers with throughput/ETA, and
change detection for long-running mirrors.

## Backfill and increments are the same job

A job with `delete_source = "never"` mirrors a growing, occasionally repaired source:

- **First run = backfill**: every unit not yet sent is packed and sent.
- **`--follow` = increments**: new keys are picked up; units still being written are held
  back by `settle`.
- **Repairs**: each unit has a fingerprint (sorted relative paths + sizes; stat only). A
  changed unit is re-sent as a new revision; with `extract = true` the receiver replaces
  each unit path wholesale, so files deleted at the source disappear too. After the first
  pass only the newest `recheck_recent` keys are re-fingerprinted each round; restart the
  sender (or run without `--follow`) for a full recheck.
- **Seeding**: if the destination already holds part of the data, run
  `ferry inventory job.toml` on the receiver. It publishes fingerprints of what it has;
  the sender marks identical units as `seeded` instead of sending them.

## Commands

```bash
ferry init [--output PATH] [--stdout]     # offline interactive configuration wizard
ferry doctor job.toml [--side receiver]   # backend login, proxy settings, routes
ferry plan job.toml                       # units found under the source root
ferry send job.toml [--follow]            # until every unit is pushed (then marks done)
ferry receive job.toml [--once|--follow]  # until the sender is done and the relay is empty
ferry inventory job.toml [--root PATH]     # receiver: publish fingerprints for seeding
ferry status job.toml [--side receiver] [--scan-source] [--remote] [--json]
```

A second sender (or receiver) for the same job on the same host exits immediately.

### Running unattended

ferry has no daemon of its own: `send`/`receive --follow` is a long-running loop that
polls for new or changed units. Put it under a supervisor; `ferry service` prints (or,
with `--install`, writes and enables) one:

```bash
ferry service /absolute/job.toml --side receiver          # preview a systemd user unit
ferry service /absolute/job.toml --side receiver --install # install, enable, and start now
ferry service /absolute/job.toml --side receiver --uninstall
ferry service /absolute/job.toml --side sender --kind cron --schedule "0 2 * * *"
ferry service /absolute/job.toml --side sender --kind cron --schedule "0 2 * * *" --install
ferry service /absolute/job.toml --side sender --kind cron --uninstall
```

systemd units run `--follow` with `Restart=on-failure`; cron entries run one-shot
commands (`send`, `receive --once`). Overlapping instances of the same local
job/side exit 2, rather than running additional workers. A one-shot sender can
still wait on receiver acknowledgements or relay space, so arrange an independent
receiver. `receive --once` is one scan, not proof that a whole batch arrived.

Review the [unattended-operation guide](docs/operations.md#unattended-operation)
for backend `PATH`, authentication, logs, and systemd lingering. Enabling linger
is a separate host-policy decision, not something Ferry does automatically.
For [Pueue](docs/pueue.md), prefer queued sender batches plus a service-managed
receiver; do not put a receiver behind a sender that needs it to make progress.
Ferry has no launchd or Pueue installer. Do not let multiple supervisors own the
same job/side.

Library use (e.g. from an orchestrator):

```python
import ferry
job = ferry.load_job("job.toml")
report = ferry.preflight(job, ferry.adapter_for(job, "sender"))
result = ferry.send(job)            # SendResult(pushed=..., pushed_bytes=..., ...)
st = ferry.side_status(job, "sender", remote=True)
```

## Egress

Proxy traffic is metered, so by default ferry insists on a direct path:

1. `http(s)_proxy`, `all_proxy`, ... are removed from every backend process environment.
2. Backend tools' own proxy settings (BaiduPCS-Go `proxy`) are reported.
3. Each backend endpoint is resolved; a `198.18.0.0/15` answer (clash/mihomo fake-ip DNS)
   or a route via `tun*`/`utun*`/`Meta`/`wg*`/`tailscale*` is reported.

With `egress = "direct"` any finding fails `send`/`receive`; `egress = "any"` (or
`--allow-proxy` for one run) accepts them with warnings — for backends that are only
reachable through a proxy, e.g. Google Drive from mainland China, where the proxy's own
rules may still pick DIRECT for some hosts. A transparent proxy on the router is not
visible from the host and cannot be detected.

## Backend notes

See [`pitfalls/`](pitfalls/) — e.g. Baidu silently drops empty files and exits 0 on
errors, single-threaded Baidu downloads are ~4x slower, GitHub assets are limited to 2 GiB.
