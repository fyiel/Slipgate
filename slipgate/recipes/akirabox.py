"""Akira Box download recipe (via FlareSolverr).

Akira Box sits behind an active Cloudflare gate, but its public File Status API
(``GET /api/files?url=<file url>``) returns JSON metadata for a file without any
auth, including the direct download link::

    {"status": 200, "name": "example.pdf", "size": "1.2 MB",
     "type": "file", "mime": "application/pdf", "url": "https://akirabox.com/<id>/file"}

This recipe normalizes the hoster page URL to Akira Box's canonical
``/<file_code>/file`` form, then queries that API inside a warm FlareSolverr
session so Cloudflare is cleared and the returned cf_clearance + User-Agent come
back for the caller to replay against the (gated) direct file URL. FlareSolverr
wraps a JSON response in ``<pre>...</pre>``.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import quote, urlsplit

from ..models import ResolveRequest, ResolveResponse
from ..solver import FlareSolverrClient, SolverError
from .base import Recipe

API = "https://akirabox.com/api/files"

_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
# The public File Status API reports size as a display string (e.g. "1.2 MB").
_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?i?B)", re.IGNORECASE)
_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


class AkiraBoxRecipe(Recipe):
    name = "akirabox"
    hosts = ("akirabox", "akirabox.com")
    # One warm FlareSolverr session, reused so the Cloudflare solve is paid once.
    SESSION = "slipgate-akirabox"

    async def resolve(self, client: FlareSolverrClient, req: ResolveRequest) -> ResolveResponse:
        if not req.page_url:
            return ResolveResponse(ok=False, error="missing page_url")
        canonical = _canonical(req.page_url)
        if not canonical:
            return ResolveResponse(ok=False, error="unrecognized akirabox url")
        api_url = f"{API}?url={quote(canonical, safe='')}"

        async with client.session_lock(self.SESSION):
            for attempt in (1, 2):
                try:
                    await client.ensure_session(self.SESSION)
                    res = await client.get(api_url, session=self.SESSION)
                    data = _json(res.response_text)
                    if not isinstance(data, dict):
                        return ResolveResponse(ok=False, error="no metadata from akirabox api")
                    link = str(data.get("url") or data.get("link") or "")
                    status = int(data.get("status") or 200)
                    if link and status == 200:
                        return ResolveResponse(
                            ok=True,
                            download_url=link,
                            file_name=str(data.get("name") or ""),
                            size_bytes=_size_bytes(data.get("size")),
                            cookies=res.cookies,
                            user_agent=res.user_agent,
                        )
                    return ResolveResponse(ok=False, error=str(data.get("message") or "file not available"))
                except SolverError as exc:
                    await client.reset_session(self.SESSION)
                    if attempt == 2:
                        return ResolveResponse(ok=False, error=str(exc))
        return ResolveResponse(ok=False, error="resolve failed")


def _canonical(page_url: str) -> str:
    """Normalize a hoster page URL to Akira Box's canonical /<file_code>/file form."""
    parts = urlsplit(page_url)
    if not parts.netloc:
        return ""
    segs = [s for s in parts.path.split("/") if s]
    if not segs:
        return ""
    return f"{parts.scheme or 'https'}://{parts.netloc}/{segs[0]}/file"


def _json(text: str):
    if not text:
        return None
    m = _PRE_RE.search(text)
    raw = html.unescape(m.group(1)).strip() if m else text.strip()
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _size_bytes(size) -> int:
    if isinstance(size, (int, float)):
        return int(size)
    if isinstance(size, str):
        s = size.strip()
        if s.isdigit():
            return int(s)
        m = _SIZE_RE.search(s)
        if m:
            unit = m.group(2).upper().replace("IB", "B")
            return int(float(m.group(1)) * _UNITS.get(unit, 1))
    return 0
