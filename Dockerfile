# Paravent — container image.
#
# The image holds only the code and what ships with it (static assets, the rule tables
# in data/rules, the tag catalogue). Everything else comes in through volumes, keeping
# the two roots of TECHSTACK.md §3 apart:
#
#   /data/reference        the 5eTools corpus — mount read-only, never written
#   /data/library          everything made with Paravent — back this up
#   /data/reference-cache  derived artefacts — disposable
#   /data/backups          zip backups of the library (PARAVENT_BACKUP_AT)
#
# See docker-compose.yaml for a ready configuration.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# --- build: resolve the locked dependencies into a virtualenv -------------------
FROM base AS build

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Dependencies only: the app runs from /app, not as an installed package, because it
# finds static/ and data/ relative to its own source files.
RUN uv sync --frozen --no-dev --no-install-project

# --- runtime ----------------------------------------------------------------------
FROM base AS runtime

# Time zones, so TZ makes PARAVENT_BACKUP_AT=03:00 mean 03:00 where the NAS is; the
# slim image has none and would read every time as UTC.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
# The version is read from pyproject.toml at start (app/__init__.py).
COPY pyproject.toml pyproject.toml
# Paravent's own licence, and the licences of what the image carries from others: icons,
# fonts, htmx.
COPY LICENSE LICENSE
COPY NOTICE.md NOTICE.md
COPY app/ app/
COPY static/ static/
COPY data/rules/ data/rules/
COPY data/library/tags/5etools_tags.json data/library/tags/5etools_tags.json
COPY documentation/character.schema.json documentation/character.schema.json

RUN mkdir -p /data/reference /data/library /data/reference-cache /data/backups \
    && chmod 0777 /data/library /data/reference-cache /data/backups

ENV PATH="/opt/venv/bin:${PATH}" \
    PARAVENT_REFERENCE_DIR=/data/reference \
    PARAVENT_IMAGE_DIR=/data/img \
    PARAVENT_LIBRARY_DIR=/data/library \
    PARAVENT_REFERENCE_CACHE_DIR=/data/reference-cache \
    PARAVENT_PORT=8000

# Runs as an unprivileged user unless compose sets another (PUID/PGID there).
USER 1000:1000

EXPOSE 8000
VOLUME ["/data/library", "/data/reference-cache"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PARAVENT_PORT\"]}/health', timeout=4)"

# --proxy-headers with FORWARDED_ALLOW_IPS (unset: trust nobody) makes a NAS reverse
# proxy's client address visible to the login rate limiter.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PARAVENT_PORT\" --log-config app/log_config.json --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
