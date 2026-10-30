# pueued daemon setup and configuration

The `pueue` client is a thin RPC frontend; all state lives in `pueued`. If
the daemon isn't running, every `pueue` invocation fails with a connection
error. This page covers: getting `pueued` running per-OS, the config knobs
worth setting, where logs live, and how to run an isolated daemon for tests.

## Starting `pueued`

### Once-off

```bash
pueued -d
```

`-d` daemonizes (detaches from the terminal). Without `-d`, `pueued` runs in
the foreground — useful only for debugging.

### Auto-start on login

#### macOS (launchd)

Create `~/Library/LaunchAgents/io.github.nukesor.pueued.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.github.nukesor.pueued</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/pueued</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key>
    <string>/tmp/pueued.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/pueued.err.log</string>
</dict>
</plist>
```

Then:

```bash
launchctl load ~/Library/LaunchAgents/io.github.nukesor.pueued.plist
launchctl start io.github.nukesor.pueued
```

Adjust the `pueued` path if you installed via `cargo install` or a non-Homebrew prefix (`which pueued`).

#### Linux (systemd-user)

Create `~/.config/systemd/user/pueued.service`:

```ini
[Unit]
Description=pueue daemon
After=default.target

[Service]
Type=simple
ExecStart=%h/.cargo/bin/pueued
Restart=on-failure

[Install]
WantedBy=default.target
```

Adjust `ExecStart` to your actual `pueued` path. Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now pueued
journalctl --user -u pueued -f      # tail logs
```

#### Windows

Pueue ships a Windows service installer in newer releases. See
`https://github.com/Nukesor/pueue/wiki` for current instructions —
auto-start on Windows historically required a wrapper.

## Config file location

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/pueue/pueue.yml` |
| Linux | `~/.config/pueue/pueue.yml` (or `$XDG_CONFIG_HOME/pueue/pueue.yml`) |
| Windows | `%APPDATA%\pueue\pueue.yml` |

The file is auto-created on first daemon start with sensible defaults.
Override with `pueued -c /path/to/pueue.yml` or `PUEUE_CONFIG_PATH`.

## Important config knobs

```yaml
# pueue.yml
shared:
  pueue_directory: ~/Library/Application Support/pueue   # where state + logs live
  use_unix_socket: true                                  # macOS/Linux only

daemon:
  default_parallel_tasks: 1                              # initial slots for `default` group
  pause_group_on_failure: true                           # ← set this!
  pause_all_on_failure: false                            # nuclear: pauses every group on any failure
  callback: null                                         # optional shell command run on task done
  callback_log_lines: 10
  groups:
    default: 1                                           # parallelism per group
    ml: 4
    io: 2

client:
  read_local_logs: true
  show_confirmation_questions: false
  edit_cmd: ["vim", "-f"]                                # what `pueue edit` opens
  show_expanded_aliases: false
  status_time_format: "%H:%M:%S"
  status_datetime_format: "%Y-%m-%d %H:%M:%S"
```

### `pause_group_on_failure: true`

**Strongly recommended.** When any task in a group fails, the daemon pauses
the whole group — no new tasks start until you `pueue start --group G`.
This prevents a cascade of failures (e.g. all 30 sweep tasks failing
because of a missing env var) before you've noticed.

There is **no CLI flag** for this — it's config-only. After editing the
file, restart the daemon: `pueue shutdown && pueued -d`. Or `pueue reset`
to wipe state and reload.

### `default_parallel_tasks`

Slots for the `default` group on first daemon launch. Once the group
exists, this value is ignored — set per-group with `pueue parallel N
--group default` (or via the `groups:` block above).

### `groups:` block

Pre-declare groups + their parallelism. Equivalent to running `pueue group
add` + `pueue parallel` for each one. Useful for declarative
machine-provisioning (chezmoi, Ansible, etc.).

### `callback`

Shell command run after every task finishes. Receives `$PUEUE_TASK_ID`,
`$PUEUE_GROUP`, `$PUEUE_RESULT`, `$PUEUE_INPUT` (the command), and a few
other env vars. Use case: send a notification, post to Slack, append to
an audit log.

## Where logs live

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/pueue/logs/` |
| Linux | `~/.local/share/pueue/logs/` (or `$XDG_DATA_HOME/pueue/logs/`) |
| Windows | `%APPDATA%\pueue\logs\` |

Each task gets one file: `<task_id>.log`. Reading directly is faster than
`pueue log --json --full <id>` for huge logs (the JSON form loads
everything into memory).

## Isolated daemon for tests

To run pueued without disturbing your normal queue:

```bash
TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/state" "$TMPDIR/config"
cat > "$TMPDIR/config/pueue.yml" <<EOF
shared:
  pueue_directory: $TMPDIR/state
  use_unix_socket: true
daemon:
  default_parallel_tasks: 4
  pause_group_on_failure: true
EOF
PUEUE_CONFIG_PATH="$TMPDIR/config/pueue.yml" pueued -d
sleep 1
PUEUE_CONFIG_PATH="$TMPDIR/config/pueue.yml" pueue add -- echo isolated
```

The `tests/conftest.py` fixture in this skill uses this pattern.

## Stopping the daemon

```bash
pueue shutdown    # graceful
```

State persists. The next `pueued -d` resumes where you left off (queued
tasks remain queued, running tasks were killed during shutdown so they
typically need a `pueue restart --in-place`).

## Troubleshooting

- **"Connection refused" or "couldn't connect"** — `pueued` not running.
  `pueued -d`, or `scripts/check-daemon.sh --start`.
- **"Version mismatch"** — client and daemon versions differ. Restart the
  daemon (`pueue shutdown && pueued -d`).
- **Tasks stuck in `Queued` forever** — group might be paused. Check
  `pueue group --json` for `"status": "Paused"`. Resume with `pueue start
  --group G`.
- **`pueued -d` exits silently** — usually means the socket is held by an
  old/zombie daemon. Check `pgrep -lf pueued`; kill stragglers; remove the
  socket file under `pueue_directory/`.
- **Logs missing** — `pueue log <id>` falls back to in-memory if the file
  is gone. To preserve logs across `pueue clean`, copy them out of the log
  dir before cleaning.
