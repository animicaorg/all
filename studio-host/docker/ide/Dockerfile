# anm-studio-ide — headless agent sidecar image (Animica Web IDE).
#
# Build context MUST be the animica-studio-qt package dir:
#   docker build -t anm-studio-ide \
#     -f studio-host/docker/ide/Dockerfile \
#     /root/animica/animica-studio-qt
#
# Python image: git + FastAPI/uvicorn/pydantic/httpx for the agent sidecar,
# plus bash/curl + Node.js 20 (npm) so an interactive terminal and per-container
# dev servers (run/preview) work. No X11, no PySide6 (the agent_http app + the
# services it imports are Qt-free).
FROM python:3.12-slim

# --- system deps --------------------------------------------------------- #
# git: /git/clone. ca-certificates: https github + NodeSource. bash: the
# interactive terminal (docker exec ... /bin/bash -l). curl: NodeSource setup +
# preview port health probes. Node.js 20 + npm: dev servers (npm run dev/start).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ca-certificates bash curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

# --- python deps --------------------------------------------------------- #
RUN pip install --no-cache-dir \
    "fastapi>=0.110" \
    "uvicorn>=0.29" \
    "pydantic>=2.5" \
    "httpx>=0.27"

# --- non-root user (uid/gid 10001) --------------------------------------- #
RUN groupadd --gid 10001 studio \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/studio studio

# --- application package -------------------------------------------------- #
# Copy only the headless package into /app so PYTHONPATH=/app resolves
# `animica_studio.agent_http.main`.
COPY animica_studio /app/animica_studio

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    AGENT_PORT=8090 \
    DEV_PORT=8099 \
    REPO_DIR=/home/studio/workspace/repo

# Workspace owned by the unprivileged user.
RUN mkdir -p /home/studio/workspace/repo \
    && chown -R studio:studio /home/studio /app

WORKDIR /home/studio/workspace
# 8090: agent sidecar (FastAPI). 8099: per-container dev server (run/preview).
EXPOSE 8090 8099
USER studio

CMD ["sh", "-c", "exec uvicorn animica_studio.agent_http.main:app --host 0.0.0.0 --port ${AGENT_PORT:-8090} --no-access-log"]
