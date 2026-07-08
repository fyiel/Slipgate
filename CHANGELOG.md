# Changelog

All notable changes to Slipgate, the self-hosted challenge-solving download
resolver for [Union.Manifold](https://github.com/fyiel/Union.Manifold) and any
other client.

## 0.4.0

### Added

- three more download recipes, so the launcher resolves these file hosts in-app
  instead of falling back to a browser: `datavaults` (`datavaults.co`),
  `vikingfile` (`vikingfile.com`, `vik1ngfile.site`) and `akirabox`
  (`akirabox.com`). each reuses one warm FlareSolverr session, retries once on a
  solver error, and returns the direct download URL plus the cookies and
  User-Agent the browser ended up with
- the DataVaults recipe drives the XFileSharing two-step free flow through
  FlareSolverr (download1 -> download2), solving the positional-digit captcha
  deterministically from the page and honouring the countdown before reading the
  direct CDN link off the final page
- the ViKiNG FiLE recipe clears the Cloudflare Turnstile on the file page and
  reads the direct server link from the populated download button or the
  download API's JSON
- the Akira Box recipe queries the public File Status API through the cleared
  Cloudflare session and returns the file's direct link, name and size

## 0.3.1

### Changed

- the Nexus recipe tries the generate call directly first, skipping the 4.7MB
  file-page load and the countdown wait that dominated resolve time. Nexus's
  download countdown is client-side, so a warm resolve now returns in a couple of
  seconds instead of ~18s; it falls back to the full page-visit-plus-wait flow
  only if the direct call yields nothing

## 0.3.0

### Changed

- resolves now reuse one warm FlareSolverr session instead of creating and
  destroying a fresh browser per request. the browser spin-up and the Cloudflare
  solve are paid once, so the first resolve is as slow as before but every
  subsequent one is much faster. requests to the shared session are serialized
  and each re-seeds its own cookies, so it stays correct across users, and a
  session that expires or dies is reset and retried once automatically

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
