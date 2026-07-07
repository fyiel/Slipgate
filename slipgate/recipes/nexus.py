"""NexusMods free manual-download recipe (via FlareSolverr).

Free accounts get direct download links only through the website, behind a
Cloudflare gate and a short wait. FlareSolverr clears the gate; this recipe drives
it: it loads the file page in a FlareSolverr session (so the generate call shares
the same cleared, logged-in browser context and referer), waits out the free
countdown, then POSTs the same GenerateDownloadUrl endpoint the site's "Slow
download" button uses. Nexus answers with a JSON array of CDN mirrors; the first
entry's URI is the direct download URL.

The caller supplies a logged-in `nexusmods_session` cookie once; FlareSolverr
mints the Cloudflare clearance itself.

Inputs (ResolveRequest.params): domain, mod_id, file_id, game_id.
game_id is the numeric NexusMods game id; the caller already resolves it.
"""

from __future__ import annotations

import asyncio
import json
import re
from html import unescape

from ..models import Cookie, ResolveRequest, ResolveResponse
from ..solver import FlareSolverrClient, SolverError, SolverResult
from .base import Recipe

WWW = "https://www.nexusmods.com"
GENERATE_URL = f"{WWW}/Core/Libs/Common/Managers/Downloads?GenerateDownloadUrl"

# Nexus enforces a short countdown before the free generate call succeeds. This
# matches the site's wait; tune against live Nexus if it changes.
FREE_WAIT_SECS = 6.0

# FlareSolverr renders a JSON response inside Chrome's viewer, so the body arrives
# wrapped in <pre>...</pre>. Pull the JSON back out of it.
_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)


class NexusRecipe(Recipe):
    name = "nexusmods"
    hosts = ("nexusmods", "nexus", "nexusmods.com")
    # One warm FlareSolverr session, reused across resolves so the browser and
    # Cloudflare solve are paid once. Requests serialize on the session's lock.
    SESSION = "slipgate-nexusmods"

    async def resolve(self, client: FlareSolverrClient, req: ResolveRequest) -> ResolveResponse:
        missing = [k for k in ("domain", "mod_id", "file_id", "game_id") if not req.params.get(k)]
        if missing:
            return ResolveResponse(ok=False, error=f"missing params: {', '.join(missing)}")
        domain = req.params["domain"]
        mod_id = req.params["mod_id"]
        file_id = req.params["file_id"]
        game_id = req.params["game_id"]

        if not any(c.name == "nexusmods_session" for c in req.cookies):
            return ResolveResponse(ok=False, error="no nexusmods_session cookie supplied")
        cookies = [Cookie(name=c.name, value=c.value, domain=".nexusmods.com", path="/") for c in req.cookies]

        file_page = req.page_url or f"{WWW}/{domain}/mods/{mod_id}?tab=files&file_id={file_id}"
        body = f"fid={file_id}&game_id={game_id}"

        res: SolverResult | None = None
        async with client.session_lock(self.SESSION):
            # Reuse the warm session; on a session error (expiry / FlareSolverr
            # restart) reset it and retry once with a fresh one.
            for attempt in (1, 2):
                try:
                    await client.ensure_session(self.SESSION)
                    await client.get(file_page, cookies=cookies, session=self.SESSION)
                    await asyncio.sleep(FREE_WAIT_SECS)
                    res = await client.post(GENERATE_URL, body, cookies=cookies, session=self.SESSION)
                    break
                except SolverError as exc:
                    await client.reset_session(self.SESSION)
                    if attempt == 2:
                        return ResolveResponse(ok=False, error=str(exc))

        if res is None:
            return ResolveResponse(ok=False, error="resolve failed")
        url = _extract_uri(res.response_text)
        if not url:
            return ResolveResponse(
                ok=False,
                error="could not obtain a download url; the session may be logged out "
                "or the free wait was too short",
            )
        return ResolveResponse(ok=True, download_url=url, cookies=res.cookies, user_agent=res.user_agent)


def _extract_uri(text: str) -> str:
    """Pull the first CDN URI out of the generate response. Nexus returns an array
    of mirror objects (each with a URI); a logged-out request returns an empty
    array, which yields no URL."""
    if not text:
        return ""
    match = _PRE_RE.search(text)
    raw = unescape(match.group(1)).strip() if match else text.strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return str(data[0].get("URI") or data[0].get("uri") or "")
    if isinstance(data, dict):
        return str(data.get("url") or data.get("URI") or "")
    return ""
