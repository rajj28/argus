# Argus on Hugging Face Spaces (Docker SDK, single container, port 7860).
# push.py uploads this file as the Space's root Dockerfile; see deploy/hf_space/README.md.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY argus ./argus
RUN pip install -e .

COPY . .
RUN pip install -e . && mkdir -p /data

ENV ARGUS_LIVE_HOME=/data/.argus-live \
    SKYOPS_URL=http://127.0.0.1:8000 \
    SKYOPS_B_URL=http://127.0.0.1:8001

EXPOSE 7860

CMD ["bash", "deploy/hf_space/start.sh"]
