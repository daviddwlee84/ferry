# Operations

## Inspect before transferring

```sh
ferry doctor job.toml
ferry doctor job.toml --side receiver
ferry plan job.toml
ferry status job.toml
ferry status job.toml --side receiver --json
ferry status job.toml --scan-source --remote
```

Doctor checks the selected backend and egress; it can contact remote services
and, for rsync, create a missing landing directory. It is not a generic dry-run
or proof that every later backend operation will succeed. Plan enumerates source
units and members; it does not simulate transfer, settling, or source deletion.

Status normally reads local ledgers. `--scan-source` adds source enumeration;
`--remote` queries the backend for relay information. Sender and receiver have
separate state, so inspect each on its own host. Inspection and transfer commands
accept `--json`. Service output remains supervisor text; `init` produces TOML,
not a JSON report.

## Run and resume

```sh
ferry send job.toml
ferry receive job.toml --once
```

Send processes eligible work and marks the sender done. Receive with `--once`
makes one pass; without it, receive waits until the sender is done and the relay
is empty. For continuous operation, run `send --follow` and `receive --follow`
in separate processes, adding `--poll 60` to control idle polling in seconds.

To resume, restart the same command with the same job, state, staging, and relay
prefix. Do not delete ledgers, staged manifests, or destination partial files to
"fix" an interrupted transfer. Uploaded parts with matching remote sizes can be
reused; downloaded parts are checked by SHA-256 before reuse. One sender and one
receiver may run per job on a host; a second instance of either side fails fast.

Inspect `<state_dir>/sender.jsonl`, `receiver.jsonl`, and side logs when progress
stalls. Check read windows, settling, staging space, remote limits, destination
free space, and preflight errors. Keep credentials and session files out of reports.

## Integrity and destination safety

The sender hashes the packed payload and its parts. The receiver checks sizes,
part and whole-payload SHA-256 hashes, and tar member counts before recording
verification. It then removes that unit's relay data. With extraction enabled,
existing unit paths are replaced, including removal of files absent in the new copy.

Change detection is weaker than transfer verification: its path-and-size
fingerprints miss same-size edits. `ferry inventory job.toml --no-upload --json`
previews fingerprints of an existing destination tree; omit `--no-upload` only
when you intend to publish them to the relay for sender-side skipping. Use
`--root PATH` for a source-shaped tree when `receiver.dest` contains a key template.
Inventory seeding is not a content-hash audit or evidence of a fresh transfer.

## Source deletion

- `never` is the default and the recommended starting point.
- `after_remote_size` deletes source members after upload size checks, **before
  destination SHA-256 verification**. Use only for independently recoverable data.
- `after_receiver_ack` waits for the receiver's verification acknowledgement.
  Keep the sender running or restart it to consume acknowledgements; one-shot
  sending can wait for the receiver rather than exiting immediately.

Keep source units immutable throughout transfer and acknowledgement handling:
settling does not lock files, and deletion acts on source paths. Use trusted,
job-specific relay storage; do not fabricate acknowledgements or reuse old
protocol state for a different dataset. Neither deletion policy replaces backups.

## Egress

Backend subprocesses always have proxy environment variables stripped, including
when `egress = "any"`. The default `direct` policy rejects detected backend proxy
settings, fake-IP DNS in `198.18.0.0/15`, and routes through recognized tunnel
interfaces. Backend-internal proxy configuration and host routing still matter.

Set `remote.egress = "any"`, or pass `--allow-proxy` to doctor/send/receive, only
when accepting those egress findings is intentional. This does not restore proxy
environment variables or fix missing authentication. `trusted_interfaces` exempts
interfaces you have independently verified route this backend directly. Host
checks cannot detect every transparent upstream proxy; verify cost-sensitive paths.

## Unattended operation

Ferry has no daemon of its own. `--follow` runs a foreground loop; a supervisor
keeps that process alive. The wizard can suggest a run method, but it never
installs or starts one.

| Need | Recommended owner |
| --- | --- |
| Continuously discover or receive units | systemd user service, or another service manager |
| Queue a batch and detach from the terminal | [Pueue](pueue.md), with an independent receiver |
| Run on a recurring calendar schedule | cron, or a manually managed timer |
| Watch a foreground run interactively | a terminal or tmux; not automatic restart after reboot |

