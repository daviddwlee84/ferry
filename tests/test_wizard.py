import io
import threading
import tomllib
from pathlib import Path

import pytest

from ferry import adapters, cli, core, proc, service, wizard
from ferry.job import job_from_dict, load_job
from ferry.receiver import receive
from ferry.sender import send


class Terminal(io.StringIO):
    def isatty(self):
        return True


def answers(tmp_path, *, backend="local", selection="glob", pack="tar", side="sender",
            method="manual", confirm="yes", extract="no", local_receiver="yes"):
    values = ["sample", str(tmp_path / "source"), str(tmp_path / "state"),
              str(tmp_path / "stage"), selection]
    if selection == "glob":
        values += ["*.txt"]
    else:
        values += ["_manifests/*.json", "data/{key}", "_manifests/{key}.json", ""]
    values += [pack, str(tmp_path / "destination")]
    if pack == "tar":
        values += [extract]
    values += [backend]
    values += {"local": [str(tmp_path / "relay")],
               "baidupcs": ["BaiduPCS-Go"],
               "rclone": ["gdrive:"],
               "ghrelease": ["owner/repository", "ferry-sample"],
               "rsync": ["nas", "/srv/landing", local_receiver]}[backend]
    values += ["/ferry/sample", side, method]
    if method == "cron":
        values += ["0 2 * * *"]
    return values + [confirm]


