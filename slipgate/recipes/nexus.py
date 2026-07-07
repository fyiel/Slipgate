"""NexusMods free manual-download recipe.

Free accounts get direct download links only through the website, behind a
Cloudflare gate and a short wait. Doing this from a plain HTTP client fails on
Cloudflare's TLS/JS fingerprinting even with valid cookies. Here a real browser
loads the file page (clearing Cloudflare on its own), then calls the same
GenerateDownloadUrl endpoint the site's "Slow download" button uses, from inside
the page's own origin, and returns the signed CDN URL.

The caller seeds a logged-in `nexusmods_session` cookie once; the browser mints
cf_clearance itself, so no Cloudflare token needs to be pasted.

Inputs (ResolveRequest.params): domain, mod_id, file_id, game_id.
game_id is the numeric NexusMods game id; the caller already resolves it.
"""

from __future__ import annotations

import asyncio
import json

from ..config import get_settings
from ..engine import ChallengeError, Page, wait_cloudflare
from ..models import Cookie, ResolveRequest, ResolveResponse
from .base import Recipe

WWW = "https://www.nexusmods.com"

# Nexus enforces a short countdown before the free generate call succeeds. This
# matches the site's wait; the recipe agent should confirm the exact duration.
FREE_WAIT_SECS = 6.0

# Same endpoint the site's manual "Slow download" button posts to.
_GENERATE_JS = """
(async () => {
  try {
    const r = await fetch("/Core/Libs/Common/Managers/Downloads?GenerateDownloadUrl", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: "fid=%FID%&game_id=%GAME_ID%",
      credentials: "include",
    });
    const text = await r.text();
    try {
      const j = JSON.parse(text);
      return JSON.stringify({ ok: true, url: j.url || (Array.isArray(j) && j[0] && j[0].URI) || "" });
    } catch (e) {
      return JSON.stringify({ ok: false, error: "non-json response", status: r.status });
    }
  } catch (e) {
    return JSON.stringify({ ok: false, error: String(e) });
  }
})()
"""


class NexusRecipe(Recipe):
    name = "nexusmods"
    hosts = ("nexusmods", "nexus", "nexusmods.com")

    async def resolve(self, page: Page, req: ResolveRequest) -> ResolveResponse:
        missing = [k for k in ("domain", "mod_id", "file_id", "game_id") if not req.params.get(k)]
        if missing:
            return ResolveResponse(ok=False, error=f"missing params: {', '.join(missing)}")
        domain = req.params["domain"]
        mod_id = req.params["mod_id"]
        file_id = req.params["file_id"]
        game_id = req.params["game_id"]

        if not any(c.name == "nexusmods_session" for c in req.cookies):
            return ResolveResponse(ok=False, error="no nexusmods_session cookie supplied")

        # Seed the login session on the Nexus domain before navigating so the
        # first page load is already authenticated.
        await page.set_cookies(
            [Cookie(name=c.name, value=c.value, domain=".nexusmods.com", path="/") for c in req.cookies]
        )

        file_page = req.page_url or f"{WWW}/{domain}/mods/{mod_id}?tab=files&file_id={file_id}"
        await page.goto(file_page)
        try:
            await wait_cloudflare(page, get_settings().challenge_timeout_secs)
        except ChallengeError as e:
            return ResolveResponse(ok=False, error=str(e), needs_interactive=e.needs_interactive)

        # The generate call is rejected until the free countdown elapses.
        await asyncio.sleep(FREE_WAIT_SECS)

        script = _GENERATE_JS.replace("%FID%", file_id).replace("%GAME_ID%", game_id)
        raw = await page.eval(script)
        url = _extract_url(raw)

        if not url:
            # Fall back to clicking the visible manual-download control and
            # intercepting whatever file URL the page fires.
            url = await _click_and_capture(page)

        if not url:
            return ResolveResponse(
                ok=False,
                error="could not obtain a download url; the session may be logged out "
                "or the file page layout changed",
            )

        cookies = await page.get_cookies()
        return ResolveResponse(
            ok=True,
            download_url=url,
            user_agent=await page.user_agent(),
            cookies=cookies,
        )


def _extract_url(raw: object) -> str:
    """Parse the JSON string returned by the injected generate script."""
    if not raw:
        return ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(data, dict) and data.get("ok"):
        return str(data.get("url") or "")
    return ""


async def _click_and_capture(page: Page) -> str:
    """Best-effort fallback: click the manual/slow download button and capture
    the resulting download URL. Selectors are verified against live Nexus by the
    recipe owner; failure here simply yields an empty URL."""
    for selector in (
        "a#slowDownloadButton",
        "a.btn[href*='file_id']",
        "button[data-download-url]",
    ):
        if await page.wait_for_selector(selector, timeout=2.0):
            await page.click(selector)
            captured = await page.capture_download(timeout=15.0)
            if captured:
                return captured
    return ""
