"""Allowlist-based URL validation, the single choke point for SSRF protection.

Every caller-supplied URL that would otherwise be opened by the FlareSolverr
browser or a plain HTTP client must pass :func:`validate_url` first. It is
fail-closed: anything that is not a well-formed https URL whose host is an exact
entry in the allowlist is rejected, and no URL is ever rewritten from it.

Residual: the DNS resolution check is best-effort. The browser (or the proxy it
routes through) may resolve the host differently than this process does (DNS
rebinding, split-horizon DNS on the proxy, a misconfigured resolver), so the
exact host allowlist is the primary control; the IP check is only a second
layer that rejects addresses that are private under every plausible answer.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit

# Module-level so tests can monkeypatch it to exercise the IP-block without
# touching real DNS.
_getaddrinfo = socket.getaddrinfo


async def validate_url(url: str, allowed_hosts: set[str], *, max_length: int = 2000) -> bool:
    """Return True only for an https URL on an exact allowlisted host that
    resolves exclusively to public addresses. Anything else returns False."""
    if len(url) > max_length:
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    hostname = parts.hostname
    if not hostname:
        return False
    if parts.scheme != "https":
        return False
    if parts.username or parts.password or parts.fragment:
        return False
    if port not in (None, 443):
        return False

    host = hostname.lower()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        return False
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        return False
    if host not in allowed_hosts:
        return False
    return await asyncio.to_thread(_blocked_ip, host)


def _blocked_ip(host: str) -> bool:
    """Return True only when the host resolves exclusively to public addresses."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False  # IP literals are never allowlisted.
    try:
        infos = _getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True
