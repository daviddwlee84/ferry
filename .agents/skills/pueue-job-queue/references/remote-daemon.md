# Driving a remote pueued

Pueue can't schedule *across* hosts (that's Slurm / K8s / Ray territory), but a
local `pueue` client **can** drive a `pueued` on another machine — and it gets
full control, not read-only status: `add`, `kill`, `log`, `follow` all work.

Whether you should is a different question. Read "Just SSH over instead" first.

> Everything below was verified against a real macOS client (pueue 4.0.4)
> driving a Linux `pueued` (4.0.2) over SSH unix-socket forwarding.

## Just SSH over instead

For most work, this is the correct answer and needs no setup at all:

```bash
ssh myhost 'pueue add --label train-seed1 -- ./train.sh'
ssh myhost 'pueue status --json' | jq .
```

The remote client runs on the remote box, so paths, environment, and logs are
all consistent. Nothing to configure, no secret to copy, no cert to sync.

Reach for a real remote connection only when you want **local composition** —
a `pueue status --json` you can pipe straight into `jq` without an SSH round
trip per call, or a script/agent that shells out to `pueue` many times.

Even then, the upstream wiki steers you away from exposing the daemon:

> "If that's not safe enough for your use-case, you can always listen on
> unix-sockets/localhost and do port/unix-socket forwarding via SSH."

So the recommended setup is **SSH forwarding**, below. Direct TCP exposure is
the last resort, not the default.

## Recommended: SSH unix-socket forwarding

The daemon keeps its default unix socket and **needs no reconfiguration** —
you change nothing on the server. Only the client gets a config.

### 1. Find the daemon's socket

```bash
ssh myhost 'ls /run/user/$(id -u)/pueue_*.socket'
# /run/user/1000/pueue_myuser.socket
```

`unix_socket_path: null` in the server's config means this default location.

### 2. Copy the shared secret

The client authenticates with the *same* secret file as the daemon:

```bash
mkdir -p ~/.config/pueue/remote && chmod 700 ~/.config/pueue/remote
ssh myhost 'cat ~/.local/share/pueue/shared_secret' > ~/.config/pueue/remote/shared_secret
chmod 600 ~/.config/pueue/remote/shared_secret
```

### 3. Write a client-only config

Keep it separate from your local config and select it with `-c`:

```yaml
# ~/.config/pueue/remote/client.yml
client:
  read_local_logs: false          # REQUIRED — see Gotchas
shared:
  use_unix_socket: true
  unix_socket_path: /home/you/.config/pueue/remote/remote.sock
  shared_secret_path: /home/you/.config/pueue/remote/shared_secret
```

### 4. Forward the socket, then talk to it

```bash
ssh -f -N -o ExitOnForwardFailure=yes \
    -L ~/.config/pueue/remote/remote.sock:/run/user/1000/pueue_myuser.socket \
    myhost

pueue -c ~/.config/pueue/remote/client.yml status
```

Add `-o ServerAliveInterval=30 -o ServerAliveCountMax=3` for a long-lived
tunnel, or manage it with `autossh` / a systemd user unit. Remove the stale
local socket file before re-forwarding.

Because the tunnel terminates at the daemon's own unix socket, **no TLS
certificate is involved** — the `daemon_cert` / TCP plumbing below only applies
if you skip SSH.

## Alternative: direct TCP + TLS

Only if SSH forwarding genuinely doesn't fit. Requires editing the server's
config and **restarting `pueued`**, which interrupts a running queue.

On the daemon: `use_unix_socket: false`, plus a reachable `host` and `port`.
Pueue self-signs a TLS certificate at startup; the client verifies against a
copy of it:

| Client key | Points at |
|---|---|
| `shared_secret_path` | copy of the daemon's `shared_secret` |
| `daemon_cert` | copy of the daemon's `daemon.cert` |
| `host` / `port` | the daemon's listen address |
| `use_unix_socket: false` | required |

The client "will only connect to a pueued daemon which serves the known
certificate", so the cert must be re-copied whenever the daemon regenerates it.

## Gotchas

- **`read_local_logs: false` is mandatory on a remote client.** Left at the
  default `true`, the client tries to read task logs off its *own* disk, where
  the remote daemon's log directory does not exist. Set it false and logs come
  back over the connection instead. `log` and `follow` were verified working
  this way.

- **The working directory is resolved on the CLIENT, and this is the thing
  that will bite you.** Pueue records a task's cwd at submit time and
  canonicalizes it locally. Three distinct failures, all observed:

  | You do | What happens |
  |---|---|
  | `pueue add -- ./x.sh` from a local dir | Local cwd is sent. On the daemon it doesn't exist → task ends `FailedToSpawn`, never runs. |
  | `--working-directory /home/you` (remote-only path) | Client **refuses to submit**: `Failed to canonicalize given working directory path`. |
  | `--working-directory /tmp` from macOS | Silently rewritten to `/private/tmp` (macOS symlink) → `FailedToSpawn` on Linux. |

  Only a path that exists **and canonicalizes identically on both machines**
  works — verified with `--working-directory /usr`, which ran correctly and
  returned the remote hostname.

  This makes cross-platform remote submission (macOS client → Linux daemon)
  impractical for real jobs, since your project paths won't exist locally.
  **`ssh myhost 'pueue add ...'` has none of this problem** and is the right
  tool for submitting. A forwarded client is still excellent for *reading*
  (`status --json`, `log`) and for `kill` / `pause` / `restart`.

- **A `FailedToSpawn` task is `Done`, not `Failed`.** Its result is
  dict-shaped — `{"FailedToSpawn": "<os error>"}` — not the bare string the
  older schema notes implied. `wait.py` now treats any terminal result that
  isn't `Success` as a failure, so this exits `5`; before the fix it exited
  `0` and a job that never started was reported as success.

- **Version mismatch warns but works.** A 4.0.4 client against a 4.0.2 daemon
  prints `Different protocol version detected '0.30.1'. Consider updating and
  restarting the daemon.` on every command and then behaves normally. Filter it
  out when parsing stderr; don't parse stdout for it (JSON stays clean).

- **The secret is a credential.** `shared_secret` is 512 bytes, mode `0640` on
  the server. Copy it over SSH, store it `0600`, and don't commit it. Anyone
  with the secret and a route to the socket can run arbitrary commands as the
  daemon's user.

- **Task ids are global to the daemon.** A remote client sees and can remove
  *everyone's* tasks in that queue, including a colleague's or your own
  long-running jobs. Prefer `--label` filtering, and be careful with
  `pueue clean` / `pueue reset` against a shared box.

## Quick verification

```bash
C=~/.config/pueue/remote/client.yml
pueue -c "$C" status                                  # remote queue?
pueue -c "$C" add --print-task-id --working-directory /usr -- 'hostname'
pueue -c "$C" log <id>                                # remote hostname => wired up
```

If `status` hangs, the tunnel is down. If it reports a secret mismatch,
re-copy `shared_secret`. If tasks land as `FailedToSpawn`, it's the working
directory — see above.
