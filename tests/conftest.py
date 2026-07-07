"""Shared test doubles.

FakePage/FakeEngine implement the engine contract in memory so the API and the
recipes can be exercised with no Chrome, no nodriver, and no network.
"""

from __future__ import annotations

import pytest

from slipgate.engine import Engine, Page
from slipgate.models import Cookie


class FakePage(Page):
    def __init__(
        self,
        *,
        html: str = "<html><body>ok</body></html>",
        eval_result: object = "",
        cookies: list[Cookie] | None = None,
        ua: str = "FakeUA/1.0",
        selectors: set[str] | None = None,
        download_url: str | None = None,
    ) -> None:
        self.html = html
        self.eval_result = eval_result
        self._cookies = list(cookies or [])
        self.ua = ua
        self.selectors = selectors or set()
        self.download_url = download_url
        self.visited: list[str] = []
        self.set_cookie_calls: list[Cookie] = []
        self.clicked: list[str] = []

    async def goto(self, url: str) -> None:
        self.visited.append(url)

    async def current_url(self) -> str:
        return self.visited[-1] if self.visited else ""

    async def content(self) -> str:
        return self.html

    async def eval(self, script: str):
        return self.eval_result

    async def wait_for_selector(self, selector: str, timeout: float) -> bool:
        return selector in self.selectors

    async def click(self, selector: str) -> None:
        self.clicked.append(selector)

    async def get_cookies(self) -> list[Cookie]:
        return list(self._cookies)

    async def set_cookies(self, cookies: list[Cookie]) -> None:
        self.set_cookie_calls.extend(cookies)
        self._cookies.extend(cookies)

    async def user_agent(self) -> str:
        return self.ua

    async def capture_download(self, timeout: float) -> str | None:
        return self.download_url


class FakeEngine(Engine):
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.ready_flag = True

    async def startup(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def ready(self) -> bool:
        return self.ready_flag

    async def _acquire(self) -> Page:
        return self._page

    async def _release(self, page: Page) -> None:
        pass


@pytest.fixture
def fast_wait(monkeypatch):
    """Zero out the Nexus free-download countdown so tests do not sleep."""
    monkeypatch.setattr("slipgate.recipes.nexus.FREE_WAIT_SECS", 0.0)
