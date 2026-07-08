"""Runtime configuration, read from the environment with the SLIPGATE_ prefix.

Every value has a safe default so the bundled compose file works with no .env,
yet an operator can lock the service down (bind address, API key) and point it at
whatever FlareSolverr instance they run.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SLIPGATE_", env_file=".env", extra="ignore")

    # Bind to loopback by default. Replaying a logged-in session is sensitive, so
    # a wider bind must be opted into explicitly (and paired with an API key).
    host: str = "127.0.0.1"
    # Not 8191: that is FlareSolverr's default, which Slipgate talks to.
    port: int = 8189

    # When set, every /resolve must carry `X-Slipgate-Key` with this value.
    api_key: str = ""

    # The FlareSolverr /v1 endpoint that does the actual gate clearing.
    flaresolverr_url: str = "http://localhost:8191/v1"
    # maxTimeout handed to FlareSolverr per request, in milliseconds.
    flaresolverr_timeout_ms: int = 60000
    # HTTP read timeout for the call to FlareSolverr, in seconds. Must comfortably
    # exceed flaresolverr_timeout_ms so a slow challenge is not cut off locally.
    flaresolverr_http_timeout_secs: float = 90.0

    # Optional upstream proxy for the solver's browser. When set, every
    # FlareSolverr request routes through it — point it at a residential/clean
    # egress to reach hosts Cloudflare challenges by datacenter IP. Credentials
    # may be embedded (http://user:pass@host:port); Slipgate splits them out for
    # FlareSolverr, which takes username/password as separate fields.
    proxy_url: str = ""

    # Overall per-resolve ceiling (a recipe may make several FlareSolverr calls).
    resolve_timeout_secs: float = 150.0
    # Ceiling on concurrent resolves so a burst cannot exhaust FlareSolverr.
    max_concurrency: int = 2

    log_level: str = "info"


@lru_cache
def get_settings() -> Settings:
    return Settings()
