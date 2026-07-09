# Changelog

All notable changes to Slipgate, the self-hosted challenge-solving download
resolver for [Union.Manifold](https://github.com/fyiel/Union.Manifold) and any
other client.

## 0.5.1

### Changed

- `POST /fetch` now runs through one warm, shared FlareSolverr session (the same
  pattern the per-host recipes use) instead of spinning up a fresh browser per
  call. the first fetch pays the Cloudflare solve; subsequent same-origin fetches
  reuse the clearance cookie, so a catalogue pull that used to re-solve on every
  request (~12s each) now solves once and the rest return at page-load speed.
  requests serialize on the session lock, and a stale session (expiry /
  FlareSolverr restart) is reset and the fetch retried once.

## 0.5.0

### Added

- optional upstream proxy (`SLIPGATE_PROXY_URL`). when set, every FlareSolverr
  request routes the browser through it, so a datacenter host can borrow a
  residential/clean egress to reach sites Cloudflare challenges by IP.
  credentials may be embedded in the URL and are split into FlareSolverr's
  separate username/password fields (Chrome's `--proxy-server` rejects inline
  credentials).
- new `POST /fetch` endpoint: fetches a URL through the solver's browser (and
  proxy) and returns its body, with no per-host recipe. it exists to pull a
  Cloudflare-gated static resource — such as a source-catalogue JSON — that a
  plain HTTP client cannot retrieve from a challenged IP; Chrome's JSON view is
  decoded back to raw JSON before returning.

## 0.4.1

### Fixed

- `datavaults` now returns a real link. A successful XFS `download2` is a 302 to
  the CDN file, which FlareSolverr turns into a browser download and never
  surfaces, so the recipe always failed. It now uses FlareSolverr only to clear
  any Cloudflare gate (and adopt its User-Agent + cookies), then replays the
  `download1`/`download2` form POSTs with a plain HTTP client (redirects off) and
  reads the direct URL from the 302 `Location`. `SolverResult` now also carries
  the final navigation `url`.
- `vikingfile` now fails honestly. Its link is gated behind an embedded
  Cloudflare **Turnstile** widget; FlareSolverr clears Cloudflare's own challenge
  but does not solve embedded Turnstile widgets, so no token is minted. The
  recipe returns `needs_interactive` with a clear reason instead of a vague
  failure. Resolving this host needs a Turnstile-solving service.

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
