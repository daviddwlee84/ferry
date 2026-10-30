import pytest

from ferry import cli, service
from ferry.job import job_from_dict


def _job(tmp_path):
    path = tmp_path / "job.toml"
    path.write_text("")
    return job_from_dict({"name": "mirror", "units": {"root": "/src", "glob": "*"},
                          "receiver": {"dest": "/dst"},
                          "remote": {"adapter": "local", "prefix": "/p",
                                     "options": {"root": "/r"}}}, path)


def test_systemd_unit_runs_follow_with_restart(tmp_path):
    unit = service.systemd_unit(_job(tmp_path), "receiver", poll=300)
    assert "Restart=on-failure" in unit
    exec_line = next(x for x in unit.splitlines() if x.startswith("ExecStart="))
    assert exec_line.endswith(f"receive {tmp_path / 'job.toml'} --follow --poll 300")
    assert service.unit_name(_job(tmp_path), "sender") == "ferry-mirror-sender.service"


def test_cron_line_is_one_shot_and_tagged(tmp_path):
    line = service.cron_line(_job(tmp_path), "receiver", "*/30 * * * *", poll=None)
    assert line.startswith("*/30 * * * * ")
    assert "receive" in line and "--once" in line and "--follow" not in line
    assert line.endswith("# ferry:mirror:receiver")
    send = service.cron_line(_job(tmp_path), "sender", "0 2 * * *", poll=None)
    assert " send " in send and "--follow" not in send and "--once" not in send


def test_install_cron_replaces_only_its_own_line(tmp_path, monkeypatch):
    table = ["MAILTO=x", "0 1 * * * other", "5 * * * * old # ferry:mirror:sender"]
    monkeypatch.setattr(service, "_crontab", lambda: list(table))
    written = {}
    monkeypatch.setattr(service, "_write_crontab", lambda lines: written.setdefault("l", lines))
    service.install_cron(_job(tmp_path), "sender", "0 3 * * *", poll=None)
    assert written["l"][:2] == ["MAILTO=x", "0 1 * * * other"]
    assert len(written["l"]) == 3 and written["l"][2].startswith("0 3 * * * ")


@pytest.mark.parametrize("kind", ["systemd", "cron"])
def test_cli_preview_does_not_probe_or_install(tmp_path, monkeypatch, capsys, kind):
    job = _job(tmp_path)
    monkeypatch.setattr(cli, "load_job", lambda _: job)

    def forbidden(*args, **kwargs):
        pytest.fail("preview must not run commands or install anything")

    monkeypatch.setattr(service.subprocess, "run", forbidden)
    monkeypatch.setattr(service, "linger_enabled", forbidden)
    monkeypatch.setattr(service, "install_systemd", forbidden)
    monkeypatch.setattr(service, "install_cron", forbidden)
    assert cli.main(["service", str(job.path), "--side", "receiver", "--kind", kind]) == 0
    out = capsys.readouterr()
    if kind == "systemd":
        assert out.out == service.systemd_unit(job, "receiver", poll=None)
        assert "requires lingering" in out.err
    else:
        assert out.out.strip() == service.cron_line(job, "receiver", "0 * * * *", poll=None)


def test_cli_systemd_install_keeps_linger_warning(tmp_path, monkeypatch, capsys):
    job = _job(tmp_path)
    monkeypatch.setattr(cli, "load_job", lambda _: job)
    calls = []

    def install(job_, side, *, poll):
        calls.append((job_, side, poll))
        return tmp_path / service.unit_name(job_, side)

    monkeypatch.setattr(service, "install_systemd", install)
    monkeypatch.setattr(service, "linger_enabled", lambda: False)
    assert cli.main(["service", str(job.path), "--side", "sender", "--install",
                     "--poll", "30"]) == 0
    out = capsys.readouterr()
    assert calls == [(job, "sender", 30)]
    assert "installed and started" in out.out and "lingering is off" in out.err


@pytest.mark.parametrize("kind", ["systemd", "cron"])
def test_cli_uninstall_keeps_existing_interface(tmp_path, monkeypatch, capsys, kind):
    job = _job(tmp_path)
    monkeypatch.setattr(cli, "load_job", lambda _: job)
    calls = []
    monkeypatch.setattr(service, f"uninstall_{kind}", lambda j, s: calls.append((j, s)))
    assert cli.main(["service", str(job.path), "--side", "receiver", "--kind", kind,
                     "--uninstall"]) == 0
    assert calls == [(job, "receiver")]
    assert f"removed {kind} entry" in capsys.readouterr().out
