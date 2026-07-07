"""NexusMods recipe unit tests, driven entirely through the fake page."""

from __future__ import annotations

import pytest

from slipgate.models import Cookie, ResolveRequest
from slipgate.recipes.nexus import NexusRecipe, _extract_url
from tests.conftest import FakePage

RECIPE = NexusRecipe()
PARAMS = {"domain": "skyrimspecialedition", "mod_id": "266", "file_id": "1000", "game_id": "110"}
SESSION = [Cookie(name="nexusmods_session", value="abc")]


def _req(**over) -> ResolveRequest:
    data = {"host": "nexusmods", "params": dict(PARAMS), "cookies": list(SESSION)}
    data.update(over)
    return ResolveRequest(**data)


@pytest.mark.usefixtures("fast_wait")
async def test_resolve_returns_generated_cdn_url():
    page = FakePage(
        eval_result='{"ok": true, "url": "https://cdn.nexus/file.zip?token=x"}',
        cookies=[Cookie(name="cf_clearance", value="fresh")],
        ua="Chrome/Real",
    )
    res = await RECIPE.resolve(page, _req())
    assert res.ok
    assert res.download_url == "https://cdn.nexus/file.zip?token=x"
    assert res.user_agent == "Chrome/Real"
    # The login session was seeded on the Nexus domain before navigating.
    assert any(c.name == "nexusmods_session" for c in page.set_cookie_calls)
    # cf_clearance the browser minted rides back to the caller.
    assert any(c.name == "cf_clearance" for c in res.cookies)


@pytest.mark.usefixtures("fast_wait")
async def test_missing_params_fail_fast():
    res = await RECIPE.resolve(FakePage(), _req(params={"domain": "x"}))
    assert not res.ok
    assert "missing params" in res.error


@pytest.mark.usefixtures("fast_wait")
async def test_requires_session_cookie():
    res = await RECIPE.resolve(FakePage(), _req(cookies=[]))
    assert not res.ok
    assert "nexusmods_session" in res.error


@pytest.mark.usefixtures("fast_wait")
async def test_falls_back_to_click_capture_when_generate_empty():
    page = FakePage(
        eval_result='{"ok": false, "error": "nope"}',
        selectors={"a#slowDownloadButton"},
        download_url="https://cdn.nexus/manual.zip",
    )
    res = await RECIPE.resolve(page, _req())
    assert res.ok
    assert res.download_url == "https://cdn.nexus/manual.zip"
    assert "a#slowDownloadButton" in page.clicked


@pytest.mark.usefixtures("fast_wait")
async def test_no_url_anywhere_is_a_clean_failure():
    res = await RECIPE.resolve(FakePage(eval_result='{"ok": false}'), _req())
    assert not res.ok
    assert "download url" in res.error


def test_extract_url_variants():
    assert _extract_url('{"ok": true, "url": "u"}') == "u"
    assert _extract_url('{"ok": false, "url": "u"}') == ""
    assert _extract_url("not json") == ""
    assert _extract_url("") == ""
    assert _extract_url(None) == ""
