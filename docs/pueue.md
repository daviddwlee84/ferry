# Pueue

[Pueue](https://github.com/Nukesor/pueue) is an optional shell-task queue backed
by the `pueued` daemon. It can keep a submitted command running after you detach
from the terminal, label jobs, capture their output, and limit parallel tasks.
It is not a Ferry dependency, a distributed scheduler, or a replacement for
Ferry's integrity checks and resumable state.

The examples use Pueue 4.0.2 command syntax. Install Pueue separately and manage
`pueued` with the execution host's service manager; consult the
[upstream documentation](https://github.com/Nukesor/pueue/wiki) for setup.
Ferry does not install or start it. Run `pueue status` to confirm your intended
daemon is reachable before submitting anything.

## Recommended arrangement

- **Receiver host:** a service-managed `ferry receive ... --follow` keeps draining
  the relay, verifying units, and publishing acknowledgements when required.
- **Sender host:** Pueue queues non-follow `ferry send ...` batches in a dedicated
  group with controlled concurrency.
- **Each job/side has one owner:** do not also install a sender service for the
  same queued sender, or run a second receiver while its service is active.

For continuous discovery on both sides, use service-managed `--follow` processes
instead of queue tasks expected to finish. Pueue groups limit the number of
whole Ferry processes, not Ferry's internal upload threads or parts.

A non-follow sender is a batch command, **not a bounded-duration command**. It
can wait indefinitely for upload retries, source read windows, staging/relay
capacity, or receiver acknowledgements. An apparently stuck Running task needs
Ferry diagnostics, not necessarily another queued copy.

## Prepare the receiver

On the receiver host, review the generated job, backend access, destination,
and [service environment](operations.md#environment-and-authentication). The
examples assume `name = "mirror"` and host-appropriate copies of the job.
Start with `delete_source = "never"`.

```sh
ferry doctor /home/alex/jobs/mirror.toml --side receiver
ferry service /home/alex/jobs/mirror.toml --side receiver
# Explicitly install and start the receiver after reviewing the preview.
ferry service /home/alex/jobs/mirror.toml --side receiver --install
systemctl --user status ferry-mirror-receiver.service
```

Doctor can contact the backend; it is not an offline validation command.
Installation starts receiving immediately. On hosts without a usable systemd
user manager, run the receiver under an available service manager, or in a
separate foreground terminal for a supervised trial. Do not put that receiver
behind the sender in a queue dependency chain.

## Queue a sender batch

On the sender host, first inspect source units and backend access:

```sh
ferry doctor /home/alex/jobs/mirror.toml --side sender
ferry plan /home/alex/jobs/mirror.toml
```

Create the dedicated group once. Pueue does not create missing groups when you
submit a task; if the group already exists, inspect it rather than recreating it.

```sh
pueue group add ferry-send
pueue parallel 1 --group ferry-send
```

Submit from a stable working directory, using absolute executable and job paths:

```sh
cd "$HOME"
ferry_bin=$(command -v ferry)
job=/home/alex/jobs/mirror.toml

sender_id=$(pueue add --group ferry-send --label send-mirror-batch01 \
  --print-task-id --escape -- "$ferry_bin" send "$job")
printf 'Sender task: %s\n' "$sender_id"
```

Verify that `command -v ferry` returns an absolute executable path, not an alias
or shell function, before submitting. `--escape` preserves each argument,
including paths containing spaces. Without it, Pueue joins arguments into a
shell command and quoting can be lost. Do not combine this escaped-argument
form with shell operators such as `&&`; they would be literal arguments.

The job, state, staging, and working directory must remain available for queued
runs and retries. Ensure backend binaries and noninteractive authentication work
in the task environment; Ferry's own executable path does not fix the backend
`PATH`. Use distinguishable labels for additional batches. Keep this group's
parallelism at one unless you intentionally want concurrent **different** jobs
and have enough disk, relay capacity, and bandwidth.

## Inspect, verify, and retry

On the sender host:

```sh
pueue status
pueue log "$sender_id"
pueue follow "$sender_id"      # Ctrl-C detaches from the log view
ferry status /home/alex/jobs/mirror.toml --side sender
```

Check delivery separately on the receiver host:

```sh
ferry status /home/alex/jobs/mirror.toml --side receiver --json
journalctl --user -u ferry-mirror-receiver.service -f
```

Compare the expected units with the receiver's verified records and, when
appropriate, the destination contents. Status totals can include previous
batches; the sender's pushed count alone is not proof of receiver verification.

`pueue wait "$sender_id"` waits for a terminal task state; its exit status does
not distinguish task success from failure. Inspect `pueue status --json` or the
task log afterward. Even a successful Ferry exit is not an unconditional promise
that your intended batch arrived:

- `receive --once` scans the relay once; incomplete uploads or insufficient
  destination space can leave units behind without failing that pass.
- A sender can skip units that have not settled yet.
- Current SIGTERM handling can stop Ferry and return 0 with work still incomplete.

After diagnosing a failure, rerun the same job with the same persistent state.
Pueue can resubmit the command:

```sh
pueue restart "$sender_id"
```

By default this creates a new task ID, preserving the old result and log. Use
`pueue restart --in-place "$sender_id"` only when intentionally reusing the ID
and overwriting its previous log. Inspect whether the group is paused; resume
it deliberately with `pueue start --group ferry-send` if appropriate. Do not
submit another copy while the original sender is still running.

Do not delete ledgers, staging, relay protocol files, or destination partials to
force a retry. Pueue reruns the process; Ferry's state provides transfer resume.

## Dependencies and concurrency traps

**Do not make the receiver depend on the sender finishing.** `pueue add --after`
requires every dependency to succeed. With `after_receiver_ack`, the sender
waits for receiver verification before exiting; with relay backpressure, it may
need the receiver to free space before it can upload the remaining units.
Putting the receiver after the sender deadlocks those workflows. Putting both
in a shared one-slot group has the same problem.

Sequential send-then-receive is appropriate only for a small, static,
source-retaining batch that can fit entirely in the relay without waiting for
receiver activity. It is not the general recommendation. A one-pass receiver
still needs a separate completion check.

For repeated batches, prefer the continuously running receiver above. Ferry's
sender-done marker persists after a batch. A non-follow receiver started before
the next sender can see that old marker and an empty relay, then exit too early.
Do not delete protocol state as a scheduling workaround.

Ferry's per-side lock applies to the local state directory. A duplicate side
using that state exits 2; separate state directories or hosts do not share a
distributed lock. Pueue group limits also apply only to that daemon, so neither
mechanism makes duplicate cross-host senders safe.

## Remote hosts, reboot, and recurring schedules

Submit on the host that will execute the command. From another machine, SSH in
and run the Pueue client there, for example:

```sh
ssh sender-host 'pueue add --group ferry-send --label send-mirror-remote --escape -- /home/alex/.local/bin/ferry send /home/alex/jobs/mirror.toml'
ssh sender-host 'pueue status'
```

Create the group and prepare that host's environment first. Pueue resolves the
submission working directory on the client: using a local client against a
remote daemon can pass a local-only or differently canonicalized path. Running
the client through SSH avoids that mismatch. Task IDs and dependencies belong
to one daemon; do not use a receiver host's task ID in a sender host's `--after`.

Pueue persists queue state, but it does not checkpoint a running Ferry process.
After a host reboot or daemon shutdown, inspect interrupted tasks and restart
them with the same Ferry state as needed. Automatic daemon startup depends on
your service-manager setup; configuring the queue is not itself boot setup.

Pueue's delay option is for a single delayed task, not a recurring calendar
schedule. Use [cron or a timer](operations.md#cron) for periodic runs, and a
service manager for long-lived `--follow` processes. Repeatedly adding tasks on
a timer can build an unbounded queue if a sender is still waiting, so do not
blindly enqueue a new batch on every tick.
