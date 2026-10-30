"""Blocked time windows such as ``"Mon-Fri 09:15-15:30 Asia/Shanghai"``.

Used to keep heavy source reads (packing) out of hours where they would compete with
other users of a shared filesystem. Days are ``Mon-Fri``, ``Sat,Sun`` or ``*``.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True)
class Window:
    days: frozenset[int]  # 0 = Monday
    start: dt.time
    end: dt.time
    tz: ZoneInfo

    @classmethod
    def parse(cls, spec: str) -> Window:
        m = re.fullmatch(r"\s*(\S+)\s+(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s+(\S+)\s*", spec)
        if not m:
            raise ValueError(
                f"invalid window {spec!r}; expected 'Mon-Fri 09:15-15:30 Asia/Shanghai'")
        days = _parse_days(m.group(1))
        start = dt.time(int(m.group(2)), int(m.group(3)))
        end = dt.time(int(m.group(4)), int(m.group(5)))
        if end <= start:
            raise ValueError(f"window {spec!r} must end after it starts (same day)")
        return cls(days, start, end, ZoneInfo(m.group(6)))

    def contains(self, now: dt.datetime) -> bool:
        local = now.astimezone(self.tz)
        return local.weekday() in self.days and self.start <= local.time() < self.end

    def seconds_until_open(self, now: dt.datetime) -> float:
        if not self.contains(now):
            return 0.0
        local = now.astimezone(self.tz)
        end = dt.datetime.combine(local.date(), self.end, tzinfo=self.tz)
        return (end - local).total_seconds()


def _parse_days(text: str) -> frozenset[int]:
    if text == "*":
        return frozenset(range(7))
    days: set[int] = set()
    for part in text.lower().split(","):
        if "-" in part:
            a, b = (_DAYS.index(x[:3]) for x in part.split("-", 1))
            days.update(range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)])
        else:
            days.add(_DAYS.index(part[:3]))
    return frozenset(days)


def blocked_for(windows: list[Window], now: dt.datetime | None = None) -> float:
    """Seconds until no window blocks ``now`` (0 when free). Handles adjacent windows."""
    now = now or dt.datetime.now(dt.timezone.utc)
    total = 0.0
    for _ in range(len(windows) + 1):
        wait = max((w.seconds_until_open(now) for w in windows), default=0.0)
        if wait <= 0:
            return total
        total += wait
        now += dt.timedelta(seconds=wait)
    return total


@dataclass(frozen=True)
class Schedule:
    """When a transfer direction may run: inside any `allowed` window (if any are given)
    and outside every `blocked` window. Empty schedule = always open."""

    allowed: tuple[Window, ...] = ()
    blocked: tuple[Window, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.allowed or self.blocked)

    def is_open(self, now: dt.datetime) -> bool:
        if self.allowed and not any(w.contains(now) for w in self.allowed):
            return False
        return not any(w.contains(now) for w in self.blocked)


def wait_seconds(schedules: list[Schedule], now: dt.datetime | None = None,
                 *, step: int = 60, horizon_days: int = 8) -> float | None:
    """Seconds until every schedule is open (0 if open now; None if never within horizon).

    Windows are minute-aligned, so a minute-step scan is exact enough and simple.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    active = [s for s in schedules if s]
    if all(s.is_open(now) for s in active):
        return 0.0
    probe = now.replace(second=0, microsecond=0) + dt.timedelta(seconds=step)
    end = now + dt.timedelta(days=horizon_days)
    while probe <= end:
        if all(s.is_open(probe) for s in active):
            return (probe - now).total_seconds()
        probe += dt.timedelta(seconds=step)
    return None


def seconds_until_close(schedules: list[Schedule], now: dt.datetime | None = None,
                        *, step: int = 60, horizon_days: int = 8) -> float | None:
    """While open: seconds until any schedule closes (None if it stays open past horizon)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    active = [s for s in schedules if s]
    if not active:
        return None
    probe = now.replace(second=0, microsecond=0) + dt.timedelta(seconds=step)
    end = now + dt.timedelta(days=horizon_days)
    while probe <= end:
        if not all(s.is_open(probe) for s in active):
            return max(0.0, (probe - now).total_seconds())
        probe += dt.timedelta(seconds=step)
    return None