Use **one owner for each job/side**. Do not run the same sender under both pueue
and systemd, for example. A second process using the same state directory and
side exits **2**; the lock prevents local overlap, not duplicate work across
hosts or different state directories. Sender and receiver may run concurrently.
Test manually with source retention before enabling unattended operation.

### Environment and authentication

Install Ferry and the required backend tools on each execution host. Use an
absolute job filename and absolute filesystem paths in the job. Run the service
as the same user whose backend login, SSH configuration, and persistent state
it should use. Noninteractive SSH must work without password prompts.

Supervisors do not necessarily load your interactive shell configuration.
Generated entries locate the Ferry executable, but do not copy your interactive
`PATH` for backend tools. Ensure `HOME` and the backend search path are correct.
Use backend executable options where supported, or configure the supervisor's
environment explicitly. Do not put authentication tokens or session contents in
service definitions, job files, or log reports.

### Systemd user service

The following examples assume the job has `name = "mirror"`. Run them on the
host that will execute the selected side, replacing paths as needed.

```sh
# Preview only: no service changes or loginctl probe.
ferry service /home/alex/jobs/mirror.toml --side receiver

# Explicitly install, enable, and start receiving now.
ferry service /home/alex/jobs/mirror.toml --side receiver --install

systemctl --user status ferry-mirror-receiver.service
journalctl --user -u ferry-mirror-receiver.service -f

# Stop, disable, and remove this generated unit.
ferry service /home/alex/jobs/mirror.toml --side receiver --uninstall
```

The unit lives under `~/.config/systemd/user/`, runs `receive --follow` (or
`send --follow` for the sender side), and uses `Restart=on-failure` with a
60-second restart delay. Installation can immediately begin transfers; inspect
the job and preview first. For a continuously running sender, install its sender
unit on the source host as well. For queued sender batches, do not install that
sender unit; use the [pueue recipe](pueue.md) instead.

For example, a user override created with
`systemctl --user edit ferry-mirror-receiver.service` can set a reviewed backend
search path:

```ini
[Service]
Environment="PATH=/home/alex/.local/bin:/usr/local/bin:/usr/bin:/bin"
```

Adjust it for the actual tool locations. Reload the user manager with
`systemctl --user daemon-reload`, then deliberately restart the affected service
to apply an environment change; restarting interrupts any current transfer.
Keep the same job and state when resuming.

User services need lingering to run independently of login sessions and start
at boot. Installation warns if lingering is detected as disabled; Ferry does not
enable it. If host policy permits, explicitly run:

```sh
loginctl enable-linger "$USER"
```

This may require administrator approval. Some older or restricted hosts have no
usable systemd user manager; do not assume `--user` or lingering is available.
Use cron for periodic work or an available service manager for continuous work.
A successful install is not a guarantee of connectivity after the next reboot.

### Cron

Preview first, then explicitly install the same job/side entry:

```sh
ferry service /home/alex/jobs/mirror.toml --side sender --kind cron --schedule "0 2 * * *"
ferry service /home/alex/jobs/mirror.toml --side sender --kind cron --schedule "0 2 * * *" --install

# Remove only Ferry's tagged entry for this job and side.
ferry service /home/alex/jobs/mirror.toml --side sender --kind cron --uninstall
```

Cron entries are tagged by job name and side; installing again replaces that
entry and preserves unrelated entries. The sender runs without `--follow`; a
receiver cron entry uses `receive --once`. `--once` means a single relay scan,
not guaranteed batch completion: an unfinished upload or insufficient destination
space can leave work behind. Cron provides periodic attempts, not a persistent
receiver process.

A non-follow sender can still wait indefinitely for retries, read windows,
capacity, or receiver acknowledgements. Schedule or supervise the receiver
independently when needed. Later cron invocations of an already-running side
exit 2 rather than creating extra workers. The generated line discards process
stdout/stderr; inspect `<state_dir>/sender.log` or `receiver.log` and the ledgers
for transfer activity. Early startup errors may occur before those logs exist,
so validate the command manually in the intended environment first.

Cron often has a minimal `PATH`. Configure it through a reviewed crontab
environment (which can affect other entries), and verify the host's cron timezone
when choosing a schedule. Do not assume shell aliases, shell initialization, or
an interactive SSH agent will be available.

### Other supervisors

Ferry does not install launchd jobs, systemd timers, or pueued. Use the appropriate
service manager for a persistent `send --follow` or `receive --follow` process.
For finite work, see [Pueue](pueue.md); persisting queue metadata is different
from automatically resuming a process interrupted by a reboot.
