import datetime as dt
from pathlib import Path

import pytest

from ferry.adapters.baidupcs import parse_config_value, parse_ls, parse_meta_size, parse_quota
from ferry.sizes import human, parse_size
from ferry.windows import Window, blocked_for

FIX = Path(__file__).parent / "fixtures" / "baidupcs"
CST = dt.timezone(dt.timedelta(hours=8))


def fx(name: str) -> str:
    return (FIX / f"{name}.txt").read_text()


def test_sizes():
    assert parse_size("3900MiB") == 3900 * 1024**2
    assert parse_size("1.5TB") == 1_500_000_000_000
    assert parse_size("200 GB") == 200 * 10**9
    assert parse_size(7) == 7 and parse_size(None) is None
    with pytest.raises(ValueError):
        parse_size("12 parsecs")
    assert human(9_500_000) == "9.5MB"


def test_baidu_quota():
    total, used = parse_quota(fx("quota"))
    assert total > used > 0
    assert round((total - used) / 1024**4, 2) == round(10.006836 - 1.084948, 2)


def test_baidu_meta():
    assert parse_meta_size(fx("meta_file")) == 5974353920
    assert parse_meta_size(fx("meta_missing")) is None
    assert parse_meta_size(fx("meta_dir")) is None


def test_baidu_ls():
    assert parse_ls(fx("ls_dir")) == ["payload/", "payload.tar", "payload.tar.sha256"]
    assert parse_ls(fx("ls_missing")) == []


def test_baidu_config_proxy():
    text = fx("config_proxy")
    assert parse_config_value(text, "proxy") == ""
    configured = text.replace("  proxy      ", "  proxy      socks5://127.0.0.1:7890      ", 1)
    assert parse_config_value(configured, "proxy") == "socks5://127.0.0.1:7890"


@pytest.mark.parametrize("when,blocked", [
    (dt.datetime(2026, 10, 30, 9, 14, tzinfo=CST), False),   # Friday before open
    (dt.datetime(2026, 10, 30, 9, 15, tzinfo=CST), True),
    (dt.datetime(2026, 10, 30, 15, 29, tzinfo=CST), True),
    (dt.datetime(2026, 10, 30, 15, 30, tzinfo=CST), False),
    (dt.datetime(2026, 10, 31, 11, 0, tzinfo=CST), False),   # Saturday
    (dt.datetime(2026, 10, 30, 3, 0, tzinfo=dt.timezone.utc), True),  # 11:00 CST
])
def test_window(when, blocked):
    w = Window.parse("Mon-Fri 09:15-15:30 Asia/Shanghai")
    assert w.contains(when) is blocked
    assert (blocked_for([w], when) > 0) is blocked


def test_window_wait_and_day_lists():
    w = Window.parse("Mon-Fri 09:15-15:30 Asia/Shanghai")
    assert blocked_for([w], dt.datetime(2026, 10, 30, 15, 0, tzinfo=CST)) == 1800
    assert Window.parse("Sat,Sun 00:00-23:59 UTC").days == frozenset({5, 6})
    assert Window.parse("Fri-Mon 01:00-02:00 UTC").days == frozenset({4, 5, 6, 0})
    with pytest.raises(ValueError):
        Window.parse("Mon 10:00-09:00 UTC")
