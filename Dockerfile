# Slipgate runs a real Chromium, so the image ships one plus the service.
FROM python:3.12-slim

# Chromium and the fonts/libs it needs. The chromium package pulls its own
# shared-library dependencies; fonts-liberation avoids blank glyph boxes that can
# trip some bot checks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        fonts-liberation \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user; Chrome's setuid sandbox is disabled via
# SLIPGATE_SANDBOX below because containers rarely grant the needed privileges.
RUN useradd --create-home --uid 10001 slipgate
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY slipgate ./slipgate
RUN pip install --no-cache-dir .

USER slipgate

ENV SLIPGATE_HOST=0.0.0.0 \
    SLIPGATE_PORT=8191 \
    SLIPGATE_HEADLESS=true \
    SLIPGATE_SANDBOX=false \
    SLIPGATE_BROWSER_PATH=/usr/bin/chromium \
    SLIPGATE_BROWSER_ARGS=--disable-dev-shm-usage

EXPOSE 8191
CMD ["slipgate"]
