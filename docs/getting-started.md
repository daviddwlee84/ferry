# Getting started

## Install

With [uv](https://docs.astral.sh/uv/) installed and Python 3.11+ available:

```sh
uv tool install git+https://github.com/daviddwlee84/ferry
ferry --help
```

Ensure uv's tool executable directory is on your `PATH`. The local adapter needs
no other tools or cloud account. Other adapters require their backend CLI and
separately configured authentication; never paste credential or session files
into a job, diagnostic report, or issue.

## Create a job interactively

Run the offline wizard in an interactive terminal:

```sh
ferry init
# Or choose a filename in an existing directory:
ferry init --output /absolute/path/job.toml
```

The wizard asks about your source and destination, unit grouping, backend, and
preferred run method. It previews the TOML and next commands before asking to
save. It refuses to overwrite an existing file or symlink; choose a new filename.
The default output is `job.toml` in the current directory.

Generated jobs retain source data (`delete_source = "never"`), require direct
egress, and default to keeping archives rather than extracting them. Backend
login remains a separate step. The wizard does not contact a backend, inspect
credentials, run doctor, transfer files, or install/start a service. Choosing
systemd or cron previews a definition and prints an explicit installation command
for you to review and run later.

For a preview without saving, use `ferry init --stdout`: prompts and next steps
go to stderr, and only the finished TOML goes to stdout. It still requires
interactive stdin. Save that output at the proposed path before using any printed
job or service commands. EOF or Ctrl-C cancels; no configuration is written before
confirmation. Shell redirection can create a file independently of the wizard.

Use absolute paths and distinguish sender-host paths from receiver-host paths.
The other host's directories do not need to exist on the machine running the
wizard. See [Configuration](configuration.md) for advanced limits, read windows,
and source-deletion policies. For a first end-to-end test, use the isolated local
demo below instead of an existing dataset.

## Try an isolated local relay

Run these commands in one POSIX shell. They create a fresh temporary directory
containing a non-empty sample file, job state, staging, relay, and destination.
No existing dataset or cloud service is involved.

```sh
demo=$(mktemp -d /tmp/ferry-demo.XXXXXX)
mkdir -p "$demo/source" "$demo/relay" "$demo/destination"
printf 'Hello from Ferry\n' > "$demo/source/hello.txt"

cat > "$demo/job.toml" <<EOF
name = "local-demo"
state_dir = "$demo/state"

[units]
root = "$demo/source"
glob = "*.txt"
pack = "none"

[sender]
stage_dir = "$demo/stage"
delete_source = "never"

[receiver]
dest = "$demo/destination"
extract = false

[remote]
adapter = "local"
prefix = "/local-demo"
egress = "direct"
options = { root = "$demo/relay" }
EOF

ferry doctor "$demo/job.toml"
ferry doctor "$demo/job.toml" --side receiver
ferry plan "$demo/job.toml"
ferry send "$demo/job.toml"
ferry receive "$demo/job.toml" --once
ferry status "$demo/job.toml" --side receiver
cmp "$demo/source/hello.txt" "$demo/destination/hello.txt"
printf 'Demo directory: %s\n' "$demo"
```

Both doctor checks should report `OK`; plan and receiver status should each show
one unit. `cmp` exits successfully without output when files match. The source
remains intact; verified relay payloads are removed, but protocol files may remain.
Keep the printed directory to inspect or repeat the job: using the same state
resumes recorded work rather than forcing a fresh copy. The demo directory remains.

## Move to two hosts

Use matching job names, unit definitions, and relay prefixes, with host-appropriate
paths and persistent state on each host. Start with a small disposable dataset and
`delete_source = "never"`; run doctor for each side before sending.
Choose a backend in [Configuration](configuration.md), then read
[Operations](operations.md) before enabling follow mode or source deletion.
