"""Shared test doubles.

FakeSolverrClient implements the FlareSolverr client surface in memory so the API
and recipes run with no FlareSolverr and no network.
"""

from __future__ import annotations

import asyncio

import pytest

from slipgate.solver import SolverError, SolverResult


class FakeSolverrClient:
    def __init__(
        self,
        *,
        reachable: bool = True,
        get_result: SolverResult | None = None,
        post_result: SolverResult | None = None,
        raise_on: str = "",
    ) -> None:
        self._reachable = reachable
        self.get_result = get_result or SolverResult(status=200, response_text="<html></html>")
        self.post_result = post_result or SolverResult(status=200, response_text="<pre>[]</pre>")
        self.raise_on = raise_on
        self.calls: list[tuple] = []
        self.ensured = 0
        self.reset = 0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        pass

    async def reachable(self) -> bool:
        return self._reachable

    def session_lock(self, name: str) -> asyncio.Lock:
        return self._lock

    async def ensure_session(self, name: str) -> None:
        self.ensured += 1

    async def reset_session(self, name: str) -> None:
        self.reset += 1

    async def get(self, url, *, cookies=None, session="", max_timeout_ms=None) -> SolverResult:
        self.calls.append(("get", url))
        if self.raise_on == "get":
            raise SolverError("boom")
        return self.get_result

    async def post(self, url, post_data, *, cookies=None, session="", max_timeout_ms=None) -> SolverResult:
        self.calls.append(("post", url, post_data))
        if self.raise_on == "post":
            raise SolverError("boom")
        return self.post_result


@pytest.fixture
def fast_wait(monkeypatch):
    """Zero out the Nexus free-download countdown so tests do not sleep."""
    monkeypatch.setattr("slipgate.recipes.nexus.FREE_WAIT_SECS", 0.0)
