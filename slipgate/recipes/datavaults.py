"""DataVaults free-download recipe (XFileSharing).

DataVaults runs XFileSharing (XFS): the free flow is a two-step form POST against
the file page. Step one (``op=download1``) returns the download2 page, which
carries a long ``rand`` token, a short countdown (``<span id="seconds">N</span>``)
and an XFS positional-digit captcha. Step two (``op=download2``) submits the token
plus the solved captcha and, once the countdown elapses, 302-redirects to the
direct CDN file URL (a ``d<N>.datavaults.co/d/<token>/<name>`` link).

That last step is a redirect to a file download, which a real browser turns into
a download rather than a navigation, so FlareSolverr never surfaces the Location.
So we use FlareSolverr only to clear any Cloudflare gate and adopt a matching
User-Agent + cookies, then replay the two form POSTs with a plain HTTP client that
keeps redirects OFF and reads the 302 ``Location`` directly.

The captcha is the classic XFS form: each digit is an absolutely-positioned
``<span>`` whose ``padding-left`` fixes its left-to-right order and whose glyph is
an HTML entity (or a bare digit). Reading the digits by ascending padding-left
recovers the code deterministically, so this recipe solves it rather than bailing.
"""

from __future__ import annotations

import asyncio
import html
import re
from urllib.parse import urlencode, urlsplit

import httpx

from ..models import ResolveRequest, ResolveResponse
from ..solver import FlareSolverrClient, SolverError
from .base import Recipe

# Fallback UA when FlareSolverr is unavailable; DataVaults is normally un-gated.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# XFS enforces a short countdown before download2 succeeds. The page ships the
# real value; we honor at least this floor so a spoofed tiny value cannot rush
# the wait, and cap it so a bogus large value cannot stall a resolve. Both are
# monkeypatched to 0 in tests so the suite never sleeps.
WAIT_SECS = 15.0
WAIT_CAP_SECS = 60.0
HTTP_TIMEOUT = 45.0

_RAND_RE = re.compile(r'name="rand"\s+value="([^"]+)"', re.IGNORECASE)
_SECONDS_RE = re.compile(r'id="seconds"[^>]*>\s*(\d+)', re.IGNORECASE)
# Each captcha digit is a positioned span: the padding-left px is its order, the
# body is an HTML entity (e.g. &#50;) or a bare digit.
_CAPTCHA_SPAN_RE = re.compile(r"padding-left:\s*(\d+)px;[^>]*>\s*(&#\d+;|\d)\s*</span>", re.IGNORECASE)
_BLOCKED_RE = re.compile(r"Wrong captcha|Skip countdown|have to wait|expired", re.IGNORECASE)
# The direct link is a CDN URL carrying a /d/ token and a file extension.
_DIRECT_RE = re.compile(
    r"""https?://[^\s"'<>]+?/d/[^\s"'<>]+?\.(?:zip|rar|7z|exe|bin|iso)(?:\?[^\s"'<>]*)?""",
    re.IGNORECASE,
)
_EXT_HREF_RE = re.compile(
    r'href="(https?://[^"]+?\.(?:zip|rar|7z|exe|bin|iso)(?:\?[^"]*)?)"', re.IGNORECASE
)


class DataVaultsRecipe(Recipe):
    name = "datavaults"
    hosts = ("datavaults", "datavaults.co")
    # One warm FlareSolverr session, reused so any Cloudflare solve is paid once.
    SESSION = "slipgate-datavaults"

    async def resolve(self, client: FlareSolverrClient, req: ResolveRequest) -> ResolveResponse:
        if not req.page_url:
            return ResolveResponse(ok=False, error="missing page_url")
        parts = urlsplit(req.page_url)
        segs = [s for s in parts.path.split("/") if s]
        if not parts.netloc or len(segs) < 2:
            return ResolveResponse(ok=False, error="unrecognized datavaults url")
        file_id, fname = segs[0], segs[-1]

        # Clear any Cloudflare gate and adopt the browser's UA + cookies so the
        # plain-client form POSTs below present a matching, cleared session. If
        # the solver is down, proceed anyway: DataVaults is normally un-gated.
        ua, seed = DEFAULT_UA, {}
        try:
            async with client.session_lock(self.SESSION):
                await client.ensure_session(self.SESSION)
                warm = await client.get(req.page_url, session=self.SESSION)
            ua = warm.user_agent or DEFAULT_UA
            seed = {c.name: c.value for c in warm.cookies}
        except SolverError:
            pass

        try:
            url, reason = await _form_flow(req.page_url, file_id, fname, ua, seed)
        except httpx.HTTPError as exc:
            return ResolveResponse(ok=False, error=f"datavaults request failed: {exc}")
        if url:
            return ResolveResponse(ok=True, download_url=url, file_name=fname, user_agent=ua)
        return ResolveResponse(ok=False, error=reason, needs_interactive=False)


