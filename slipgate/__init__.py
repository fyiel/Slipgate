"""Slipgate: a self-hosted challenge-solving download resolver.

Slipgate wraps a FlareSolverr instance (which clears Cloudflare-style gates with a
real browser) and adds per-host download resolution on top: it drives the gate
through FlareSolverr, runs a recipe to obtain a direct download URL, and returns
that URL so the caller (for example the Union.Manifold launcher) can fetch the
file with its own downloader.

The version here is the single source of truth and is asserted against
pyproject.toml in CI, mirroring the launcher's version discipline.
"""

__version__ = "0.3.1"
