"""HTTP API.

Two endpoints: `/health` for liveness and capability discovery (including whether
the configured FlareSolverr is reachable), and `POST /resolve` which runs a
per-host recipe through FlareSolverr and returns a direct download URL. An
optional API key guards `/resolve`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException

from . import __version__
from .config import Settings, get_settings
from .models import HealthResponse, ResolveRequest, ResolveResponse
from .recipes import get_recipe, recipe_names
from .solver import FlareSolverrClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Honor a client injected before startup (the test suite does this to run
    # without a real FlareSolverr); otherwise build the configured one.
    client = getattr(app.state, "solver", None) or FlareSolverrClient(
        settings.flaresolverr_url,
        settings.flaresolverr_timeout_ms,
        settings.flaresolverr_http_timeout_secs,
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
