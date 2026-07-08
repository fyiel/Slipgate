"""ViKiNG FiLE download recipe (via FlareSolverr).

A ViKiNG FiLE page (``https://vikingfile.com/f/<hash>``) is a JS app gated by a
Cloudflare Turnstile widget. When the widget is solved the page's own script
POSTs the same ``/f/<hash>`` URL (``application/x-www-form-urlencoded``, body
``cf-turnstile-response=<token>&ipv4=&ipv6=``) and gets back JSON
``{"link": ..., "name": ..., "size": ...}``; ``link`` is the direct download
server URL, which the page then hangs off its download button
(``<a id="download-link" href="...">``).

FlareSolverr clears the Turnstile in a real browser, so this recipe warms a
session on the file page and lets that script run: the returned HTML then carries
the populated ``download-link`` anchor (and/or the JSON). As a fallback it
re-POSTs the file URL inside the cleared context and parses the JSON body
(FlareSolverr wraps a JSON response in ``<pre>...</pre>``).
"""

from __future__ import annotations

import html
import json
import re

from ..models import ResolveRequest, ResolveResponse
from ..solver import FlareSolverrClient, SolverError
from .base import Recipe

# The page's own download XHR body. FlareSolverr supplies the cleared Turnstile
# context; the token itself is minted by the browser, not by us.
_POST_BODY = "cf-turnstile-response=&ipv4=&ipv6="

# FlareSolverr renders a JSON response inside Chrome's viewer, so it arrives
# wrapped in <pre>...</pre>.
_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
_DL_ANCHOR_RE = re.compile(r'id="download-link"[^>]*href="([^"#][^"]*)"', re.IGNORECASE)
_NAME_EL_RE = re.compile(r'id="name"[^>]*>\s*([^<]+?)\s*<', re.IGNORECASE)


class VikingFileRecipe(Recipe):
    name = "vikingfile"
    hosts = ("vikingfile", "vikingfile.com", "vik1ngfile.site")
    # One warm FlareSolverr session, reused so the Turnstile solve is paid once.
    SESSION = "slipgate-vikingfile"

    async def resolve(self, client: FlareSolverrClient, req: ResolveRequest) -> ResolveResponse:
        if not req.page_url:
            return ResolveResponse(ok=False, error="missing page_url")
        file_url = req.page_url

        async with client.session_lock(self.SESSION):
            for attempt in (1, 2):
                try:
                    await client.ensure_session(self.SESSION)
                    # GET clears Turnstile; the page's own script may populate the link.
                    res = await client.get(file_url, session=self.SESSION)
                    found = _extract(res.response_text)
                    if not found["link"]:
                        # Fallback: drive the download POST inside the cleared context.
                        res = await client.post(file_url, _POST_BODY, session=self.SESSION)
                        found = _extract(res.response_text)
                    if found["link"]:
                        return ResolveResponse(
                            ok=True,
                            download_url=found["link"],
                            file_name=found["name"],
                            size_bytes=found["size"],
                            cookies=res.cookies,
                            user_agent=res.user_agent,
                        )
                    return ResolveResponse(
                        ok=False,
                        error="no download link; the Turnstile gate may be unsolved",
                    )
                except SolverError as exc:
                    await client.reset_session(self.SESSION)
                    if attempt == 2:
                        return ResolveResponse(ok=False, error=str(exc))
        return ResolveResponse(ok=False, error="resolve failed")


def _extract(text: str) -> dict:
    """Pull {link, name, size} out of either the JSON download response or the
    populated download-link anchor in the file page HTML."""
    out = {"link": "", "name": "", "size": 0}
    if not text:
        return out
    m = _PRE_RE.search(text)
    raw = html.unescape(m.group(1)).strip() if m else text.strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict) and data.get("link"):
        out["link"] = str(data["link"])
        out["name"] = str(data.get("name") or "")
        out["size"] = _as_int(data.get("size"))
        return out
    # HTML shape: the page's script filled in the download button's href.
    am = _DL_ANCHOR_RE.search(text)
    if am:
        href = html.unescape(am.group(1))
        if href.lower().startswith("http"):
            out["link"] = href
            nm = _NAME_EL_RE.search(text)
            out["name"] = html.unescape(nm.group(1)) if nm else ""
    return out


def _as_int(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0
