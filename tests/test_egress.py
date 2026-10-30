import socket

from ferry.egress import check_routes, clean_env, proxy_vars_present


def test_clean_env_strips_every_proxy_variable():
    base = {"PATH": "/bin", "https_proxy": "http://p:7890", "HTTP_PROXY": "x",
            "ALL_PROXY": "socks5://p", "no_proxy": "localhost"}
    env = clean_env(base)
    assert env["PATH"] == "/bin"
    assert not {"https_proxy", "HTTP_PROXY", "ALL_PROXY"} & set(env)
    assert env["NO_PROXY"] == env["no_proxy"] == "*"
    assert proxy_vars_present(base) == ["ALL_PROXY", "HTTP_PROXY", "https_proxy"]


def _resolver(mapping):
    def resolve(host, port, type=None):  # noqa: A002 - mirrors getaddrinfo
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]
    return resolve


def test_fake_ip_and_tun_routes_are_flagged():
    resolve = _resolver({"a.example": "198.18.0.7", "b.example": "8.8.8.8",
                         "c.example": "1.1.1.1", "d.example": "9.9.9.9",
                         "nas.tailnet": "100.100.1.1"})
    ifaces = {"8.8.8.8": "utun4", "1.1.1.1": "eno1", "9.9.9.9": "tailscale0",
              "100.100.1.1": "utun3"}
    hosts = ["a.example", "b.example", "c.example", "d.example", "nas.tailnet"]
    found = {f.host: f for f in check_routes(hosts, resolve=resolve, route_iface=ifaces.get)}
    assert "fake-ip" in found["a.example"].problem
    assert "utun4" in found["b.example"].problem
    assert found["c.example"].problem is None and found["c.example"].interface == "eno1"
    assert found["d.example"].problem is None  # VPN mesh exit is reported, not a proxy
    assert found["nas.tailnet"].problem is None  # tailnet/private peers are direct


def test_unresolvable_host_is_reported():
    def fail(*a, **k):
        raise socket.gaierror("no such host")
    (finding,) = check_routes(["nowhere.invalid"], resolve=fail, route_iface=lambda a: None)
    assert finding.problem.startswith("cannot resolve")
