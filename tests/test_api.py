"""API tests using a fake solver client injected before the app lifespan starts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
