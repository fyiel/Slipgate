"""HTTP API.

Two endpoints: `/health` for liveness and capability discovery (including whether
the configured FlareSolverr is reachable), and `POST /resolve` which runs a
per-host recipe through FlareSolverr and returns a direct download URL. An
optional API key guards `/resolve`.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException

from . import __version__
from .config import Settings, get_settings
from .models import FetchRequest, FetchResponse, HealthResponse, ResolveRequest, ResolveResponse
from .recipes import get_recipe, recipe_names
from .solver import FlareSolverrClient, SolverError


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Honor a client injected before startup (the test suite does this to run
    # without a real FlareSolverr); otherwise build the configured one.
    client = getattr(app.state, "solver", None) or FlareSolverrClient(
        settings.flaresolverr_url,
        settings.flaresolverr_timeout_ms,
        settings.flaresolverr_http_timeout_secs,
        proxy=settings.proxy_url,
    )
    app.state.solver = client
    try:
        yield
    finally:
        await client.close()


app = FastAPI(title="Slipgate", version=__version__, lifespan=lifespan)


def require_key(
    x_slipgate_key: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.api_key and x_slipgate_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-Slipgate-Key")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    client = getattr(app.state, "solver", None)
    ok = bool(client) and await client.reachable()
    return HealthResponse(ok=True, version=__version__, flaresolverr_ok=ok, recipes=recipe_names())


@app.post("/resolve", response_model=ResolveResponse, dependencies=[Depends(require_key)])
async def resolve(req: ResolveRequest, settings: Settings = Depends(get_settings)) -> ResolveResponse:
    recipe = get_recipe(req.host)
    if recipe is None:
        return ResolveResponse(ok=False, error=f"no recipe for host '{req.host}'")

    client = getattr(app.state, "solver", None)
    if client is None:
        return ResolveResponse(ok=False, error="solver client is not initialized")

    try:
        return await asyncio.wait_for(recipe.resolve(client, req), timeout=settings.resolve_timeout_secs)
    except TimeoutError:
        return ResolveResponse(ok=False, error="resolve timed out")
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the client
        return ResolveResponse(ok=False, error=f"resolve failed: {exc}")


def _document_text(response_html: str) -> str:
    """FlareSolverr returns the browser-rendered DOM. For a JSON endpoint Chrome
    wraps the body in its JSON viewer, so stripping tags and unescaping recovers
    the original JSON text; anything that does not parse as JSON is returned as
    the raw page HTML for the caller to handle."""
    if not response_html:
        return ""
    stripped = html.unescape(re.sub(r"<[^>]+>", "", response_html)).strip()
    if stripped[:1] in "{[":
        try:
            json.loads(stripped)
            return stripped
        except ValueError:
            pass
    return response_html


_FETCH_SESSION = "slipgate-fetch"


@app.post("/fetch", response_model=FetchResponse, dependencies=[Depends(require_key)])
async def fetch(req: FetchRequest, settings: Settings = Depends(get_settings)) -> FetchResponse:
    """Fetch a URL through the solver's browser (and proxy, if configured) and
    return its body. Unlike /resolve this runs no per-host recipe: it exists to
    pull a Cloudflare-gated static resource (for example a source catalogue JSON)
    that a plain HTTP client cannot retrieve from a challenged IP. All fetches
    share one warm FlareSolverr session, so the Cloudflare solve is paid once and
    later same-origin fetches reuse the clearance cookie instead of re-solving.
    Requests serialize on that session's lock; on a session error (expiry /
    FlareSolverr restart) the session is reset and the fetch retried once."""
    client = getattr(app.state, "solver", None)
    if client is None:
        return FetchResponse(ok=False, error="solver client is not initialized")
    result = None
    try:
        async with client.session_lock(_FETCH_SESSION):
            for attempt in (1, 2):
                try:
                    await client.ensure_session(_FETCH_SESSION)
                    result = await asyncio.wait_for(
                        client.get(req.url, session=_FETCH_SESSION),
                        timeout=settings.resolve_timeout_secs,
                    )
                    break
                except SolverError:
                    await client.reset_session(_FETCH_SESSION)
                    if attempt == 2:
                        raise
    except TimeoutError:
        return FetchResponse(ok=False, error="fetch timed out")
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the client
        return FetchResponse(ok=False, error=f"fetch failed: {exc}")
    if result is None:
        return FetchResponse(ok=False, error="fetch failed")
    body = _document_text(result.response_text)
    ok = result.status == 200 and bool(body)
    return FetchResponse(
        ok=ok,
        status=result.status,
        body=body,
        error="" if ok else f"upstream status {result.status}",
    )
