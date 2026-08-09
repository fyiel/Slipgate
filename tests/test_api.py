"""API tests using a fake solver client injected before the app lifespan starts."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import slipgate.api as api_module
from slipgate.api import app
from slipgate.config import Settings, get_settings
from slipgate.models import Cookie
from slipgate.solver import SolverResult
from tests.conftest import FakeSolverrClient


@pytest.fixture
def client_factory():
    def make(solver: FakeSolverrClient, settings: Settings | None = None) -> TestClient:
        app.state.solver = solver
        if settings is not None:
            app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()
    if hasattr(app.state, "solver"):
        del app.state.solver


def test_health_reports_flaresolverr_and_recipes(client_factory):
    with client_factory(FakeSolverrClient(reachable=True)) as c:
        body = c.get("/health").json()
    assert body["ok"] is True
    assert body["flaresolverr_ok"] is True
    assert "nexusmods" in body["recipes"]


def test_health_flags_unreachable_flaresolverr(client_factory):
    with client_factory(FakeSolverrClient(reachable=False)) as c:
        body = c.get("/health").json()
    assert body["flaresolverr_ok"] is False


def test_resolve_unknown_host(client_factory):
    with client_factory(FakeSolverrClient()) as c:
        body = c.post("/resolve", json={"host": "nope"}).json()
    assert body["ok"] is False
    assert "no recipe" in body["error"]


def test_resolve_routes_to_recipe(client_factory, fast_wait):
    solver = FakeSolverrClient(
        post_result=SolverResult(status=200, response_text='<pre>[{"URI":"https://cdn/f.zip"}]</pre>')
    )
    payload = {
        "host": "nexusmods",
        "params": {"domain": "sse", "mod_id": "1", "file_id": "2", "game_id": "3"},
        "cookies": [Cookie(name="nexusmods_session", value="v").model_dump()],
    }
    with client_factory(solver) as c:
        body = c.post("/resolve", json=payload).json()
    assert body["ok"] is True
    assert body["download_url"] == "https://cdn/f.zip"


def test_api_key_enforced(client_factory):
    with client_factory(FakeSolverrClient(), settings=Settings(api_key="secret")) as c:
        assert c.post("/resolve", json={"host": "nexusmods"}).status_code == 401
        ok = c.post("/resolve", json={"host": "nope"}, headers={"X-Slipgate-Key": "secret"})
        assert ok.status_code == 200


def test_fetch_reuses_warm_session(client_factory):
    solver = FakeSolverrClient(
        get_result=SolverResult(status=200, response_text='<pre>{"downloads": []}</pre>')
    )
    with client_factory(solver) as c:
        body = c.post("/fetch", json={"url": "https://hydralinks.cloud/sources/gog.json"}).json()
    assert body["ok"] is True
    assert body["body"] == '{"downloads": []}'
    assert solver.ensured >= 1
    assert solver.calls == [("get", "https://hydralinks.cloud/sources/gog.json")]


def test_mangafire_fetch_uses_proxy_and_rejects_other_destinations(client_factory, monkeypatch):
    seen = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [{"hid": "ro8ro"}], "meta": {"hasNext": False}})

    def fake_client(**kwargs):
        kwargs.pop("proxy", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(api_module.httpx, "AsyncClient", fake_client)
    settings = Settings(proxy_url="http://proxy.test:8080")
    url = "https://mangafire.to/api/titles?keyword=solo&vrf=signed"
    with client_factory(FakeSolverrClient(), settings=settings) as c:
        body = c.post("/mangafire/fetch", json={"url": url}).json()
        blocked = c.post("/mangafire/fetch", json={"url": "http://127.0.0.1:8191/v1"}).json()

    assert body["ok"] is True
    assert '"hid":"ro8ro"' in body["body"].replace(" ", "")
    assert blocked == {"ok": False, "status": 0, "body": "", "error": "unrecognized MangaFire resource"}
    assert [str(request.url) for request in seen] == [url]
    assert seen[0].headers["x-requested-with"] == "XMLHttpRequest"


def test_mangafire_fetch_rejects_challenge_html(client_factory, monkeypatch):
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("proxy", None)
        return real_client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text="<title>Just a moment...</title>")
            ),
            **kwargs,
        )

    monkeypatch.setattr(api_module.httpx, "AsyncClient", fake_client)
    settings = Settings(proxy_url="http://proxy.test:8080")
    with client_factory(FakeSolverrClient(), settings=settings) as c:
        body = c.post("/mangafire/fetch", json={
            "url": "https://mangafire.to/api/chapters/9348523?vrf=signed",
        }).json()

    assert body["ok"] is False
    assert body["error"] == "MangaFire returned a challenge"


def test_mangafire_image_is_binary_and_source_scoped(client_factory, monkeypatch):
    seen = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\xff\xd8page", headers={"content-type": "image/jpeg"})

    def fake_client(**kwargs):
        kwargs.pop("proxy", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(api_module.httpx, "AsyncClient", fake_client)
    settings = Settings(proxy_url="http://proxy.test:8080")
    payload = {
        "url": "https://nw8.mfcdn3.xyz/mf/path/p.jpg",
        "referer": "https://mangafire.to/title/ro8ro/chapter/9348523",
    }
    with client_factory(FakeSolverrClient(), settings=settings) as c:
        response = c.post("/mangafire/image", json=payload)
        blocked = c.post("/mangafire/image", json={**payload, "url": "https://example.com/page.jpg"})

    assert response.status_code == 200
    assert response.content == b"\xff\xd8page"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=14400"
    assert blocked.status_code == 400
    assert len(seen) == 1
    assert seen[0].headers["referer"] == payload["referer"]
