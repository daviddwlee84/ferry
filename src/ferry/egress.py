"""Direct-egress guard: keep bulk transfers off metered proxies.

Three layers:

1. Every child process runs with proxy variables removed (``clean_env``), so tools such as
   rclone, gh or curl cannot silently pick up an ``https_proxy`` from the caller.
2. Adapters reject proxies configured inside the tool itself (``Adapter.check``).
3. ``check_routes`` resolves each backend endpoint and inspects the kernel route: a clash /
   mihomo fake-ip answer (198.18.0.0/15) or a tun-style egress interface means the traffic
   would be captured by a TUN proxy.

With ``egress = "direct"`` (default) any finding is fatal; ``"any"`` downgrades findings to
warnings for backends that can only be reached through a proxy (e.g. Google Drive behind
the GFW, where the proxy's own rules may still choose DIRECT). A transparent proxy on the
router is invisible from the host and cannot be detected here.
"""
from __future__ import annotations

import ipaddress
import os
import platform
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass

PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "rsync_proxy", "no_proxy")
FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
# Interfaces that TUN-mode proxies (clash/mihomo/sing-box) create. VPN meshes such as
# tailscale or wireguard are reported as the interface but are not proxies.
PROXY_IFACE = re.compile(r"^(tun|utun|meta|mihomo|clash|sing)", re.I)


def clean_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for name in list(env):
        if name.lower() in PROXY_VARS:
            del env[name]
    env["NO_PROXY"] = env["no_proxy"] = "*"
    return env


def proxy_vars_present(base: dict[str, str] | None = None) -> list[str]:
    env = os.environ if base is None else base
    return sorted(n for n, v in env.items() if n.lower() in PROXY_VARS[:-1] and v)


@dataclass(frozen=True)
class RouteFinding:
    host: str
    address: str | None
    interface: str | None
    problem: str | None  # None means the route looks direct


def _tool(name: str) -> str:
    """Locate admin tools that live in /sbin on older distros (not on a user's PATH)."""
    for candidate in (shutil.which(name), f"/usr/sbin/{name}", f"/sbin/{name}"):
        if candidate and os.path.exists(candidate):
            return candidate
    return name


def _route_iface(address: str) -> str | None:
    system = platform.system()
    try:
        if system == "Linux":
            out = subprocess.run([_tool("ip"), "route", "get", address], capture_output=True,
                                 text=True, timeout=10, env=clean_env()).stdout
            m = re.search(r"\bdev\s+(\S+)", out)
        elif system == "Darwin":
            out = subprocess.run([_tool("route"), "-n", "get", address], capture_output=True,
                                 text=True, timeout=10, env=clean_env()).stdout
            m = re.search(r"interface:\s*(\S+)", out)
        else:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    return m.group(1) if m else None


def check_routes(hosts: tuple[str, ...] | list[str], *, resolve=socket.getaddrinfo,
                 route_iface=_route_iface, trusted: tuple[str, ...] = ()) -> list[RouteFinding]:
    findings = []
    for host in hosts:
        try:
            infos = resolve(host, 443, type=socket.SOCK_STREAM)
        except OSError as e:
            findings.append(RouteFinding(host, None, None, f"cannot resolve: {e}"))
            continue
        addresses = sorted({info[4][0] for info in infos if info[0] == socket.AF_INET}) or \
            sorted({info[4][0] for info in infos})
        address = addresses[0]
        if ipaddress.ip_address(address) in FAKE_IP:
            findings.append(RouteFinding(host, address, None,
                                         "resolves to fake-ip range 198.18.0.0/15 (TUN proxy DNS)"))
            continue
        iface = route_iface(address)
        problem = None
        ip = ipaddress.ip_address(address)
        private = ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")
        if iface and PROXY_IFACE.match(iface) and not private and iface not in trusted:
            problem = f"routed via tun interface {iface} (TUN-mode proxy?)"
        findings.append(RouteFinding(host, address, iface, problem))
    return findings
