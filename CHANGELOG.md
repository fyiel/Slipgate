# Changelog

All notable changes to Slipgate, the self-hosted challenge-solving download
resolver for [Union.Manifold](https://github.com/fyiel/Union.Manifold) and any
other client.

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
