# Slipgate no longer runs a browser: it talks to FlareSolverr over HTTP. So the
# image is just the small Python service.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY slipgate ./slipgate
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 slipgate

USER slipgate

ENV SLIPGATE_HOST=0.0.0.0 \
    SLIPGATE_PORT=8189

EXPOSE 8189
CMD ["slipgate"]
