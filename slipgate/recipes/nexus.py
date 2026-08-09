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

# Nexus enforces a short countdown before the free generate call succeeds. It
# starts when the file page finishes loading in the browser, so the recipe waits
# it out after visiting the page. The site's wait is ~5s; be generous and retry
# in case a fresh browser context needs longer.
FREE_WAIT_SECS = 10.0
POST_RETRIES = 3

# FlareSolverr renders a JSON response inside Chrome's viewer, so the body arrives
# wrapped in <pre>...</pre>. Pull the JSON back out of it.
_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
# Cloudflare re-challenging a navigation mid-flow.
_CHALLENGE_RE = re.compile(
    r"Just a moment|challenge-platform|cf-chl-|__cf_chl", re.IGNORECASE
)


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
            # On a session error (expiry / FlareSolverr restart) reset and retry
            # once with a fresh session.
            for attempt in (1, 2):
                try:
                    await client.ensure_session(self.SESSION)
                    # Warm the browser context on the file page first: this mints
                    # the Cloudflare clearance, seeds the session cookie into the
                    # browser (so the generate POST shares the logged-in context),
                    # sets the referer Nexus expects, and starts the free
                    # countdown. POSTing the generate endpoint cold from a fresh
                    # context fails with an empty array or a challenge, which is
                    # exactly the "could not obtain a download url" symptom.
                    await client.get(file_page, cookies=cookies, session=self.SESSION)
                    await asyncio.sleep(FREE_WAIT_SECS)
                    for _ in range(POST_RETRIES):
                        res = await client.post(
                            GENERATE_URL, body, cookies=cookies, session=self.SESSION
                        )
                        if _extract_uri(res.response_text):
                            break
                        # Either the countdown is still running or Cloudflare
                        # re-challenged the POST; wait and retry before giving up.
                        await asyncio.sleep(FREE_WAIT_SECS)
                    break
                except SolverError as exc:
                    await client.reset_session(self.SESSION)
                    if attempt == 2:
                        return ResolveResponse(ok=False, error=str(exc))

        if res is None:
            return ResolveResponse(ok=False, error="resolve failed")

        if _CHALLENGE_RE.search(res.response_text or ""):
            return ResolveResponse(
                ok=False,
                error="Cloudflare blocked the download request even after solving — "
                "retry, or check that FlareSolverr is healthy",
            )
        url = _extract_uri(res.response_text)
        if not url:
            if _is_empty_array(res.response_text):
                return ResolveResponse(
                    ok=False,
                    error="NexusMods returned no download url — the session appears "
                    "logged out; paste a fresh nexusmods_session cookie in Settings",
                )
            return ResolveResponse(
                ok=False,
                error="could not obtain a download url; the session may be logged out "
                "or the free wait was too short",
            )
        return ResolveResponse(ok=True, download_url=url, cookies=res.cookies, user_agent=res.user_agent)


def _parse_json(text: str):
    """Parse the JSON out of a FlareSolverr response, or return None."""
    if not text:
        return None
    match = _PRE_RE.search(text)
    raw = unescape(match.group(1)).strip() if match else text.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _is_empty_array(text: str) -> bool:
    """A logged-out generate call returns an empty mirror array, `[]`."""
    data = _parse_json(text)
    return isinstance(data, list) and not data


def _extract_uri(text: str) -> str:
    """Pull the first CDN URI out of the generate response. Nexus returns an array
    of mirror objects (each with a URI); a logged-out request returns an empty
    array, which yields no URL."""
    data = _parse_json(text)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return str(data[0].get("URI") or data[0].get("uri") or "")
    if isinstance(data, dict):
        return str(data.get("url") or data.get("URI") or "")
    return ""
