"""nodriver-backed browser engine (nodriver 0.50.x).

Launches one real Chrome and opens a tab per resolve. A real browser is the whole
point: it performs a genuine TLS handshake and runs Cloudflare's JS challenge, so
it clears gates a raw HTTP client cannot, and nodriver adds the anti-automation
patches on top.

Imported lazily (see engine.build_engine) so the package and the test suite import
cleanly without nodriver or Chrome present.
"""

from __future__ import annotations

import asyncio

import nodriver as uc
from nodriver import cdp

from .config import Settings
from .engine import Engine, Page
from .models import Cookie

NEXUS_DEFAULT_URL = "https://www.nexusmods.com"


class NodriverPage(Page):
    def __init__(self, browser: uc.Browser, tab: uc.Tab) -> None:
        self._browser = browser
        self._tab = tab

    async def goto(self, url: str) -> None:
        self._tab = await self._browser.get(url)

    async def current_url(self) -> str:
        return await self._tab.evaluate("location.href", return_by_value=True)

    async def content(self) -> str:
        return await self._tab.get_content()

    async def eval(self, script: str):
        # `script` is an expression that may evaluate to a promise; await_promise
        # resolves it and return_by_value hands back the JSON value, not a handle.
        return await self._tab.evaluate(script, await_promise=True, return_by_value=True)

    async def wait_for_selector(self, selector: str, timeout: float) -> bool:
        try:
            await self._tab.select(selector, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 - a missing selector is a normal "no"
            return False

    async def click(self, selector: str) -> None:
        element = await self._tab.select(selector)
        await element.click()

    async def get_cookies(self) -> list[Cookie]:
        raw = await self._browser.cookies.get_all()
        return [
            Cookie(name=c.name, value=c.value, domain=c.domain or "", path=c.path or "/")
            for c in raw
        ]

    async def set_cookies(self, cookies: list[Cookie]) -> None:
        # CDP set_cookie works before navigation, so a session can be seeded on
        # the very first page load. A domain is required; fall back to the Nexus
        # origin as a URL when a caller omits it.
        for c in cookies:
            if c.domain:
                await self._tab.send(
                    cdp.network.set_cookie(name=c.name, value=c.value, domain=c.domain, path=c.path or "/")
                )
            else:
                await self._tab.send(
                    cdp.network.set_cookie(
                        name=c.name, value=c.value, url=NEXUS_DEFAULT_URL, path=c.path or "/"
                    )
                )

    async def user_agent(self) -> str:
        return await self._tab.evaluate("navigator.userAgent", return_by_value=True)

    async def capture_download(self, timeout: float) -> str | None:
        # Deny the actual download but keep the events, then read the URL from the
        # downloadWillBegin event Chrome emits before fetching any bytes.
        loop = asyncio.get_event_loop()
        captured: asyncio.Future[str] = loop.create_future()

        def on_begin(event: cdp.browser.DownloadWillBegin) -> None:
            if not captured.done():
                captured.set_result(event.url)

        self._tab.add_handler(cdp.browser.DownloadWillBegin, on_begin)
        await self._tab.send(cdp.browser.set_download_behavior(behavior="deny", events_enabled=True))
        try:
            return await asyncio.wait_for(captured, timeout=timeout)
        except TimeoutError:
            return None
        finally:
            self._tab.remove_handler(cdp.browser.DownloadWillBegin, on_begin)


class NodriverEngine(Engine):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._browser: uc.Browser | None = None
        self._sema = asyncio.Semaphore(settings.max_concurrency)

    async def startup(self) -> None:
        self._browser = await uc.start(
            headless=self._settings.headless,
            browser_executable_path=self._settings.browser_path or None,
            browser_args=self._settings.browser_arg_list or None,
            sandbox=self._settings.sandbox,
        )

    async def shutdown(self) -> None:
        if self._browser is not None:
            self._browser.stop()
            self._browser = None

    async def ready(self) -> bool:
        return self._browser is not None

    async def _acquire(self) -> Page:
        await self._sema.acquire()
        if self._browser is None:
            self._sema.release()
            raise RuntimeError("browser engine not started")
        tab = await self._browser.get("about:blank", new_tab=True)
        return NodriverPage(self._browser, tab)

    async def _release(self, page: Page) -> None:
        try:
            if isinstance(page, NodriverPage):
                # Clear cookies so one resolve's session never bleeds into the
                # next tab that reuses this single browser context.
                try:
                    await self._browser.cookies.clear()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001 - best effort cleanup
                    pass
                await page._tab.close()
        finally:
            self._sema.release()
