"""DataNodes free-download recipe.

DataNodes is a XFileSharing-style hoster with a JSON free-download endpoint: a
multipart POST to ``https://datanodes.to/download`` (``op=download2``) returns
``{"url": "<direct cdn link>"}`` where the URL is percent-encoded. The native
flow works un-gated most of the time, but DataNodes sits behind an active
Cloudflare gate from some IPs, which a plain client cannot clear.

So, mirroring the DataVaults recipe: use FlareSolverr to warm a session against
the hoster page (clearing any Cloudflare challenge and adopting the browser's
User-Agent + cookies), then replay the download2 POST with a plain HTTP client
using that same UA and cookie jar. FlareSolverr wraps JSON responses in
``<pre>...</pre>``, so the URL is pulled out of the JSON either way.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from ..models import Cookie, ResolveRequest, ResolveResponse
from ..solver import FlareSolverrClient, SolverError
from .base import Recipe

# Fallback UA when FlareSolverr is unavailable; DataNodes is normally un-gated.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DOWNLOAD_URL = "https://datanodes.to/download"
REFERER = "https://datanodes.to/download"
HTTP_TIMEOUT = 45.0

_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
_BOUNDARY = "----SlipgateDataNodesBoundary7kJ2xQ9vRt3mWp"


def _absolute_redirect(base: str, value: str) -> str:
    resolved = urljoin(base, value)
    return resolved if urlsplit(resolved).scheme in {"http", "https"} else ""


class DataNodesRecipe(Recipe):
    name = "datanodes"
    hosts = ("datanodes", "datanodes.to")
    # One warm FlareSolverr session, reused so any Cloudflare solve is paid once.
    SESSION = "slipgate-datanodes"

    async def resolve(self, client: FlareSolverrClient, req: ResolveRequest) -> ResolveResponse:
        if not req.page_url:
            return ResolveResponse(ok=False, error="missing page_url")
        parts = urlsplit(req.page_url)
        segs = [s for s in parts.path.split("/") if s]
        if not parts.netloc or not segs:
            return ResolveResponse(ok=False, error="unrecognized datanodes url")
        file_id = segs[0]

        # Clear any Cloudflare gate and adopt the browser's UA + cookies so the
        # plain-client POST below presents a matching, cleared session. If the
        # solver is down, proceed anyway: DataNodes is normally un-gated.
        ua, seed = DEFAULT_UA, {}
        replay_cookies: list[Cookie] = []
        try:
            async with client.session_lock(self.SESSION):
                await client.ensure_session(self.SESSION)
                warm = await client.get(req.page_url, session=self.SESSION)
            ua = warm.user_agent or DEFAULT_UA
            replay_cookies = warm.cookies
            seed = {c.name: c.value for c in warm.cookies}
        except SolverError:
            pass

        try:
            url, reason = await _download2(req.page_url, file_id, ua, seed)
        except httpx.HTTPError as exc:
            return ResolveResponse(ok=False, error=f"datanodes request failed: {exc}")
        if url:
            fname = url.rsplit("/", 1)[-1].split("?")[0]
            return ResolveResponse(
                ok=True,
                download_url=url,
                file_name=fname,
                cookies=replay_cookies,
                user_agent=ua,
            )
        return ResolveResponse(ok=False, error=reason or "no datanodes download url")


def _multipart(fields: list[tuple[str, str]]) -> bytes:
    body = bytearray()
    for k, v in fields:
        body += f"--{_BOUNDARY}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += v.encode()
        body += b"\r\n"
    body += f"--{_BOUNDARY}--\r\n".encode()
    return bytes(body)


async def _download2(
    page_url: str, file_id: str, ua: str, seed_cookies: dict[str, str]
) -> tuple[str, str]:
    """POST the download2 form and pull the direct URL out of the JSON response.
    Returns ``(direct_url, reason)``; ``reason`` is set only on failure."""
    body = _multipart(
        [
            ("op", "download2"),
            ("id", file_id),
            ("rand", ""),
            ("referer", REFERER),
            ("method_free", "Free Download >>"),
            ("method_premium", ""),
            ("__dl", "1"),
            ("g_captch__a", "1"),
        ]
    )
    headers = {
        "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
        "Referer": REFERER,
        "Origin": "https://datanodes.to",
    }
    request_cookies = {**seed_cookies, "lang": "english"}
    async with httpx.AsyncClient(
        headers={"User-Agent": ua},
        cookies=request_cookies,
        follow_redirects=False,
        timeout=httpx.Timeout(HTTP_TIMEOUT),
    ) as http:
        resp = await http.post(DOWNLOAD_URL, content=body, headers=headers)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "")
            if loc:
                direct = _absolute_redirect(str(resp.url), loc)
                if direct:
                    return (direct, "")
        return _extract_url(resp.text), ""


def _extract_url(text: str) -> str:
    """Pull the direct download URL out of the JSON response. FlareSolverr wraps
    JSON in <pre>; the plain client returns it raw. The URL is percent-encoded."""
    if not text:
        return ""
    m = _PRE_RE.search(text)
    raw = html.unescape(m.group(1)).strip() if m else text.strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    url = data.get("url") or data.get("URL") or data.get("direct_url") or ""
    if not url:
        return ""
    decoded = unquote(url)
    return decoded if decoded.startswith("http") else ""
