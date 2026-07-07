"""Browser engine abstraction.

`Page` is the small surface a recipe needs; `Engine` hands out pages and owns the
browser lifecycle. Recipes depend ONLY on `Page`, so the real nodriver backend
and the in-memory test fake are interchangeable.

The nodriver backend is intentionally thin here: it launches one Chrome, opens a
tab per resolve, and exposes navigation, script evaluation, cookies, and a
download interception hook. The heavy anti-bot and challenge-clearing behavior
lives in nodriver itself (a real Chrome), which is what lets it pass gates a raw
HTTP client cannot.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable

from .config import Settings
from .models import Cookie


@runtime_checkable
class Page(Protocol):
    """The browser operations a recipe is allowed to use."""

    async def goto(self, url: str) -> None: ...

    async def current_url(self) -> str: ...

    async def content(self) -> str: ...

    # Evaluate a JS expression (which MAY evaluate to a promise) and return its
    # resolved, JSON-serializable value.
    async def eval(self, script: str) -> Any: ...

    async def wait_for_selector(self, selector: str, timeout: float) -> bool: ...

    async def click(self, selector: str) -> None: ...

    async def get_cookies(self) -> list[Cookie]: ...

    async def set_cookies(self, cookies: list[Cookie]) -> None: ...

    async def user_agent(self) -> str: ...

    # Arm a one-shot capture of the next file download the page triggers, then
    # return the captured URL (the browser's own download is cancelled so only
    # the URL is taken). Returns None if nothing downloaded before timeout.
    async def capture_download(self, timeout: float) -> str | None: ...


class ChallengeError(Exception):
    """Raised when a gate could not be cleared without a visible browser."""

    def __init__(self, message: str, *, needs_interactive: bool = False) -> None:
        super().__init__(message)
        self.needs_interactive = needs_interactive


class Engine(Protocol):
    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def ready(self) -> bool: ...

    # Acquire a page bound to a fresh or reused context. Callers MUST use the
    # `page()` context manager below rather than calling this directly.
    async def _acquire(self) -> Page: ...

    async def _release(self, page: Page) -> None: ...


@asynccontextmanager
async def page(engine: Engine):
    p = await engine._acquire()
    try:
        yield p
    finally:
        await engine._release(p)


# Titles/markers a Cloudflare interstitial shows while the JS challenge runs. A
# recipe waits for these to disappear before treating the page as loaded.
CLOUDFLARE_MARKERS = (
    "Just a moment",
    "Checking your browser",
    "cf-browser-verification",
    "challenge-platform",
)


def build_engine(settings: Settings) -> Engine:
    """Construct the default (nodriver) engine. Imported lazily so the package
    imports and the test suite run without Chrome or nodriver present."""
    from .nodriver_engine import NodriverEngine

    return NodriverEngine(settings)


async def wait_cloudflare(p: Page, timeout: float) -> None:
    """Poll until the Cloudflare interstitial clears or the timeout elapses.

    Raises ChallengeError(needs_interactive=True) if it never clears, so the
    caller can decide to fall back to a visible browser or another route.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        html = (await p.content()).lower()
        if not any(m.lower() in html for m in CLOUDFLARE_MARKERS):
            return
        await asyncio.sleep(1.0)
    raise ChallengeError("cloudflare challenge did not clear", needs_interactive=True)
