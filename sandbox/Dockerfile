# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Argus sandbox image
# Base: official Microsoft Playwright Python image matching playwright==1.63.0
# See: https://mcr.microsoft.com/en-us/artifact/mar/playwright/python/tags
# ---------------------------------------------------------------------------
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

# Fail fast on any error
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

# System timezone / locale (avoids interactive prompts during apt)
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Disable LLM by default — the sandbox proves the deterministic core only
    ARGUS_LLM=off \
    # Playwright uses this for headless Chromium inside the container
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Working directory inside the container
WORKDIR /app

# Copy project files (respects .dockerignore — .env, .venv, .argus*, .git are excluded)
COPY . .

# Install Argus and its pure-Python runtime dependencies.
# The image already ships Chromium; we only pip-install, no playwright install needed.
RUN pip install --no-cache-dir -e . && python -m playwright install chromium

# Ensure the output directory exists; it is bind-mounted from the host at runtime
RUN mkdir -p /out

# Make stress.sh executable
RUN chmod +x /app/sandbox/stress.sh

# Default entry point — overridden by docker-compose
ENTRYPOINT ["/bin/bash", "/app/sandbox/stress.sh"]
