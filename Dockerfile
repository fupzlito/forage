FROM python:3.13-slim

WORKDIR /srv/forage

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (layer caching). Playwright downloads Chromium + system deps.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium
    # To enable patchright anti-detection fork, uncomment below and in requirements.txt:
    # && (patchright install chromium || true)

# Application code & templates
COPY app/ app/
COPY config.example.yaml config.example.yaml
COPY prompts.example.yaml prompts.example.yaml

# Factory-default config (users override via bind mount in compose)
COPY config.example.yaml /etc/forage/config.yaml
COPY prompts.example.yaml /etc/forage/prompts.yaml

ENV FORAGE_CONFIG=/etc/forage/config.yaml

EXPOSE 3672

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -sf http://localhost:3672/health || exit 1

CMD ["python", "-m", "app.main"]
