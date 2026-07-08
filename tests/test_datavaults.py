"""DataVaults recipe unit tests.

The Cloudflare warm-up runs through the fake FlareSolverr client; the XFS form
flow (real HTTP, redirect-off) is exercised by monkeypatching `_form_flow`, so
the suite needs no network and no real FlareSolverr.
"""

from __future__ import annotations

import httpx
import pytest

from slipgate.recipes import datavaults
from slipgate.recipes.datavaults import DataVaultsRecipe, _direct_url, _solve_captcha, _wait_secs
from tests.conftest import FakeSolverrClient

RECIPE = DataVaultsRecipe()
PAGE = "https://datavaults.co/9rmy4t6thhaq/game.zip"
DIRECT = "https://d5.datavaults.co/d/tok3ntok3n/game.zip?fp=YWJj"

# An XFS positional captcha: padding-left order 10,30,50,70 -> digits 1,2,3,4.
DOWNLOAD2_PAGE = (
    '<span style="padding-left:50px;">&#51;</span>'
    '<span style="padding-left:10px;">&#49;</span>'
    '<span style="padding-left:70px;">4</span>'
    '<span style="padding-left:30px;">&#50;</span>'
    '<div id="seconds">7</div>'
)
FINAL_PAGE = f'<a class="btn-download" href="{DIRECT}">Direct Download</a>'


def _req(**over):
    data = {"host": "datavaults", "page_url": PAGE}
    data.update(over)
    from slipgate.models import ResolveRequest

    return ResolveRequest(**data)


@pytest.fixture
def fast_wait(monkeypatch):
    monkeypatch.setattr("slipgate.recipes.datavaults.WAIT_SECS", 0.0)
    monkeypatch.setattr("slipgate.recipes.datavaults.WAIT_CAP_SECS", 0.0)


def _patch_flow(monkeypatch, result=None, exc=None):
    async def fake_flow(page_url, file_id, fname, ua, seed_cookies):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(datavaults, "_form_flow", fake_flow)


async def test_resolve_returns_direct_cdn_url(monkeypatch):
    _patch_flow(monkeypatch, result=(DIRECT, ""))
    res = await RECIPE.resolve(FakeSolverrClient(), _req())
    assert res.ok
    assert res.download_url == DIRECT
    assert res.file_name == "game.zip"


async def test_missing_page_url_fails_fast():
    res = await RECIPE.resolve(FakeSolverrClient(), _req(page_url=""))
    assert not res.ok
    assert res.error == "missing page_url"


async def test_unrecognized_url_fails():
    res = await RECIPE.resolve(FakeSolverrClient(), _req(page_url="https://datavaults.co/"))
    assert not res.ok
    assert "unrecognized" in res.error


async def test_flow_failure_reason_surfaces(monkeypatch):
    _patch_flow(monkeypatch, result=("", "Wrong captcha"))
    res = await RECIPE.resolve(FakeSolverrClient(), _req())
    assert not res.ok
    assert res.error == "Wrong captcha"
    assert res.needs_interactive is False


async def test_http_error_is_clean_failure(monkeypatch):
    _patch_flow(monkeypatch, exc=httpx.ConnectError("boom"))
    res = await RECIPE.resolve(FakeSolverrClient(), _req())
    assert not res.ok
    assert "request failed" in res.error


async def test_solver_down_still_resolves(monkeypatch):
    # FlareSolverr raising on the warm-up GET must not abort: DataVaults is
    # un-gated, so the flow proceeds with the default UA.
    _patch_flow(monkeypatch, result=(DIRECT, ""))
    res = await RECIPE.resolve(FakeSolverrClient(raise_on="get"), _req())
    assert res.ok
    assert res.download_url == DIRECT


def test_solve_captcha_orders_by_padding():
    assert _solve_captcha(DOWNLOAD2_PAGE) == "1234"
    assert _solve_captcha("<html></html>") == ""


def test_direct_url_prefers_token_link_and_skips_page_url():
    assert _direct_url(FINAL_PAGE, PAGE) == DIRECT
    # The page's own URL (ends in .zip) is not a direct link.
    assert _direct_url(f'<a href="{PAGE}">x</a>', PAGE) == ""
    other = "https://mirror.example.com/game.zip"
    assert _direct_url(f'<a href="{other}">dl</a>', PAGE) == other


def test_wait_secs_floor_and_cap(fast_wait):
    # With floor/cap zeroed, the parsed value passes through clamped to <= cap 0.
    assert _wait_secs(DOWNLOAD2_PAGE) == 0.0
