# Slipgate

Self-hosted, challenge-solving download resolver. It drives a real browser
(nodriver-controlled Chrome) to slip through Cloudflare and similar gates that a
plain HTTP client cannot, runs a per-host recipe to obtain a direct download URL,
and returns that URL so your own downloader can fetch the file.

Built as a companion to [Union.Manifold](https://github.com/fyiel/Union.Manifold),
but it is a standalone HTTP service with a small, generic API, so anything can
call it.

## Why

Some file hosts (NexusMods free downloads and others) gate their download links
behind a Cloudflare JS/TLS challenge. A normal HTTP client fails that challenge
even with valid cookies, because Cloudflare fingerprints the TLS handshake, not
just the session. A real browser clears it. Slipgate runs that browser for you,
once, behind an API, so the rest of your tooling can keep using a plain
downloader.

## Quickstart

```sh
docker compose up -d
curl localhost:8191/health
```

By default the service binds to `127.0.0.1:8191` and runs Chrome headless.

## API

### `GET /health`

```json
{ "ok": true, "version": "0.1.0", "engine_ready": true, "recipes": ["nexusmods"] }
```

### `POST /resolve`

Resolve a gated page to a direct download URL.

```json
{
  "host": "nexusmods",
  "params": { "domain": "skyrimspecialedition", "mod_id": "266", "file_id": "1000", "game_id": "110" },
  "cookies": [{ "name": "nexusmods_session", "value": "<your session>" }]
}
```

Response:

```json
{
  "ok": true,
  "download_url": "https://<cdn>/...",
  "file_name": "",
  "size_bytes": 0,
  "cookies": [{ "name": "cf_clearance", "value": "..." }],
  "user_agent": "Mozilla/5.0 ...",
  "needs_interactive": false,
  "error": ""
}
```

`cookies` in the request seed a logged-in session (for hosts that need one). You
never paste `cf_clearance`; the browser mints it. `cookies` in the response are
what the browser held after clearing the gate, so a caller that downloads the URL
itself can present a matching session if the file host also checks it.

## Configuration

All settings are read from the environment with the `SLIPGATE_` prefix (or a
`.env` file):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SLIPGATE_HOST` | `127.0.0.1` | Bind address. Keep it on loopback unless you set an API key. |
| `SLIPGATE_PORT` | `8191` | Bind port. |
| `SLIPGATE_API_KEY` | (empty) | When set, every `/resolve` needs `X-Slipgate-Key`. |
| `SLIPGATE_HEADLESS` | `true` | Run Chrome headless. |
| `SLIPGATE_BROWSER_PATH` | (auto) | Explicit Chrome/Chromium path. |
| `SLIPGATE_CHALLENGE_TIMEOUT_SECS` | `40` | How long to wait for a challenge to clear. |
| `SLIPGATE_RESOLVE_TIMEOUT_SECS` | `90` | Overall per-request ceiling. |
| `SLIPGATE_MAX_CONCURRENCY` | `2` | Concurrent browser tabs. |

## Security

Replaying a logged-in session is sensitive. Bind to loopback, or set
`SLIPGATE_API_KEY` and put the service behind your own TLS/reverse proxy before
exposing it. Only ever use it with your own accounts.

## Legal

Automating a website's download flow may be against that site's Terms of Service.
This tool is for self-hosting against your own accounts and content you are
entitled to. You are responsible for how you use it.

## Development

```sh
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run ruff check .
uv run pytest -q
```

The test suite runs with no browser: the engine is faked. The nodriver engine and
per-host recipes are verified against live hosts on a machine with Chrome.
