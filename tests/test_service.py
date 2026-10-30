from ferry import service
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
