"""Request and response models: the wire contract every client codes against.

A resolve takes a target page plus optional seed cookies (for example a pasted
login session) and returns a direct, ready-to-download URL along with the cookies
and User-Agent the browser ended up with, so the caller's downloader can replay
them if the file host also checks them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Cookie(BaseModel):
    name: str
    value: str
    domain: str = ""
    path: str = "/"


class ResolveRequest(BaseModel):
    # Recipe selector, for example "nexusmods". Chooses the per-host automation.
    host: str
    # The page the recipe starts from (a mod file page, a hoster download page).
    page_url: str = ""
    # Free-form recipe inputs (game domain, mod id, file id, and so on).
    params: dict[str, str] = Field(default_factory=dict)
    # Session cookies to seed before navigating, for example a login session the
    # user pasted once. cf_clearance is NOT expected here; the browser mints it.
    cookies: list[Cookie] = Field(default_factory=list)
    # Opaque handle to reuse a warmed, logged-in browser context across calls.
    session_id: str = ""


class ResolveResponse(BaseModel):
    ok: bool
    # The direct download URL when ok is true.
    download_url: str = ""
    file_name: str = ""
    size_bytes: int = 0
    # The cookies and UA the browser held after clearing the gate, so a caller
    # downloading the URL itself can present a matching session if required.
    cookies: list[Cookie] = Field(default_factory=list)
    user_agent: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    # Populated when ok is false: a short, human-readable reason.
    error: str = ""
    # Set when the failure is a challenge that needs a visible browser to solve.
    needs_interactive: bool = False


class HealthResponse(BaseModel):
    ok: bool
    version: str
    flaresolverr_ok: bool
    recipes: list[str]


class FetchRequest(BaseModel):
    # The URL to fetch through the solver's browser (and proxy, if configured).
    url: str


class FetchResponse(BaseModel):
    ok: bool
    status: int = 0
    # The document body. For a JSON endpoint the browser's JSON view is decoded
    # back to the raw JSON text; otherwise this is the page HTML.
    body: str = ""
    error: str = ""
