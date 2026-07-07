"""Runtime configuration, read from the environment with SLIPGATE_ prefix.

Every value has a safe default so `docker compose up` works with no .env, yet an
operator can lock the service down (bind address, API key) for anything beyond a
loopback deployment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SLIPGATE_", env_file=".env", extra="ignore")

    # Bind to loopback by default. Replaying a logged-in session is sensitive, so
    # the service must be opted into a wider bind explicitly.
    host: str = "127.0.0.1"
    port: int = 8191

    # When set, every request must carry `X-Slipgate-Key` with this value. Empty
    # means no auth, which is only safe on a loopback bind.
    api_key: str = ""

    # Chrome runs headless by default. An operator debugging a stubborn gate can
    # flip this to watch the browser solve the challenge.
    headless: bool = True
    # Chrome's setuid sandbox needs privileges most containers lack, so a Docker
    # deployment sets this false. Leave it true on a normal desktop host.
    sandbox: bool = True
    # Explicit Chrome/Chromium binary path; empty lets nodriver auto-detect.
    browser_path: str = ""
    # Extra flags passed to Chrome, comma separated.
    browser_args: str = ""

    # How long a single resolve may take end to end, including the challenge.
    resolve_timeout_secs: float = 90.0
    # How long to wait for a Cloudflare interstitial to clear on its own.
    challenge_timeout_secs: float = 40.0
    # Ceiling on concurrent browser tabs so a burst cannot exhaust memory.
    max_concurrency: int = 2

    log_level: str = "info"

    @property
    def browser_arg_list(self) -> list[str]:
        return [a.strip() for a in self.browser_args.split(",") if a.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
