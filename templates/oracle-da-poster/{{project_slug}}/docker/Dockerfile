# syntax=docker/dockerfile:1
##
## Animica Oracle DA Poster — Container Image (multi-stage)
## - Builder stage creates wheels to keep the runtime image slim.
## - Runtime stage runs as an unprivileged user and uses tini as PID 1.
## - The app reads configuration from environment variables (or --env-file).
##
## Build:
##   docker build -t {{project_slug}}:latest -f docker/Dockerfile .
##
## Run (example):
##   docker run --rm \
##     --name {{project_slug}} \
##     --env-file ./.env \
##     -v $(pwd)/data:/var/lib/{{project_slug}} \
##     {{project_slug}}:latest
##
## Notes:
## - Mount a writable volume to /var/lib/{{project_slug}} if you want durable state.
## - Provide RPC_URL / CHAIN_ID / ORACLE_CONTRACT et al. via env.
## - Health checking can be added later once a /health endpoint exists.
##

############################
# Stage 1: builder (wheels)
############################
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps for building wheels when upstream provides no manylinux wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      gcc \
      libffi-dev \
      git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /w

# Copy only requirements first to leverage Docker layer caching.
COPY requirements.txt /w/requirements.txt

# Pin/upgrade pip & build wheels for all deps into /wheels.
RUN python -m pip install --upgrade pip wheel \
 && pip wheel --wheel-dir /wheels -r /w/requirements.txt

############################
# Stage 2: runtime (slim)
############################
FROM python:3.11-slim AS runtime

# Minimal runtime OS packages; tini handles proper signal forwarding (PID 1).
RUN apt-get update && apt-get install -y --no-install-recommends \
      tini \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # App defaults—override via --env or --env-file as needed
    LOG_LEVEL=info

# App directory
WORKDIR /app

# Install dependencies from prebuilt wheels
COPY --from=builder /wheels /wheels
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt \
 && rm -rf /wheels /tmp/requirements.txt

# Copy application source (keep context minimal)
COPY oracle_poster ./oracle_poster
COPY pyproject.toml README.md .env.example ./

# Create an unprivileged user and grant ownership of work/state/log dirs
RUN groupadd -r app && useradd -r -g app app \
 && mkdir -p /var/lib/{{project_slug}} /var/log/{{project_slug}} \
 && chown -R app:app /app /var/lib/{{project_slug}} /var/log/{{project_slug}}

USER app

# Use tini for proper signal handling and zombie reaping
ENTRYPOINT ["/usr/bin/tini","--"]

# Execute the poster in module mode; it reads env at startup
CMD ["python","-m","oracle_poster.main"]
