"""HTTP API.

Two endpoints: `/health` for liveness and capability discovery, and
`POST /resolve` which runs a per-host recipe inside the browser engine and
returns a direct download URL. An optional API key guards every non-health route.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException

from . import __version__
from .config import Settings, get_settings
from .engine import ChallengeError, build_engine, page
from .models import HealthResponse, ResolveRequest, ResolveResponse
from .recipes import get_recipe, recipe_names


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Honor an engine injected before startup (the test suite does this to run
    # without Chrome); otherwise build the default nodriver engine.
    engine = getattr(app.state, "engine", None) or build_engine(settings)
    app.state.engine = engine
    # Start the browser eagerly so the first resolve is not slowed by launch, but
    # never let a launch failure crash the service: /health will report it.
    try:
        await engine.startup()
    except Exception as exc:  # noqa: BLE001 - surfaced via /health, not fatal
        app.state.engine_error = str(exc)
    try:
        yield
    finally:
        await engine.shutdown()


app = FastAPI(title="Slipgate", version=__version__, lifespan=lifespan)


def require_key(
    x_slipgate_key: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.api_key and x_slipgate_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-Slipgate-Key")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    engine = getattr(app.state, "engine", None)
    ready = bool(engine) and await engine.ready()
    return HealthResponse(ok=True, version=__version__, engine_ready=ready, recipes=recipe_names())


@app.post("/resolve", response_model=ResolveResponse, dependencies=[Depends(require_key)])
async def resolve(req: ResolveRequest, settings: Settings = Depends(get_settings)) -> ResolveResponse:
    recipe = get_recipe(req.host)
    if recipe is None:
        return ResolveResponse(ok=False, error=f"no recipe for host '{req.host}'")

    engine = getattr(app.state, "engine", None)
    if engine is None or not await engine.ready():
        detail = getattr(app.state, "engine_error", "browser engine is not ready")
        return ResolveResponse(ok=False, error=detail)

    try:
        async with page(engine) as p:
            return await asyncio.wait_for(recipe.resolve(p, req), timeout=settings.resolve_timeout_secs)
    except TimeoutError:
        return ResolveResponse(ok=False, error="resolve timed out")
    except ChallengeError as exc:
        return ResolveResponse(ok=False, error=str(exc), needs_interactive=exc.needs_interactive)
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the client
        return ResolveResponse(ok=False, error=f"resolve failed: {exc}")
