"""HTTP API.

Two endpoints: `/health` for liveness and capability discovery (including whether
the configured FlareSolverr is reachable), and `POST /resolve` which runs a
per-host recipe through FlareSolverr and returns a direct download URL. An
optional API key guards `/resolve`.
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response

from . import __version__
from .config import Settings, get_settings
from .models import (
    AniDbFetchRequest,
    AniDbSourceRequest,
    AniDbSourceResponse,
    FetchRequest,
    FetchResponse,
    HealthResponse,
    MangaFireFetchRequest,
    MangaFireImageRequest,
    ResolveRequest,
    ResolveResponse,
)
from .recipes import get_recipe, recipe_names
from .solver import FlareSolverrClient, SolverError
from .urlcheck import validate_url


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
_MANGAFIRE_API = re.compile(r"^/api/(?:titles(?:/[a-z0-9._-]+(?:/chapters)?)?|chapters/[a-z0-9._-]+)$", re.I)
_MANGAFIRE_SIGNER = re.compile(r"^/build/mf/assets/polyfill-[a-z0-9_-]+\.js$", re.I)
_MANGAFIRE_IMAGE_HOST = re.compile(r"(?:^|\.)mfcdn\d*\.xyz$", re.I)
_MANGAFIRE_REFERER = re.compile(r"^/title/[a-z0-9._-]+(?:-[a-z0-9._-]+)*/chapter/[a-z0-9._-]+$", re.I)
_MANGAFIRE_MAX_BODY = 6 * 1024 * 1024
_MANGAFIRE_MAX_IMAGE = 20 * 1024 * 1024
_ANIDB_SESSION = "slipgate-anidb"
_ANIDB_API = re.compile(r"^/api/frontend/(?:anime/\d+/episodes|episode/\d+/languages)$")
_ANIDB_EMBED = re.compile(r"^/embed/[a-z0-9_-]{20,200}$", re.I)
_ANIDB_HLS = re.compile(r"^/stream/([a-z0-9_-]{20,200})/master\.m3u8$", re.I)
_ANIDB_MEDIA_PATH = re.compile(r"^[a-z0-9._~/-]{1,500}$", re.I)
_ANIDB_MAX_BODY = 2 * 1024 * 1024
_ANIDB_MAX_PLAYLIST = 512 * 1024
_ANIDB_MAX_MEDIA = 8 * 1024 * 1024
_ANIDB_MEDIA_TTL = 20 * 60
_anidb_media: dict[str, tuple[str, float]] = {}


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
    allowed = {h.strip().lower() for h in settings.fetch_allowed_hosts.split(",") if h.strip()}
    if not allowed or not await validate_url(req.url, allowed, max_length=20_000):
        return FetchResponse(ok=False, error="fetch url not allowed")
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


def _mangafire_target(url: str) -> str | None:
    if len(url) > 20_000:
        return None
    try:
        target = urlsplit(url)
        port = target.port
    except ValueError:
        return None
    if (
        target.scheme != "https"
        or target.username
        or target.password
        or target.fragment
        or port not in (None, 443)
    ):
        return None
    if target.hostname == "mangafire.to" and _MANGAFIRE_API.fullmatch(target.path):
        return "api"
    if target.hostname == "s.mfcdn.nl" and not target.query and _MANGAFIRE_SIGNER.fullmatch(target.path):
        return "signer"
    return None


@app.post("/mangafire/fetch", response_model=FetchResponse, dependencies=[Depends(require_key)])
async def mangafire_fetch(
    req: MangaFireFetchRequest,
    settings: Settings = Depends(get_settings),
) -> FetchResponse:
    """Fetch only MangaFire's signed JSON API or its public signer bundle.

    This deliberately does not extend the generic browser fetch surface. The
    configured clean-egress proxy can reach these resources with ordinary HTTP
    even when Chrome/FlareSolverr is challenged, while the strict destination
    allowlist prevents the endpoint becoming an SSRF or open-proxy primitive.
    """
    target_type = _mangafire_target(req.url)
    if target_type is None:
        return FetchResponse(ok=False, error="unrecognized MangaFire resource")
    if not settings.proxy_url:
        return FetchResponse(ok=False, error="MangaFire proxy is not configured")

    try:
        async with httpx.AsyncClient(
            proxy=settings.proxy_url,
            timeout=30.0,
            follow_redirects=False,
        ) as client:
            response = await client.get(req.url, headers={
                "accept": "application/json" if target_type == "api" else "application/javascript",
                "referer": "https://mangafire.to/",
                "x-requested-with": "XMLHttpRequest",
            })
    except httpx.HTTPError:
        return FetchResponse(ok=False, error="MangaFire proxy request failed")

    body = response.text
    if len(response.content) > _MANGAFIRE_MAX_BODY:
        return FetchResponse(ok=False, status=response.status_code, error="MangaFire response too large")
    if response.status_code != 200 or not body:
        return FetchResponse(
            ok=False,
            status=response.status_code,
            error=f"upstream status {response.status_code}",
        )
    if target_type == "api":
        try:
            json.loads(body)
        except ValueError:
            return FetchResponse(
                ok=False,
                status=response.status_code,
                error="MangaFire returned a challenge",
            )

    return FetchResponse(ok=True, status=response.status_code, body=body)


def _anidb_target(url: str) -> str | None:
    if len(url) > 2_000:
        return None
    try:
        target = urlsplit(url)
        port = target.port
    except ValueError:
        return None
    if (
        target.scheme != "https"
        or target.hostname != "anidb.app"
        or target.username
        or target.password
        or target.fragment
        or port not in (None, 443)
    ):
        return None

    if target.path == "/browse":
        query = parse_qs(target.query, keep_blank_values=True)
        if set(query) == {"q"} and len(query["q"]) == 1 and 0 < len(query["q"][0]) <= 200:
            return "browse"
        return None
    if not target.query and _ANIDB_API.fullmatch(target.path):
        return "api"
    if not target.query and _ANIDB_EMBED.fullmatch(target.path):
        return "embed"
    return None


async def _anidb_get(url: str, settings: Settings) -> tuple[int, str]:
    client = getattr(app.state, "solver", None)
    if client is None:
        raise SolverError("solver client is not initialized")
    async with client.session_lock(_ANIDB_SESSION):
        for attempt in (1, 2):
            try:
                await client.ensure_session(_ANIDB_SESSION)
                result = await asyncio.wait_for(
                    client.get(url, session=_ANIDB_SESSION),
                    timeout=settings.resolve_timeout_secs,
                )
                body = _document_text(result.response_text)
                return result.status, body
            except SolverError:
                await client.reset_session(_ANIDB_SESSION)
                if attempt == 2:
                    raise
    raise SolverError("AniDB browser request failed")


@app.post("/anidb/fetch", response_model=FetchResponse, dependencies=[Depends(require_key)])
async def anidb_fetch(
    req: AniDbFetchRequest,
    settings: Settings = Depends(get_settings),
) -> FetchResponse:
    target_type = _anidb_target(req.url)
    if target_type is None:
        return FetchResponse(ok=False, error="unrecognized AniDB resource")

    try:
        status, body = await _anidb_get(req.url, settings)
    except TimeoutError:
        return FetchResponse(ok=False, error="AniDB fetch timed out")
    except Exception:  # noqa: BLE001 - return a stable transport error
        return FetchResponse(ok=False, error="AniDB fetch failed")
    if len(body.encode()) > _ANIDB_MAX_BODY:
        return FetchResponse(ok=False, status=status, error="AniDB response too large")
    if status != 200 or not body:
        return FetchResponse(
            ok=False,
            status=status,
            error=f"upstream status {status}",
        )
    if target_type == "api":
        try:
            json.loads(body)
        except ValueError:
            return FetchResponse(
                ok=False,
                status=status,
                error="AniDB returned a challenge",
            )
    return FetchResponse(ok=True, status=status, body=body)


def _anidb_hls_root(url: str) -> str | None:
    try:
        target = urlsplit(url)
        port = target.port
    except ValueError:
        return None
    match = _ANIDB_HLS.fullmatch(target.path)
    if (
        target.scheme != "https"
        or target.hostname != "hls.anidb.app"
        or target.username
        or target.password
        or target.query
        or target.fragment
        or port not in (None, 443)
        or not match
    ):
        return None
    return f"https://hls.anidb.app/stream/{match.group(1)}/"


def _anidb_source_id(series_id: int, episode_id: int) -> str:
    value = f"anidbapp:{series_id}:{episode_id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@app.post("/anidb/source", response_model=AniDbSourceResponse, dependencies=[Depends(require_key)])
async def anidb_source(
    req: AniDbSourceRequest,
    settings: Settings = Depends(get_settings),
) -> AniDbSourceResponse:
    language = "jpn" if req.language == "sub" else "eng"
    try:
        status, body = await _anidb_get(
            f"https://anidb.app/api/frontend/episode/{req.episode_id}/languages",
            settings,
        )
        if status != 200:
            return AniDbSourceResponse(ok=False, status=status, error=f"upstream status {status}")
        languages = json.loads(body).get("languages", [])
        embed = next((item.get("embed_url") for item in languages if item.get("code") == language), None)
        if not isinstance(embed, str) or _anidb_target(embed) != "embed":
            return AniDbSourceResponse(ok=False, status=404, error="requested language is unavailable")
        status, body = await _anidb_get(embed, settings)
        if status != 200:
            return AniDbSourceResponse(ok=False, status=status, error=f"upstream status {status}")
        match = re.search(r"\bfile\s*:\s*(['\"])(https://hls\.anidb\.app/[^'\"]+)\1", body, re.I)
        root = _anidb_hls_root(match.group(2)) if match else None
        if root is None:
            return AniDbSourceResponse(ok=False, status=502, error="AniDB returned no supported HLS source")
    except TimeoutError:
        return AniDbSourceResponse(ok=False, error="AniDB source timed out")
    except Exception:  # noqa: BLE001 - return a stable transport error
        return AniDbSourceResponse(ok=False, error="AniDB source failed")

    now = time.monotonic()
    for key, (_, expires) in list(_anidb_media.items()):
        if expires <= now:
            _anidb_media.pop(key, None)
    if len(_anidb_media) >= 500:
        _anidb_media.pop(next(iter(_anidb_media)))
    capability = secrets.token_urlsafe(24)
    _anidb_media[capability] = (root, now + _ANIDB_MEDIA_TTL)
    return AniDbSourceResponse(
        ok=True,
        status=200,
        provider="pewe",
        category=req.language,
        source_id=_anidb_source_id(req.series_id, req.episode_id),
        media_path=f"/anidb/media/{capability}/master.m3u8",
    )


_ANIDB_CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, HEAD, OPTIONS",
    "access-control-allow-headers": "Range",
    "access-control-expose-headers": "Content-Length, Content-Range, Accept-Ranges",
}


def _anidb_media_error(message: str, status: int) -> Response:
    return Response(
        json.dumps({"error": message}),
        status_code=status,
        media_type="application/json",
        headers=_ANIDB_CORS,
    )


@app.api_route(
    "/anidb/media/{capability}/{media_path:path}",
    methods=["GET", "HEAD", "OPTIONS"],
    dependencies=[Depends(require_key)],
)
async def anidb_media(
    request: Request,
    capability: str,
    media_path: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_ANIDB_CORS)
    record = _anidb_media.get(capability)
    if record is None or record[1] <= time.monotonic():
        _anidb_media.pop(capability, None)
        return _anidb_media_error("unknown or expired AniDB media capability", 404)
    if (
        not _ANIDB_MEDIA_PATH.fullmatch(media_path)
        or media_path.startswith("/")
        or any(part in {"", ".", ".."} for part in media_path.split("/"))
    ):
        return _anidb_media_error("invalid AniDB media path", 400)
    if not settings.proxy_url:
        return _anidb_media_error("AniDB media proxy is not configured", 503)

    root = record[0]
    headers = {
        "accept": "*/*",
        "referer": "https://anidb.app/",
        "user-agent": "Mozilla/5.0",
    }
    if request.headers.get("range"):
        headers["range"] = request.headers["range"]
    try:
        async with httpx.AsyncClient(
            proxy=settings.proxy_url,
            timeout=30.0,
            follow_redirects=False,
        ) as client:
            async with client.stream(request.method, root + media_path, headers=headers) as upstream:
                limit = _ANIDB_MAX_PLAYLIST if media_path.endswith(".m3u8") else _ANIDB_MAX_MEDIA
                declared = int(upstream.headers.get("content-length", "0") or 0)
                if declared > limit:
                    return _anidb_media_error("AniDB media response too large", 502)
                chunks = []
                size = 0
                async for chunk in upstream.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        return _anidb_media_error("AniDB media response too large", 502)
                    chunks.append(chunk)
                body = b"".join(chunks)
                status = upstream.status_code
                response_headers = {
                    key: value
                    for key, value in upstream.headers.items()
                    if key in {
                        "content-type",
                        "content-length",
                        "content-range",
                        "accept-ranges",
                        "cache-control",
                    }
                }
    except httpx.HTTPError:
        return _anidb_media_error("AniDB media request failed", 502)

    if status not in (200, 206):
        return _anidb_media_error(f"upstream status {status}", status)
    if media_path.endswith(".m3u8"):
        if not body.startswith(b"#EXTM3U"):
            return _anidb_media_error("AniDB returned an invalid playlist", 502)
        body = body.replace(root.encode(), b"")
        if b"http://" in body or b"https://" in body:
            return _anidb_media_error("AniDB playlist escaped its media root", 502)
        response_headers["content-type"] = "application/vnd.apple.mpegurl"
        response_headers["content-length"] = str(len(body))
    response_headers.update(_ANIDB_CORS)
    return Response(body if request.method == "GET" else b"", status_code=status, headers=response_headers)


def _mangafire_image_target(url: str, referer: str) -> bool:
    if len(url) > 20_000 or len(referer) > 2_000:
        return False
    try:
        target = urlsplit(url)
        source = urlsplit(referer)
        ports = (target.port, source.port)
    except ValueError:
        return False
    return (
        target.scheme == "https"
        and bool(target.hostname and _MANGAFIRE_IMAGE_HOST.search(target.hostname))
        and not target.username
        and not target.password
        and not target.fragment
        and ports[0] in (None, 443)
        and source.scheme == "https"
        and source.hostname == "mangafire.to"
        and not source.username
        and not source.password
        and not source.query
        and not source.fragment
        and ports[1] in (None, 443)
        and bool(_MANGAFIRE_REFERER.fullmatch(source.path))
    )


@app.post("/mangafire/image", dependencies=[Depends(require_key)])
async def mangafire_image(
    req: MangaFireImageRequest,
    settings: Settings = Depends(get_settings),
) -> Response:
    if not _mangafire_image_target(req.url, req.referer):
        raise HTTPException(status_code=400, detail="unrecognized MangaFire image")
    if not settings.proxy_url:
        raise HTTPException(status_code=503, detail="MangaFire proxy is not configured")
    try:
        async with httpx.AsyncClient(
            proxy=settings.proxy_url,
            timeout=30.0,
            follow_redirects=False,
        ) as client:
            upstream = await client.get(req.url, headers={
                "accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                "referer": req.referer,
            })
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="MangaFire image request failed") from exc

    content_type = upstream.headers.get("content-type", "")
    if upstream.status_code != 200 or not content_type.startswith("image/"):
        raise HTTPException(status_code=502, detail="MangaFire image unavailable")
    if len(upstream.content) > _MANGAFIRE_MAX_IMAGE:
        raise HTTPException(status_code=502, detail="MangaFire image too large")
    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"cache-control": "public, max-age=14400"},
    )
