"""DataNodes recipe unit tests, driven through the fake FlareSolverr client.

The Cloudflare warm-up runs through the fake client; the download2 POST is
monkeypatched so the suite needs no network and no real FlareSolverr.
"""

from __future__ import annotations

import httpx

from slipgate.models import Cookie
from slipgate.recipes import datanodes
from slipgate.recipes.datanodes import DataNodesRecipe, _absolute_redirect, _download2, _extract_url
from slipgate.solver import SolverResult
from tests.conftest import FakeSolverrClient

RECIPE = DataNodesRecipe()
PAGE = "https://datanodes.to/abc123/game.zip"
DIRECT = "https://cdn.datanodes.to/d/abc123/game.zip"


def _req(**over):
    data = {"host": "datanodes", "page_url": PAGE}
    data.update(over)
    from slipgate.models import ResolveRequest

    return ResolveRequest(**data)


def _patch_flow(monkeypatch, result=None, exc=None):
    async def fake_flow(page_url, file_id, ua, seed_cookies):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(datanodes, "_download2", fake_flow)


async def test_resolve_returns_direct_cdn_url(monkeypatch):
    _patch_flow(monkeypatch, result=(DIRECT, ""))
    solved = Cookie(name="cf_clearance", value="fresh", domain=".datanodes.to")
    client = FakeSolverrClient(
        get_result=SolverResult(
            status=200,
            response_text="<html></html>",
            cookies=[solved],
            user_agent="Solved Browser",
        )
    )
    res = await RECIPE.resolve(client, _req())
    assert res.ok
    assert res.download_url == DIRECT
    assert res.file_name == "game.zip"
    assert res.cookies == [solved]
    assert res.user_agent == "Solved Browser"


async def test_missing_page_url_fails_fast():
    res = await RECIPE.resolve(FakeSolverrClient(), _req(page_url=""))
    assert not res.ok
    assert res.error == "missing page_url"


async def test_unrecognized_url_fails():
    res = await RECIPE.resolve(FakeSolverrClient(), _req(page_url="https://datanodes.to/"))
    assert not res.ok
    assert "unrecognized" in res.error


async def test_flow_failure_reason_surfaces(monkeypatch):
    _patch_flow(monkeypatch, result=("", "no datanodes download url"))
    res = await RECIPE.resolve(FakeSolverrClient(), _req())
    assert not res.ok
    assert "no datanodes download url" in res.error


async def test_http_error_is_clean_failure(monkeypatch):
    _patch_flow(monkeypatch, exc=httpx.ConnectError("boom"))
    res = await RECIPE.resolve(FakeSolverrClient(), _req())
    assert not res.ok
    assert "request failed" in res.error


async def test_solver_down_still_resolves(monkeypatch):
    # FlareSolverr raising on the warm-up GET must not abort: DataNodes is
    # un-gated, so the flow proceeds with the default UA.
    _patch_flow(monkeypatch, result=(DIRECT, ""))
    res = await RECIPE.resolve(FakeSolverrClient(raise_on="get"), _req())
    assert res.ok
    assert res.download_url == DIRECT


async def test_download2_merges_solver_cookies_and_joins_redirect(monkeypatch):
    seen_cookie = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_cookie
        seen_cookie = request.headers.get("cookie", "")
        return httpx.Response(302, headers={"Location": "/d/abc123/game.zip"}, request=request)

    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(datanodes.httpx, "AsyncClient", fake_client)
    direct, reason = await _download2(PAGE, "abc123", "Solved Browser", {"cf_clearance": "fresh"})

    assert reason == ""
    assert direct == "https://datanodes.to/d/abc123/game.zip"
    assert "cf_clearance=fresh" in seen_cookie
    assert "lang=english" in seen_cookie


def test_extract_url_variants():
    # Plain JSON from the direct POST.
    assert _extract_url('{"url":"https://cdn.example.com/game.zip"}') == "https://cdn.example.com/game.zip"
    # FlareSolverr wraps JSON in <pre> and HTML-escapes it.
    assert (
        _extract_url('<pre>{"url":"https://cdn.example.com/game.zip?a=1&amp;b=2"}</pre>')
        == "https://cdn.example.com/game.zip?a=1&b=2"
    )
    # The URL is percent-encoded by the host.
    assert _extract_url('{"url":"https://cdn.example.com/game%20with%20spaces.zip"}') == (
        "https://cdn.example.com/game with spaces.zip"
    )
    assert _extract_url('{"error":"nope"}') == ""
    assert _extract_url("") == ""
    assert _extract_url("not json") == ""


def test_redirects_are_absolute_http_urls():
    assert _absolute_redirect(PAGE, "/d/abc123/game.zip") == "https://datanodes.to/d/abc123/game.zip"
    assert _absolute_redirect(PAGE, "javascript:alert(1)") == ""


def test_recipe_hosts_registered():
    from slipgate.recipes import get_recipe

    r = get_recipe("datanodes")
    assert r is not None
    assert r.name == "datanodes"
    assert get_recipe("datanodes.to") is not None
