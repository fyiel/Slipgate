"""DataVaults free-download recipe (XFileSharing, via FlareSolverr).

DataVaults runs XFileSharing (XFS): the free flow is a two-step form POST against
the file page. Step one (``op=download1``) returns the download2 page, which
carries a long ``rand`` token, a short countdown (``<span id="seconds">N</span>``)
and an XFS positional-digit captcha. Step two (``op=download2``) submits the
token plus the solved captcha and, once the countdown has elapsed, 302-redirects
to the direct CDN file URL (a ``d<N>.datavaults.co/d/<token>/<name>`` link on a
different host than the hoster page).

The captcha is the classic XFS form: each digit is an absolutely-positioned
``<span>`` whose ``padding-left`` fixes its left-to-right order, and whose glyph
is an HTML entity (or a bare digit). Reading the digits by ascending padding-left
recovers the code deterministically, so this recipe solves it rather than bailing.

DataVaults has no Cloudflare gate, so the whole flow runs in one warm FlareSolverr
session (which also supplies the real browser User-Agent, cookies and obfuscated
anti-bot JS execution) so the download1 -> download2 cookies persist.
"""

from __future__ import annotations

import asyncio
import html
import re
from urllib.parse import urlencode, urlsplit

from ..models import ResolveRequest, ResolveResponse
from ..solver import FlareSolverrClient, SolverError, SolverResult
from .base import Recipe

# XFS enforces a short countdown before download2 succeeds. The page ships the
# real value; we honor at least this floor so a spoofed tiny value cannot rush
# the wait, and cap it so a bogus large value cannot stall a resolve. Both are
# monkeypatched to 0 in tests so the suite never sleeps.
WAIT_SECS = 15.0
WAIT_CAP_SECS = 60.0

_RAND_RE = re.compile(r'name="rand"\s+value="([^"]+)"', re.IGNORECASE)
_SECONDS_RE = re.compile(r'id="seconds"[^>]*>\s*(\d+)', re.IGNORECASE)
# Each captcha digit is a positioned span: the padding-left px is its order, the
# body is an HTML entity (e.g. &#50;) or a bare digit.
_CAPTCHA_SPAN_RE = re.compile(r"padding-left:\s*(\d+)px;[^>]*>\s*(&#\d+;|\d)\s*</span>", re.IGNORECASE)
_BLOCKED_RE = re.compile(r"Wrong captcha|Skip countdown|have to wait", re.IGNORECASE)
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
    # One warm FlareSolverr session, reused across resolves. Requests serialize
    # on the session lock so the two-step form's cookies never interleave.
    SESSION = "slipgate-datavaults"

    async def resolve(self, client: FlareSolverrClient, req: ResolveRequest) -> ResolveResponse:
        if not req.page_url:
            return ResolveResponse(ok=False, error="missing page_url")
        parts = urlsplit(req.page_url)
        segs = [s for s in parts.path.split("/") if s]
        if not parts.netloc or len(segs) < 2:
            return ResolveResponse(ok=False, error="unrecognized datavaults url")
        file_id, fname = segs[0], segs[-1]

        async with client.session_lock(self.SESSION):
            for attempt in (1, 2):
                try:
                    await client.ensure_session(self.SESSION)
                    # Warm the session and pick up the XFS cookies from the page.
                    await client.get(req.page_url, session=self.SESSION)
                    # Step 1: op=download1 -> the download2 page (rand + captcha + countdown).
                    dl1 = _form(
                        op="download1",
                        usr_login="",
                        id=file_id,
                        fname=fname,
                        referer="",
                        method_free="Free Download",
                    )
                    page2 = await client.post(req.page_url, dl1, session=self.SESSION)
                    rand = _first(_RAND_RE, page2.response_text)
                    if not rand:
                        # No token: maybe already the final page, otherwise gated.
                        url = _direct_url(page2.response_text, req.page_url)
                        return _ok(url, fname, page2) if url else _blocked(page2.response_text)
                    code = _solve_captcha(page2.response_text)
                    await asyncio.sleep(_wait_secs(page2.response_text))
                    # Step 2: op=download2 -> the final page / redirect to the direct URL.
                    dl2 = _form(
                        op="download2",
                        id=file_id,
                        rand=rand,
                        referer=req.page_url,
                        method_free="Free Download",
                        method_premium="",
                        code=code,
                    )
                    final = await client.post(req.page_url, dl2, session=self.SESSION)
                    url = _direct_url(final.response_text, req.page_url)
                    return _ok(url, fname, final) if url else _blocked(final.response_text)
                except SolverError as exc:
                    await client.reset_session(self.SESSION)
                    if attempt == 2:
                        return ResolveResponse(ok=False, error=str(exc))
        return ResolveResponse(ok=False, error="resolve failed")


def _form(**fields: str) -> str:
    # urlencode keeps insertion order and turns "Free Download" into "Free+Download".
    return urlencode(fields)


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
    """Pull the direct CDN link out of the final page. Prefer a /d/ token URL;
    fall back to any file-extension href on a different host than the hoster page
    (which excludes the page's own URL, itself ending in the extension)."""
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


def _ok(url: str, fname: str, res: SolverResult) -> ResolveResponse:
    return ResolveResponse(
        ok=True,
        download_url=url,
        file_name=fname,
        cookies=res.cookies,
        user_agent=res.user_agent,
    )


def _blocked(text: str) -> ResolveResponse:
    m = _BLOCKED_RE.search(text or "")
    reason = m.group(0) if m else "no direct download link found"
    return ResolveResponse(ok=False, error=reason, needs_interactive=False)
