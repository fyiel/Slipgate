"""FlareSolverr client.

Slipgate does not run a browser itself. It delegates the hard part, clearing
Cloudflare-style gates, to a FlareSolverr instance (which the operator already
runs or gets from the bundled compose file), and adds the per-host download
resolution on top. This module is the thin async client over FlareSolverr's
single `/v1` endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .models import Cookie


@dataclass
class SolverResult:
    status: int
    response_text: str
    cookies: list[Cookie] = field(default_factory=list)
    user_agent: str = ""
    message: str = ""


class SolverError(Exception):
    """FlareSolverr was unreachable or returned a non-ok status."""


class FlareSolverrClient:
    def __init__(self, base_url: str, default_timeout_ms: int, http_timeout_secs: float) -> None:
        self._url = base_url
        self._default_timeout_ms = default_timeout_ms
        self._http = httpx.AsyncClient(timeout=http_timeout_secs)

    async def close(self) -> None:
        await self._http.aclose()

    async def _cmd(self, payload: dict) -> dict:
        try:
            resp = await self._http.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            raise SolverError(f"FlareSolverr unreachable at {self._url}: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise SolverError(f"FlareSolverr returned non-JSON (HTTP {resp.status_code})") from exc
        if data.get("status") != "ok":
            raise SolverError(data.get("message") or f"FlareSolverr status {data.get('status')}")
        return data

    async def reachable(self) -> bool:
        try:
            await self._cmd({"cmd": "sessions.list"})
            return True
        except SolverError:
            return False

    async def create_session(self) -> str:
        data = await self._cmd({"cmd": "sessions.create"})
        return data.get("session", "")

    async def destroy_session(self, session: str) -> None:
        if not session:
            return
        try:
            await self._cmd({"cmd": "sessions.destroy", "session": session})
        except SolverError:
            # A best-effort cleanup; a leaked session times out on its own.
            pass

    async def get(
        self,
        url: str,
        *,
        cookies: list[Cookie] | None = None,
        session: str = "",
        max_timeout_ms: int | None = None,
    ) -> SolverResult:
        payload = self._payload("request.get", url, None, cookies, session, max_timeout_ms)
        return self._result(await self._cmd(payload))

    async def post(
        self,
        url: str,
        post_data: str,
        *,
        cookies: list[Cookie] | None = None,
        session: str = "",
        max_timeout_ms: int | None = None,
    ) -> SolverResult:
        return self._result(
            await self._cmd(self._payload("request.post", url, post_data, cookies, session, max_timeout_ms))
        )

    def _payload(
        self,
        cmd: str,
        url: str,
        post_data: str | None,
        cookies: list[Cookie] | None,
        session: str,
        timeout_ms: int | None,
    ) -> dict:
        payload: dict = {"cmd": cmd, "url": url, "maxTimeout": timeout_ms or self._default_timeout_ms}
        if post_data is not None:
            payload["postData"] = post_data
        if session:
            payload["session"] = session
        if cookies:
            payload["cookies"] = [self._cookie(c) for c in cookies]
        return payload

    @staticmethod
    def _cookie(c: Cookie) -> dict:
        out: dict = {"name": c.name, "value": c.value, "path": c.path or "/"}
        if c.domain:
            out["domain"] = c.domain
        return out

    def _result(self, data: dict) -> SolverResult:
        sol = data.get("solution", {}) or {}
        cookies = [
            Cookie(
                name=c.get("name", ""),
                value=c.get("value", ""),
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )
            for c in sol.get("cookies", []) or []
        ]
        return SolverResult(
            status=sol.get("status", 0),
            response_text=sol.get("response", "") or "",
            cookies=cookies,
            user_agent=sol.get("userAgent", "") or "",
            message=data.get("message", "") or "",
        )
