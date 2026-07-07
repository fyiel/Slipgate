# Slipgate

Self-hosted, challenge-solving download resolver. It wraps
[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) (which clears
Cloudflare-style gates with a real browser) and adds the part FlareSolverr does
not do: per-host recipes that turn a gated page into a **direct download URL**,
so your own downloader can fetch the file.

Built as a companion to [Union.Manifold](https://github.com/fyiel/Union.Manifold),
but it is a standalone HTTP service with a small, generic API.

## Why

Some file hosts (NexusMods free downloads and others) gate their download links
behind a Cloudflare JS/TLS challenge. A normal HTTP client fails that challenge
even with valid cookies, because Cloudflare fingerprints the TLS handshake, not
just the session. FlareSolverr clears it with a real browser; Slipgate then knows
how to walk a given host from a cleared page to the actual download link.

## Quickstart

```sh
docker compose up -d      # starts FlareSolverr and Slipgate together
curl localhost:8189/health
```

`flaresolverr_ok: true` in the health response means Slipgate can reach the
solver. Slipgate binds to `127.0.0.1:8189` by default (FlareSolverr keeps 8191).

Already running FlareSolverr? Skip the bundled one and point Slipgate at yours
with `SLIPGATE_FLARESOLVERR_URL`.

## API

### `GET /health`

```json
{ "ok": true, "version": "0.2.0", "flaresolverr_ok": true, "recipes": ["nexusmods"] }
```

### `POST /resolve`

Resolve a gated page to a direct download URL.

```json
{
  "host": "nexusmods",
  "params": { "domain": "skyrimspecialedition", "mod_id": "266", "file_id": "1000", "game_id": "1704" },
  "cookies": [{ "name": "nexusmods_session", "value": "<your session>" }]
}
```

Response:

```json
{
  "ok": true,
  "download_url": "https://<cdn>/...",
  "cookies": [{ "name": "cf_clearance", "value": "..." }],
  "user_agent": "Mozilla/5.0 ...",
  "needs_interactive": false,
  "error": ""
}
```

`cookies` in the request seed a logged-in session (for hosts that need one). You
never paste `cf_clearance`; FlareSolverr mints it. `cookies` in the response are
what the browser held after clearing the gate, so a caller that downloads the URL
itself can present a matching session if the file host also checks it.

## Configuration

All settings are read from the environment with the `SLIPGATE_` prefix (or a
`.env` file):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SLIPGATE_HOST` | `127.0.0.1` | Bind address. Keep it on loopback unless you set an API key. |
| `SLIPGATE_PORT` | `8189` | Bind port (FlareSolverr uses 8191). |
| `SLIPGATE_API_KEY` | (empty) | When set, every `/resolve` needs `X-Slipgate-Key`. |
| `SLIPGATE_FLARESOLVERR_URL` | `http://localhost:8191/v1` | The FlareSolverr endpoint. |
| `SLIPGATE_FLARESOLVERR_TIMEOUT_MS` | `60000` | Per-request maxTimeout handed to FlareSolverr. |
| `SLIPGATE_RESOLVE_TIMEOUT_SECS` | `150` | Overall per-request ceiling. |
| `SLIPGATE_MAX_CONCURRENCY` | `2` | Concurrent resolves. |

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

The test suite runs with no FlareSolverr and no network: the solver client is
faked. Recipes are verified against live hosts through a real FlareSolverr.
