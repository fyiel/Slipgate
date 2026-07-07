"""Slipgate: a self-hosted challenge-solving download resolver.

A real browser (nodriver-driven Chrome) clears Cloudflare or similar gates that a
plain HTTP client cannot, runs a per-host recipe to obtain a direct download URL,
and hands that URL back to the caller. The caller (for example the Union.Manifold
launcher) then downloads the signed URL with its own downloader.

The version here is the single source of truth and is asserted against
pyproject.toml in CI, mirroring the launcher's version discipline.
"""

__version__ = "0.1.0"
