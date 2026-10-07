FROM python:3.11-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

ARG WEB_PORTAL_GIT_REVISION=unknown
LABEL org.opencontainers.image.revision=$WEB_PORTAL_GIT_REVISION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/inkypi/src \
    XDG_CACHE_HOME=/tmp/.cache

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        ca-certificates \
        chromium \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        libheif1 \
        libopenjp2-7 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 portal \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /app/home portal

COPY inkypi-weather/package/InkyPi/install/requirements-base.txt /tmp/requirements-base.txt
RUN python -m pip install --no-cache-dir --require-hashes --requirement /tmp/requirements-base.txt \
    && rm /tmp/requirements-base.txt

WORKDIR /app/inkypi
COPY --chown=10001:10001 inkypi-weather/package/InkyPi/src ./src

RUN mkdir -p \
        /app/config \
        /app/publication \
        /app/security \
        /app/worker-data \
        /run/provider-secrets \
        /run/session-key \
    && chown -R 10001:10001 /app /run/provider-secrets /run/session-key

USER 10001:10001

CMD ["python", "-m", "web_portal"]
