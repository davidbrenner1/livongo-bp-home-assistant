FROM python:3.12-slim-bookworm

ARG BUILD_VERSION=0.2.0
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHROMIUM_PATH=/usr/bin/chromium

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       chromium \
       ca-certificates \
       fonts-liberation \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
    Flask==3.1.2 \
    playwright==1.55.0 \
    requests==2.32.5

COPY app /app
WORKDIR /app

CMD ["python", "-u", "/app/main.py"]
