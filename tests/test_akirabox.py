"""Akira Box recipe unit tests, driven through the fake FlareSolverr client."""

from __future__ import annotations

from slipgate.models import Cookie, ResolveRequest
from slipgate.recipes.akirabox import AkiraBoxRecipe, _canonical, _size_bytes
from slipgate.solver import SolverResult
from tests.conftest import FakeSolverrClient

RECIPE = AkiraBoxRecipe()
PAGE = "https://akirabox.com/8ifkke3kwpbc"

# File Status API JSON (FlareSolverr wraps it in <pre>; slashes JSON-escaped).
OK_JSON = (
    '<pre>{"status":200,"name":"example.rar","size":"1.2 MB","type":"file",'
    '"mime":"application/x-rar","url":"https:\\/\\/akirabox.com\\/8ifkke3kwpbc\\/file"}</pre>'
)
NOT_FOUND_JSON = '<pre>{"status":404,"message":"File not found"}</pre>'


def _req(**over) -> ResolveRequest:
    data = {"host": "akirabox", "page_url": PAGE}
    data.update(over)
    return ResolveRequest(**data)


async def test_resolve_returns_direct_link():
    client = FakeSolverrClient(
        get_result=SolverResult(
            status=200,
            response_text=OK_JSON,
            cookies=[Cookie(name="cf_clearance", value="fresh")],
            user_agent="Chrome/Real",
        )
    )
    res = await RECIPE.resolve(client, _req())
    assert res.ok
    assert res.download_url == "https://akirabox.com/8ifkke3kwpbc/file"
    assert res.file_name == "example.rar"
    assert res.size_bytes == int(1.2 * 1024**2)
    assert res.user_agent == "Chrome/Real"
    assert any(c.name == "cf_clearance" for c in res.cookies)
    assert client.ensured == 1 and client.reset == 0
    # The metadata API is queried with the canonical /file url, url-encoded.
    assert client.calls == [
        ("get", "https://akirabox.com/api/files?url=https%3A%2F%2Fakirabox.com%2F8ifkke3kwpbc%2Ffile")
    ]


async def test_missing_page_url_fails_fast():
    res = await RECIPE.resolve(FakeSolverrClient(), _req(page_url=""))
    assert not res.ok
    assert res.error == "missing page_url"


async def test_file_not_found_is_clean_failure():
    client = FakeSolverrClient(get_result=SolverResult(status=200, response_text=NOT_FOUND_JSON))
    res = await RECIPE.resolve(client, _req())
    assert not res.ok
    assert res.error == "File not found"


async def test_gated_non_json_is_clean_failure():
    gated = SolverResult(status=200, response_text="<html>Just a moment</html>")
    client = FakeSolverrClient(get_result=gated)
    res = await RECIPE.resolve(client, _req())
    assert not res.ok
    assert "no metadata" in res.error


async def test_solver_error_on_get_surfaces():
    res = await RECIPE.resolve(FakeSolverrClient(raise_on="get"), _req())
    assert not res.ok
    assert "boom" in res.error


def test_canonical_normalizes_url():
    assert _canonical("https://akirabox.com/abc123") == "https://akirabox.com/abc123/file"
    assert _canonical("https://akirabox.com/abc123/file") == "https://akirabox.com/abc123/file"
    assert _canonical("not a url") == ""


def test_size_bytes_parses_display_and_int():
    assert _size_bytes("1.2 MB") == int(1.2 * 1024**2)
    assert _size_bytes("500 KB") == 500 * 1024
    assert _size_bytes(3616623326) == 3616623326
    assert _size_bytes("12345") == 12345
    assert _size_bytes(None) == 0
