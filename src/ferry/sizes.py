"""Human-readable byte sizes: decimal (GB) and binary (GiB) units are both accepted."""
from __future__ import annotations

import re

_UNITS = {
    "": 1, "B": 1,
    "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
    "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
    "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4,
}


_BITS = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([kKmMgG]?)(?:bit|bps)\s*")


def parse_size(value: str | int | None) -> int | None:
    """Bytes from "300GB", "3900MiB", ... Network rates may also be given in bits:
    "20Mbit" / "20Mbps" (decimal, lowercase b) mean 20e6 bits = 2.5e6 bytes."""
    if value is None or isinstance(value, int):
        return value
    bits = _BITS.fullmatch(value)
    if bits:
        scale = {"": 1, "k": 10**3, "m": 10**6, "g": 10**9}[bits.group(2).lower()]
        return int(float(bits.group(1)) * scale / 8)
    m = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]*)\s*", value)
    if not m or m.group(2).upper() not in _UNITS:
        raise ValueError(f"invalid size: {value!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def human(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1000
    raise AssertionError("unreachable")
