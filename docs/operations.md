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
separate state, so inspect each on its own host. Commands accept `--json`, but
service output remains supervisor text rather than a general JSON report.

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

Preview a supervisor definition before installing anything:

```sh
ferry service job.toml --side receiver
ferry service job.toml --side sender --kind cron --schedule "0 2 * * *"
```

Systemd user units run follow mode with `Restart=on-failure`; cron uses one-shot
commands and job locks prevent overlap. `--install` writes and enables the selected
service, so it can start transfers immediately; `--uninstall` removes it. Systemd
may need `loginctl enable-linger "$USER"` to survive logout. On other supervisors,
including launchd, use the corresponding send/receive command explicitly. Test
manually with source retention before enabling any unattended destructive policy.
