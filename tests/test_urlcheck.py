"""Unit battery for slipgate.urlcheck.validate_url, the C1/C2 SSRF gate.

Every accept case here must validate True and every reject case must validate
False. The module does not exist on the pre-fix HEAD, so each test fails at its
lazy import until the fix lands (the intended red state); the autouse
`_no_real_dns` conftest fixture keeps every other test file collectible and
DNS-free.
"""

from __future__ import annotations

import socket

import pytest

ALLOWED = {
    "akirabox.com",
    "www.nexusmods.com",
    "nexusmods.com",
    "datavaults.co",
    "datanodes.to",
    "vikingfile.com",
    "vik1ngfile.site",
}


@pytest.fixture
def validate():
    """The urlcheck gate; imported lazily so collection works pre-fix."""
    from slipgate.urlcheck import validate_url

    return validate_url


async def test_accepts_https_on_allowlisted_host(validate):
    assert await validate("https://akirabox.com/x", ALLOWED) is True


async def test_accepts_allowlisted_host_with_query(validate):
    assert await validate("https://www.nexusmods.com/mods/1?tab=files&file_id=2", ALLOWED) is True


async def test_accepts_trailing_dot_host(validate):
    assert await validate("https://akirabox.com./x", ALLOWED) is True


async def test_accepts_nested_path_on_allowlisted_host(validate):
    assert await validate("https://datavaults.co/id/fname", ALLOWED) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://evil.com/x",
        "http://evil.com/x",
        "https://user@akirabox.com/x",
        "https://akirabox.com@evil.com/x",
        "https://akirabox.com/x#frag",
        "https://akirabox.com:8080/x",
        "https://akirabox.com:8443/x",
        "https://evil.akirabox.com/x",
        "https://akirabox.com.evil.com/x",
        "https://akirabox.com%20/x",
        "",
        "https://akirabox\u3002com/x",
    ],
)
async def test_rejects_non_allowlisted_or_malformed(validate, url):
    assert await validate(url, ALLOWED) is False


async def test_rejects_url_over_max_length(validate):
    assert await validate("https://akirabox.com/x", ALLOWED, max_length=20) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/x",
        "https://10.0.0.1/x",
        "https://192.168.1.1/x",
        "https://172.16.0.1/x",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/x",
        "https://[::ffff:127.0.0.1]/x",
    ],
)
async def test_rejects_private_ip_literals_even_when_allowlisted(validate, url):
    ip_hosts = {
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
    }
    assert await validate(url, ip_hosts) is False


async def test_rejects_host_resolving_to_private_ip(monkeypatch):
    import slipgate.urlcheck as urlcheck
    from slipgate.urlcheck import validate_url

    def resolves_loopback(host, port, family=0, type=0, proto=0, flags=0):
        return [("AF_INET", "SOCK_STREAM", 6, "", ("127.0.0.1", port or 443))]

    monkeypatch.setattr(urlcheck, "_getaddrinfo", resolves_loopback)
    assert await validate_url("https://akirabox.com/x", ALLOWED) is False


async def test_rejects_when_resolution_fails(monkeypatch):
    import slipgate.urlcheck as urlcheck
    from slipgate.urlcheck import validate_url

    def raises(host, port, family=0, type=0, proto=0, flags=0):
        raise socket.gaierror("no address")

    monkeypatch.setattr(urlcheck, "_getaddrinfo", raises)
    assert await validate_url("https://akirabox.com/x", ALLOWED) is False