def invoke(tmp_path, values, *, to_stdout=False, target=None):
    target = target or tmp_path / "job.toml"
    stdin = Terminal("\n".join(values) + "\n")
    stdout, stderr = io.StringIO(), io.StringIO()
    code = wizard.run(str(target), to_stdout=to_stdout, stdin=stdin, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue(), stdin


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("wizard/preview must not contact a backend or supervisor")

    for module, names in ((adapters, ("make_adapter",)),
                          (core, ("make_adapter", "adapter_for", "preflight")),
                          (cli, ("adapter_for", "preflight", "send", "receive")),
                          (proc, ("run", "run_transfer")),
                          (service, ("install_systemd", "install_cron", "uninstall_systemd",
                                     "uninstall_cron", "linger_enabled", "_systemctl")),
                          (service.subprocess, ("run", "Popen"))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)


@pytest.mark.parametrize("backend", ["local", "baidupcs", "rclone", "ghrelease", "rsync"])
def test_backend_configs_are_valid_offline(tmp_path, offline, backend):
    code, stdout, stderr, stdin = invoke(tmp_path, answers(tmp_path, backend=backend))
    assert code == 0 and stdout == ""
    assert stdin.read() == ""
    job = load_job(tmp_path / "job.toml")
    assert job.remote.adapter == backend
    assert job.sender.delete_source == "never" and job.remote.egress == "direct"
    assert not job.receiver.extract
    assert "No transfer or service was started" in stderr
    if backend == "rsync":
        assert job.remote.for_side("receiver") == ("local", {"root": "/srv/landing"})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["job.toml"]


def test_accept_defaults(tmp_path, offline):
    values = ["", "/source", "", "", "", "", "", "/destination", "", "", "/relay",
              "", "", "", "yes"]
    assert invoke(tmp_path, values)[0] == 0
    data = tomllib.loads((tmp_path / "job.toml").read_text())
    assert data["name"] == "my-job"
    assert data["state_dir"] == "~/.local/state/ferry/my-job"
    assert data["sender"]["stage_dir"] == "~/.local/state/ferry/my-job/stage"
    assert data["units"] == {"root": "/source", "glob": "*", "pack": "tar"}


@pytest.mark.parametrize("selection,pack", [("glob", "tar"), ("glob", "none"), ("keys", "tar")])
def test_unit_selection_and_packing(tmp_path, offline, selection, pack):
    code, _, _, _ = invoke(tmp_path, answers(tmp_path, selection=selection, pack=pack))
    assert code == 0
    job = load_job(tmp_path / "job.toml")
    assert job.units.pack == pack
    if selection == "keys":
        assert job.units.keys_from == "_manifests/*.json"
        assert job.units.members == ("data/{key}", "_manifests/{key}.json")
        assert job.units.glob is None
    else:
        assert job.units.glob == "*.txt" and job.units.keys_from is None


def test_optional_extraction_and_remote_receiver(tmp_path, offline):
    values = answers(tmp_path, backend="rsync", extract="yes", local_receiver="no")
    code, _, stderr, _ = invoke(tmp_path, values)
    assert code == 0
    job = load_job(tmp_path / "job.toml")
    assert job.receiver.extract and not job.remote.sides
    assert "replaces affected destination paths" in stderr


def test_rsync_local_landing_has_no_override(tmp_path, offline):
    values = answers(tmp_path, backend="rsync")
    start = values.index("nas")
    values[start:start + 3] = ["", "/srv/local"]
    assert invoke(tmp_path, values)[0] == 0
    job = load_job(tmp_path / "job.toml")
    assert job.remote.options == {"host": "", "root": "/srv/local"}
    assert job.remote.sides == {}


def test_toml_unicode_and_escaping_roundtrip():
    value = 'quotes " slash \\ newline\n\r\t\b\f nul\x00 del\x7f unicode 渡船 🚢 {key[0:4]}'
    data = {"name": "sample", "units": {"root": "/src", "glob": "*.txt"},
            "receiver": {"dest": "/dst/" + value, "extract": True},
            "sender": {"stage_dir": "/stage/" + value, "read_blocked": []},
            "remote": {"adapter": "local", "prefix": "/sample",
                       "options": {"root": "/relay/" + value},
                       "sides": {"receiver": {"adapter": "local",
                                              "options": {"root": "/other/" + value}}}}}
    result = tomllib.loads(wizard.render(data))
    assert result == data
    assert job_from_dict(result).receiver.dest == "/dst/" + value


def test_prompt_paths_preserve_remote_home_and_templates(tmp_path, offline):
    values = answers(tmp_path)
    values[1:4] = ["~/source", "~/state", "~/stage"]
    values[7] = '~/destination with "quotes" and \\backslash/渡船🚢/{key[0:4]}/{key}'
    assert invoke(tmp_path, values)[0] == 0
    data = tomllib.loads((tmp_path / "job.toml").read_text())
    assert data["units"]["root"] == "~/source"
    assert data["state_dir"] == "~/state"
    assert data["receiver"]["dest"] == values[7]


@pytest.mark.parametrize("method", ["manual", "systemd", "cron", "pueue"])
@pytest.mark.parametrize("side", ["sender", "receiver"])
def test_execution_guidance_never_installs(tmp_path, offline, method, side):
    values = answers(tmp_path, method=method, side=side)
    code, _, stderr, stdin = invoke(tmp_path, values)
    assert code == 0 and stdin.read() == ""
    job = load_job(tmp_path / "job.toml")
    if method == "systemd":
        assert service.systemd_unit(job, side, poll=None) in stderr
        assert "--kind systemd --install" in stderr and "--uninstall" in stderr
    elif method == "cron":
        assert service.cron_line(job, side, "0 2 * * *", poll=None) in stderr
        assert "--kind cron --schedule '0 2 * * *' --install" in stderr
    elif method == "pueue":
        assert "shared one-slot queue" in stderr
        assert "pueue/" in stderr
        if side == "receiver":
            assert "--follow" in stderr
    assert "doctor" in stderr


def test_stdout_is_only_toml_and_writes_nothing(tmp_path, offline):
    code, stdout, stderr, _ = invoke(tmp_path, answers(tmp_path), to_stdout=True)
    assert code == 0
    assert job_from_dict(tomllib.loads(stdout)).name == "sample"
    assert list(tmp_path.iterdir()) == []
    assert "No file will be written" in stderr and "before using these commands" in stderr


@pytest.mark.parametrize("confirm", ["no", ""])
def test_decline_save_creates_nothing(tmp_path, offline, confirm):
    code, stdout, stderr, _ = invoke(tmp_path, answers(tmp_path, confirm=confirm))
    assert code == 0 and stdout == ""
    assert list(tmp_path.iterdir()) == []
    assert "Cancelled" in stderr


def test_non_tty_fails_before_prompts(tmp_path):
    stderr = io.StringIO()
    code = wizard.run(str(tmp_path / "job.toml"), stdin=io.StringIO("yes\n"), stderr=stderr)
    assert code == 2 and "interactive stdin is required" in stderr.getvalue()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("at", [0, 4, -1])
def test_eof_leaves_no_file(tmp_path, at):
    values = answers(tmp_path)[:at]
    stderr = io.StringIO()
    stdin = Terminal("\n".join(values) + ("\n" if values else ""))
    assert wizard.run(str(tmp_path / "job.toml"), stdin=stdin, stderr=stderr) == 2
    assert "input ended" in stderr.getvalue()
    assert list(tmp_path.iterdir()) == []


def test_ctrl_c_at_confirmation_leaves_no_file(tmp_path):
    class Interrupted(Terminal):
        def readline(self):
            line = super().readline()
            if not line:
                raise KeyboardInterrupt
            return line

    stdin = Interrupted("\n".join(answers(tmp_path)[:-1]) + "\n")
    stderr = io.StringIO()
    assert wizard.run(str(tmp_path / "job.toml"), stdin=stdin, stderr=stderr) == 130
    assert "interrupted" in stderr.getvalue()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling"])
def test_existing_target_is_never_overwritten(tmp_path, kind):
    path = tmp_path / "job.toml"
    if kind == "file":
        path.write_text("keep original\n")
    elif kind == "directory":
        path.mkdir()
    else:
        target = tmp_path / "original"
        if kind == "symlink":
            target.write_text("keep original\n")
        path.symlink_to(target)
    code, _, stderr, stdin = invoke(tmp_path, answers(tmp_path))
    assert code == 2 and "refusing to overwrite" in stderr
    assert stdin.tell() == 0
    if kind == "file":
        assert path.read_text() == "keep original\n"
    if kind in ("symlink", "dangling"):
        assert path.is_symlink()


def test_parent_must_exist(tmp_path):
    code, _, stderr, _ = invoke(tmp_path, answers(tmp_path), target=tmp_path / "missing/job.toml")
    assert code == 2 and "parent directory does not exist" in stderr
    assert list(tmp_path.iterdir()) == []


def test_invalid_inputs_are_retried(tmp_path):
    values = answers(tmp_path, backend="rclone", method="cron")
    for valid, invalid in (("sample", "../bad"), (str(tmp_path / "source"), "relative"),
                           ("glob", "unknown"), ("*.txt", "../*.txt"),
                           ("gdrive:", "missing-colon"), ("/ferry/sample", "/"),
                           ("0 2 * * *", "@daily"), ("yes", "maybe")):
        values.insert(values.index(valid), invalid)
    code, _, stderr, _ = invoke(tmp_path, values)
    assert code == 0 and stderr.count("Invalid input:") == 8


def test_numeric_choices(tmp_path):
    values = answers(tmp_path)
    for choice in ("glob", "tar", "local", "sender", "manual"):
        values[values.index(choice)] = "1"
    assert invoke(tmp_path, values)[0] == 0


@pytest.mark.parametrize("validate,value", [
    (wizard._member, "data/{unknown}"), (wizard._member, "../data/{key}"),
    (wizard._destination, "/dest/{key[x]}"), (wizard._absolute_path, "~/landing"),
    (wizard._binary, "~/bin/BaiduPCS-Go"), (wizard._binary, "--help"),
    (wizard._rclone_remote, ":s3,secret=value:"), (wizard._repo, "https://github.com/a/b"),
    (wizard._ssh_host, "-oProxyCommand=cmd"), (wizard._ssh_host, "host -o x"),
    (wizard._schedule, "0 2 * * *\n* * * * *"),
    (wizard._schedule, "0 2 * * #comment"), (wizard._schedule, "0 2 * * *;command"),
])
def test_field_validation(validate, value):
    with pytest.raises(ValueError):
        validate(value)


def test_exclusive_create_race_does_not_replace_file(tmp_path, monkeypatch):
    path = tmp_path / "job.toml"
    original = wizard._write_new

    def race(target, text):
        target.write_text("another writer\n")
        original(target, text)

    monkeypatch.setattr(wizard, "_write_new", race)
    code, _, stderr, _ = invoke(tmp_path, answers(tmp_path))
    assert code == 2 and "File exists" in stderr
    assert path.read_text() == "another writer\n"


@pytest.mark.parametrize("error,expected", [(OSError("disk full"), 2), (KeyboardInterrupt(), 130)])
def test_failed_write_cleans_only_new_file(tmp_path, monkeypatch, error, expected):
    original = Path.open

    class FailedWrite:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def write(self, text):
            self.stream.write(text[:10])
            self.stream.flush()
            raise error

    def open_file(path, mode="r", *args, **kwargs):
        stream = original(path, mode, *args, **kwargs)
        return FailedWrite(stream) if mode == "x" else stream

    monkeypatch.setattr(Path, "open", open_file)
    code, _, stderr, _ = invoke(tmp_path, answers(tmp_path))
    assert code == expected
    assert ("disk full" if expected == 2 else "interrupted") in stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("output", ["", "job\n.toml", "job\x00.toml", "job\x7f.toml"])
def test_output_path_rejects_control_characters(tmp_path, monkeypatch, output):
    monkeypatch.chdir(tmp_path)
    stderr = io.StringIO()
    assert wizard.run(output, stdin=Terminal(), stderr=stderr) == 2
    assert "non-empty path without control characters" in stderr.getvalue()
    assert list(tmp_path.iterdir()) == []


def test_decline_stdout_emits_no_toml(tmp_path, offline):
    code, stdout, _, _ = invoke(tmp_path, answers(tmp_path, confirm="no"), to_stdout=True)
    assert code == 0 and stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_cli_stdout_and_default_output(tmp_path, monkeypatch, offline, capsys):
    monkeypatch.chdir(tmp_path)
    for args in (["init", "--stdout"], ["init"]):
        monkeypatch.setattr("sys.stdin", Terminal("\n".join(answers(tmp_path)) + "\n"))
        assert cli.main(args) == 0
        out = capsys.readouterr()
        if "--stdout" in args:
            assert job_from_dict(tomllib.loads(out.out)).name == "sample"
            assert list(tmp_path.iterdir()) == []
        else:
            assert out.out == "" and load_job(tmp_path / "job.toml").name == "sample"


def test_cli_init_dispatches_without_loading_job(tmp_path, monkeypatch, offline, capsys):
    def forbidden(*args):
        pytest.fail("init must not load an existing job")

    monkeypatch.setattr(cli, "load_job", forbidden)
    monkeypatch.setattr("sys.stdin", Terminal("\n".join(answers(tmp_path)) + "\n"))
    assert cli.main(["init", "--output", str(tmp_path / "job.toml")]) == 0
    assert capsys.readouterr().out == ""
    assert load_job(tmp_path / "job.toml").name == "sample"


def test_init_help_works_without_tty_or_job(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO())
    with pytest.raises(SystemExit) as exc:
        cli.main(["init", "--help"])
    assert exc.value.code == 0
    assert "--stdout" in capsys.readouterr().out


@pytest.mark.parametrize("pack", ["none", "tar"])
def test_generated_job_local_roundtrip(tmp_path, pack):
    src, relay = tmp_path / "source", tmp_path / "relay"
    src.mkdir()
    relay.mkdir()
    content = b"non-empty wizard roundtrip\n"
    (src / "hello.txt").write_bytes(content)
    assert invoke(tmp_path, answers(tmp_path, pack=pack, extract="yes"))[0] == 0
    job = load_job(tmp_path / "job.toml")
    stop = threading.Event()
    watchdog = threading.Timer(10, stop.set)
    watchdog.start()
    try:
        result = send(job, poll=0.01, stop=stop)
    finally:
        watchdog.cancel()
    assert result.pushed == 1 and not result.failed
    result = receive(job, once=True, poll=0.01)
    assert result.verified == 1 and not result.failed
    assert (tmp_path / "destination/hello.txt").read_bytes() == content
    assert (src / "hello.txt").read_bytes() == content
