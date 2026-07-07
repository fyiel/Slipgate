"""API tests using a fake engine injected before the app lifespan starts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from slipgate.api import app
from slipgate.config import Settings, get_settings
from slipgate.models import Cookie
from tests.conftest import FakeEngine, FakePage


@pytest.fixture
def client_factory():
    created: list[TestClient] = []

    def make(page: FakePage, settings: Settings | None = None) -> TestClient:
        app.state.engine = FakeEngine(page)
        app.state.engine_error = ""
        if settings is not None:
            app.dependency_overrides[get_settings] = lambda: settings
        c = TestClient(app)
        created.append(c)
        return c

    yield make
    app.dependency_overrides.clear()
    if hasattr(app.state, "engine"):
        del app.state.engine


def test_health_lists_recipes(client_factory):
    with client_factory(FakePage()) as c:
        body = c.get("/health").json()
    assert body["ok"] is True
    assert "nexusmods" in body["recipes"]
    assert body["engine_ready"] is True


def test_resolve_unknown_host(client_factory):
    with client_factory(FakePage()) as c:
        body = c.post("/resolve", json={"host": "nope"}).json()
    assert body["ok"] is False
    assert "no recipe" in body["error"]


def test_resolve_routes_to_recipe(client_factory, fast_wait):
    page = FakePage(eval_result='{"ok": true, "url": "https://cdn/f.zip"}', ua="Chrome/Real")
    payload = {
        "host": "nexusmods",
        "params": {"domain": "sse", "mod_id": "1", "file_id": "2", "game_id": "3"},
        "cookies": [Cookie(name="nexusmods_session", value="v").model_dump()],
    }
    with client_factory(page) as c:
        body = c.post("/resolve", json=payload).json()
    assert body["ok"] is True
    assert body["download_url"] == "https://cdn/f.zip"


def test_api_key_enforced(client_factory):
    settings = Settings(api_key="secret")
    with client_factory(FakePage(), settings=settings) as c:
        unauth = c.post("/resolve", json={"host": "nexusmods"})
        assert unauth.status_code == 401
        ok = c.post("/resolve", json={"host": "nope"}, headers={"X-Slipgate-Key": "secret"})
        assert ok.status_code == 200
