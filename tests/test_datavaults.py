"""DataVaults recipe unit tests, driven through the fake FlareSolverr client.

The XFS free flow is two POSTs (download1 -> download2), so the happy path uses a
small sequenced fake that returns each canned page in turn.
"""

from __future__ import annotations

import pytest

from slipgate.models import Cookie, ResolveRequest
from slipgate.recipes.datavaults import DataVaultsRecipe, _direct_url, _solve_captcha
from slipgate.solver import SolverError, SolverResult
from tests.conftest import FakeSolverrClient

RECIPE = DataVaultsRecipe()
PAGE = "https://datavaults.co/9rmy4t6thhaq/game.zip"

# download1 response: the download2 page with the rand token, an XFS positional
# captcha (padding-left order 10,30,50,70 -> digits 1,2,3,4), and a countdown.
DOWNLOAD2_PAGE = """
<form>
  <input type="hidden" name="op" value="download2">
  <input type="hidden" name="id" value="9rmy4t6thhaq">
  <input type="hidden" name="rand" value="TESTRAND123">
  <input type="hidden" name="method_free" value="Free Download">
  <div>
    <span style='position:absolute;padding-left:70px;padding-top:5px;'>&#52;</span>
    <span style='position:absolute;padding-left:10px;padding-top:7px;'>&#49;</span>
    <span style='position:absolute;padding-left:50px;padding-top:5px;'>&#51;</span>
    <span style='position:absolute;padding-left:30px;padding-top:7px;'>&#50;</span>
  </div>
  <input type="text" name="code" class="captcha_code">
  Wait <span id="seconds">20</span> seconds
</form>
"""

# download2 response: the final page carrying the direct CDN /d/ link.
DIRECT = "https://d5.datavaults.co/d/tok3ntok3n/game.zip?fp=YWJj"
FINAL_PAGE = f'<a class="btn-download" href="{DIRECT}">Direct Download</a>'

WRONG_CAPTCHA_PAGE = '<div class="alert">Wrong captcha</div><form></form>'


class SeqFake(FakeSolverrClient):
    """FakeSolverrClient that returns queued POST results in order."""

    def __init__(self, *, get_result=None, post_results=None, raise_on=""):
        super().__init__(get_result=get_result, raise_on=raise_on)
        self._posts = list(post_results or [])

    async def post(self, url, post_data, *, cookies=None, session="", max_timeout_ms=None):
        self.calls.append(("post", url, post_data))
        if self.raise_on == "post":
            raise SolverError("boom")
        return self._posts.pop(0) if self._posts else self.post_result


@pytest.fixture
def fast_wait(monkeypatch):
    """Zero out the XFS countdown (floor and cap) so tests never sleep."""
    monkeypatch.setattr("slipgate.recipes.datavaults.WAIT_SECS", 0.0)
    monkeypatch.setattr("slipgate.recipes.datavaults.WAIT_CAP_SECS", 0.0)


def _req(**over) -> ResolveRequest:
    data = {"host": "datavaults", "page_url": PAGE}
    data.update(over)
    return ResolveRequest(**data)


@pytest.mark.usefixtures("fast_wait")
async def test_resolve_returns_direct_cdn_url():
    client = SeqFake(
        post_results=[
            SolverResult(status=200, response_text=DOWNLOAD2_PAGE),
            SolverResult(
                status=200,
                response_text=FINAL_PAGE,
                cookies=[Cookie(name="xfss", value="s")],
                user_agent="Chrome/Real",
            ),
        ]
    )
    res = await RECIPE.resolve(client, _req())
    assert res.ok
    assert res.download_url == DIRECT
    assert res.file_name == "game.zip"
    assert res.user_agent == "Chrome/Real"
    assert any(c.name == "xfss" for c in res.cookies)
    # One warm session, never torn down on success.
    assert client.ensured == 1 and client.reset == 0
    # Both form POSTs carry the expected XFS bodies, with the solved captcha.
    posts = [c for c in client.calls if c[0] == "post"]
    assert posts[0][2] == (
        "op=download1&usr_login=&id=9rmy4t6thhaq&fname=game.zip&referer=&method_free=Free+Download"
    )
    assert "op=download2" in posts[1][2]
    assert "rand=TESTRAND123" in posts[1][2]
    assert "code=1234" in posts[1][2]


@pytest.mark.usefixtures("fast_wait")
async def test_missing_page_url_fails_fast():
    res = await RECIPE.resolve(SeqFake(), _req(page_url=""))
    assert not res.ok
    assert res.error == "missing page_url"


@pytest.mark.usefixtures("fast_wait")
async def test_wrong_captcha_is_clean_failure():
    client = SeqFake(post_results=[SolverResult(status=200, response_text=WRONG_CAPTCHA_PAGE)])
    res = await RECIPE.resolve(client, _req())
    assert not res.ok
    assert res.error == "Wrong captcha"
    assert res.needs_interactive is False


@pytest.mark.usefixtures("fast_wait")
async def test_gated_empty_response_is_clean_failure():
    # No rand, no link: nothing to work with.
    client = SeqFake(post_results=[SolverResult(status=200, response_text="<html></html>")])
    res = await RECIPE.resolve(client, _req())
    assert not res.ok
    assert "no direct download link" in res.error


@pytest.mark.usefixtures("fast_wait")
async def test_solver_error_on_post_surfaces():
    res = await RECIPE.resolve(SeqFake(raise_on="post"), _req())
    assert not res.ok
    assert "boom" in res.error


@pytest.mark.usefixtures("fast_wait")
async def test_solver_error_on_get_surfaces():
    res = await RECIPE.resolve(SeqFake(raise_on="get"), _req())
    assert not res.ok
    assert "boom" in res.error


def test_solve_captcha_orders_by_padding():
    assert _solve_captcha(DOWNLOAD2_PAGE) == "1234"
    assert _solve_captcha("<html></html>") == ""


def test_direct_url_prefers_token_link_and_skips_page_url():
    assert _direct_url(FINAL_PAGE, PAGE) == DIRECT
    # The hoster page URL itself ends in .zip but is the same host -> not a hit.
    assert _direct_url(f'<a href="{PAGE}">file</a>', PAGE) == ""
    # A file href on a different CDN host is accepted as a fallback.
    other = "https://cdn.example.com/get/game.rar"
    assert _direct_url(f'<a href="{other}">dl</a>', PAGE) == other
