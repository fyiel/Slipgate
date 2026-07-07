# Changelog

All notable changes to Slipgate, the self-hosted challenge-solving download
resolver for [Union.Manifold](https://github.com/fyiel/Union.Manifold) and any
other client.

## 0.2.0

Reworked to wrap FlareSolverr instead of driving its own browser.

### Changed

- Slipgate no longer launches Chrome itself. It delegates gate clearing to a
  FlareSolverr instance (bundled in the compose file, or point it at one you
  already run via SLIPGATE_FLARESOLVERR_URL) and focuses on what it adds:
  per-host recipes that turn a cleared page into a direct download URL. this
  drops the Chromium and Xvfb weight from the image and reuses a solver already
  proven against Cloudflare
- the image is now a small Python service with no browser, and docker compose
  brings up FlareSolverr and Slipgate together
- the browser, headless, sandbox and CDP settings are gone; the default service
  port is now 8189 so it does not clash with FlareSolverr's 8191

### Added

- the NexusMods recipe runs entirely through FlareSolverr: it warms a session on
  the file page, waits out the free countdown, and posts the GenerateDownloadUrl
  endpoint, parsing the CDN mirror URL out of the response

## 0.1.0

Initial release.

### Added

- a self-hosted HTTP service that clears Cloudflare-style gates with a real
  nodriver-driven Chrome and returns a direct download URL, so a plain
  downloader can fetch files from hosts that block non-browser clients
- GET /health for liveness and recipe discovery, and POST /resolve to turn a
  gated page into a direct download URL, with optional X-Slipgate-Key auth
- a pluggable per-host recipe framework, with a NexusMods free manual-download
  recipe that seeds a pasted login session and lets the browser mint the
  Cloudflare clearance itself
- a Docker image and compose file for one-command self-hosting, published to the
  GitHub Container Registry on every release
