# syntax=docker/dockerfile:1
# Tischlein-Image. Werkzeuge kommen wie lokal und im CI aus mise.toml/mise.lock,
# Python-Abhängigkeiten strikt aus uv.lock – derselbe Build auf jedem Rechner.

ARG DEBIAN=debian:trixie-slim

# ---------- build: mise installiert Python und uv, uv die Abhängigkeiten ----------
FROM ${DEBIAN} AS build
ARG MISE_VERSION
ENV MISE_DATA_DIR=/opt/mise \
    MISE_CACHE_DIR=/tmp/mise-cache \
    MISE_YES=1 \
    MISE_LOCKED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
RUN test -n "$MISE_VERSION" \
 && curl -fsSL https://mise.run | MISE_VERSION="v${MISE_VERSION}" MISE_INSTALL_PATH=/usr/local/bin/mise sh \
 && mise --version
WORKDIR /app
COPY mise.toml mise.lock ./
RUN mise trust && mise install python uv
COPY pyproject.toml uv.lock README.md ./
RUN mise exec -- uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN mise exec -- uv sync --frozen --no-dev

# ---------- runtime: nur das gepinnte Python und die fertige .venv ----------
FROM ${DEBIAN}
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home /app tischlein
# .venv verweist per absolutem Pfad auf dieses Python, daher derselbe Ort wie im Build.
COPY --from=build /opt/mise/installs/python /opt/mise/installs/python
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY tischlein.toml /app/tischlein.toml
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    TISCHLEIN_DB=/data/tischlein.db \
    TISCHLEIN_CONFIG=/app/tischlein.toml \
    TZ=Europe/Berlin \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown tischlein /data
USER tischlein
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"
CMD ["tischlein", "serve"]
