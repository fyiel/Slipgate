"""NexusMods recipe unit tests, driven through the fake FlareSolverr client."""

from __future__ import annotations

import pytest

from slipgate.models import Cookie, ResolveRequest
from slipgate.recipes.nexus import GENERATE_URL, NexusRecipe, _extract_uri
from slipgate.solver import SolverResult
from tests.conftest import FakeSolverrClient

RECIPE = NexusRecipe()
PARAMS = {"domain": "skyrimspecialedition", "mod_id": "266", "file_id": "1000", "game_id": "1704"}
SESSION = [Cookie(name="nexusmods_session", value="abc")]


def _req(**over) -> ResolveRequest:
    data = {"host": "nexusmods", "params": dict(PARAMS), "cookies": list(SESSION)}
    data.update(over)
    return ResolveRequest(**data)


@pytest.mark.usefixtures("fast_wait")
async def test_resolve_returns_generated_cdn_url():
    client = FakeSolverrClient(
        post_result=SolverResult(
            status=200,
            response_text='<pre>[{"name":"Nexus CDN","URI":"https://cdn.nexus/file.zip?token=x"}]</pre>',
            cookies=[Cookie(name="cf_clearance", value="fresh")],
            user_agent="Chrome/Real",
        )
    )
    res = await RECIPE.resolve(client, _req())
    assert res.ok
    assert res.download_url == "https://cdn.nexus/file.zip?token=x"
    assert res.user_agent == "Chrome/Real"
    assert any(c.name == "cf_clearance" for c in res.cookies)
    # The recipe warms a session (get file page) then posts the generate call, and
    # always tears the session down.
    assert client.created == 1 and client.destroyed == 1
    assert client.calls[0][0] == "get"
    assert client.calls[1][0] == "post" and client.calls[1][1] == GENERATE_URL


@pytest.mark.usefixtures("fast_wait")
async def test_missing_params_fail_fast():
    res = await RECIPE.resolve(FakeSolverrClient(), _req(params={"domain": "x"}))
    assert not res.ok
    assert "missing params" in res.error


@pytest.mark.usefixtures("fast_wait")
async def test_requires_session_cookie():
    res = await RECIPE.resolve(FakeSolverrClient(), _req(cookies=[]))
    assert not res.ok
    assert "nexusmods_session" in res.error


@pytest.mark.usefixtures("fast_wait")
async def test_logged_out_empty_array_is_clean_failure():
    # Default fake post returns <pre>[]</pre>, the logged-out generate result.
    res = await RECIPE.resolve(FakeSolverrClient(), _req())
    assert not res.ok
    assert "download url" in res.error


@pytest.mark.usefixtures("fast_wait")
async def test_solver_error_surfaces():
    res = await RECIPE.resolve(FakeSolverrClient(raise_on="post"), _req())
    assert not res.ok
    assert "boom" in res.error


def test_extract_uri_variants():
    assert _extract_uri('<pre>[{"URI":"u"}]</pre>') == "u"
    # HTML-entity-escaped ampersands in the URI are unescaped.
    assert _extract_uri('<pre>[{"URI":"https://c/f?a=1&amp;b=2"}]</pre>') == "https://c/f?a=1&b=2"
    assert _extract_uri("<pre>[]</pre>") == ""
    assert _extract_uri('[{"URI":"raw"}]') == "raw"
    assert _extract_uri("not json") == ""
    assert _extract_uri("") == ""
