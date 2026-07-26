# syntax=docker/dockerfile:1

# Python is pinned to 3.12 deliberately: ctranslate2 (faster-whisper's inference
# engine) publishes no wheel for 3.13+, so the whole service is containerised on
# a version where every dependency has a prebuilt wheel.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/home/app/.cache/huggingface

# libgomp1 is a hard requirement of ctranslate2 — `import faster_whisper` fails
# on python:*-slim without it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /srv

# Dependency layer first, so editing application code doesn't reinstall the
# (slow) ML dependency tree.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

COPY app ./app
COPY tests ./tests

# The Whisper weights volume mounts here; create it owned by `app` so the
# named volume inherits writable ownership.
RUN mkdir -p /home/app/.cache/huggingface \
 && chown -R app:app /home/app /srv

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