async def _form_flow(
    page_url: str, file_id: str, fname: str, ua: str, seed_cookies: dict[str, str]
) -> tuple[str, str]:
    """Run the XFS download1 -> download2 form flow with a plain client (redirects
    OFF) and return ``(direct_url, reason)``; ``reason`` is set only on failure.
    DataVaults 302-redirects to the CDN link on success, which the browser can't
    surface, so we capture the Location here."""
    hdr = {"Referer": page_url, "Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(
        headers={"User-Agent": ua},
        cookies=seed_cookies,
        follow_redirects=False,
        timeout=httpx.Timeout(HTTP_TIMEOUT),
    ) as http:
        await http.get(page_url)  # pick up the XFS session cookies
        dl1 = urlencode(
            {
                "op": "download1",
                "usr_login": "",
                "id": file_id,
                "fname": fname,
                "referer": "",
                "method_free": "Free Download",
            }
        )
        page2 = (await http.post(page_url, content=dl1, headers=hdr)).text
        rand = _first(_RAND_RE, page2)
        if not rand:
            direct = _direct_url(page2, page_url)
            return (direct, "") if direct else ("", _reason(page2))
        code = _solve_captcha(page2)
        await asyncio.sleep(_wait_secs(page2))
        dl2 = urlencode(
            {
                "op": "download2",
                "id": file_id,
                "rand": rand,
                "referer": page_url,
                "method_free": "Free Download",
                "method_premium": "",
                "code": code,
            }
        )
        r2 = await http.post(page_url, content=dl2, headers=hdr)
        if r2.status_code in (301, 302, 303, 307, 308):
            loc = r2.headers.get("location", "")
            if loc:
                return (loc, "")
        direct = _direct_url(r2.text, page_url)
        return (direct, "") if direct else ("", _reason(r2.text))


def _first(rx: re.Pattern[str], text: str) -> str:
    m = rx.search(text or "")
    return m.group(1) if m else ""


def _solve_captcha(text: str) -> str:
    spans = _CAPTCHA_SPAN_RE.findall(text or "")
    ordered = sorted(spans, key=lambda t: int(t[0]))
    return "".join(html.unescape(glyph) for _, glyph in ordered)


def _wait_secs(text: str) -> float:
    m = _SECONDS_RE.search(text or "")
    parsed = float(m.group(1)) if m else 0.0
    return min(max(parsed, WAIT_SECS), WAIT_CAP_SECS)


def _direct_url(text: str, page_url: str) -> str:
    """Pull the direct CDN link out of a page. Prefer a /d/ token URL; fall back
    to any file-extension href on a different host than the hoster page (which
    excludes the page's own URL, itself ending in the extension)."""
    if not text:
        return ""
    text = html.unescape(text)
    m = _DIRECT_RE.search(text)
    if m:
        return m.group(0)
    page_host = urlsplit(page_url).netloc.lower()
    page_key = page_url.rstrip("/")
    for href in _EXT_HREF_RE.findall(text):
        host = urlsplit(href).netloc.lower()
        if host and host != page_host and href.rstrip("/") != page_key:
            return href
    return ""


def _reason(text: str) -> str:
    m = _BLOCKED_RE.search(text or "")
    return m.group(0) if m else "no direct download link found"
