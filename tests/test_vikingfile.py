"""ViKiNG FiLE recipe unit tests, driven through the fake FlareSolverr client."""

from __future__ import annotations

from slipgate.models import Cookie, ResolveRequest
from slipgate.recipes.vikingfile import VikingFileRecipe, _extract
from slipgate.solver import SolverResult
from tests.conftest import FakeSolverrClient

RECIPE = VikingFileRecipe()
PAGE = "https://vikingfile.com/f/TPRSfLvcIu"

# The download POST returns JSON (FlareSolverr wraps it in <pre>); slashes arrive
# JSON-escaped from the browser's JSON viewer.
POST_JSON = (
    '<pre>{"link":"https:\\/\\/s5.vikingfile.com\\/download\\/abc",'
    '"name":"game.zip","size":12345}</pre>'
)
# The file page HTML after Turnstile clears: the script filled in the button href.
GET_HTML = (
    '<div id="name">game.iso</div>'
    '<a id="download-link" class="hidden" href="https://s7.vikingfile.com/download/xyz">Download</a>'
)


def _req(**over) -> ResolveRequest:
    data = {"host": "vikingfile", "page_url": PAGE}
    data.update(over)
    return ResolveRequest(**data)


async def test_resolve_from_post_json():
    client = FakeSolverrClient(
        post_result=SolverResult(
            status=200,
            response_text=POST_JSON,
            cookies=[Cookie(name="cf_clearance", value="fresh")],
            user_agent="Chrome/Real",
        )
    )
    res = await RECIPE.resolve(client, _req())
    assert res.ok
    assert res.download_url == "https://s5.vikingfile.com/download/abc"
    assert res.file_name == "game.zip"
    assert res.size_bytes == 12345
    assert res.user_agent == "Chrome/Real"
    assert any(c.name == "cf_clearance" for c in res.cookies)
    assert client.ensured == 1 and client.reset == 0
    # GET (no link in default page) then the fallback POST.
    assert [c[0] for c in client.calls] == ["get", "post"]


async def test_resolve_from_get_anchor_without_post():
    client = FakeSolverrClient(
        get_result=SolverResult(
            status=200,
            response_text=GET_HTML,
            cookies=[Cookie(name="cf_clearance", value="g")],
            user_agent="Chrome/G",
        )
    )
    res = await RECIPE.resolve(client, _req())
    assert res.ok
    assert res.download_url == "https://s7.vikingfile.com/download/xyz"
    assert res.file_name == "game.iso"
    assert res.user_agent == "Chrome/G"
    # The populated page needs no POST.
    assert [c[0] for c in client.calls] == ["get"]


async def test_missing_page_url_fails_fast():
    res = await RECIPE.resolve(FakeSolverrClient(), _req(page_url=""))
    assert not res.ok
    assert res.error == "missing page_url"


async def test_gated_response_is_clean_failure():
    # GET has no link and the POST fallback returns no link either.
    client = FakeSolverrClient(
        post_result=SolverResult(status=200, response_text="<pre>{\"error\":\"turnstile\"}</pre>")
    )
    res = await RECIPE.resolve(client, _req())
    assert not res.ok
    assert "no download link" in res.error


async def test_solver_error_on_get_surfaces():
    res = await RECIPE.resolve(FakeSolverrClient(raise_on="get"), _req())
    assert not res.ok
    assert "boom" in res.error


def test_extract_variants():
    got = _extract(POST_JSON)
    assert got == {"link": "https://s5.vikingfile.com/download/abc", "name": "game.zip", "size": 12345}
    got = _extract(GET_HTML)
    assert got["link"] == "https://s7.vikingfile.com/download/xyz"
    assert got["name"] == "game.iso"
    assert _extract("<html></html>")["link"] == ""
