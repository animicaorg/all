# anm-studio-ide — headless agent sidecar image (Animica Web IDE, Increment 1).
#
# Build context MUST be the animica-studio-qt package dir:
#   docker build -t anm-studio-ide \
#     -f studio-host/docker/ide/Dockerfile \
#     /root/animica/animica-studio-qt
#
# Minimal Python image: git + FastAPI/uvicorn/pydantic/httpx only. No X11, no
# Node, no PySide6 (the agent_http app + the services it imports are Qt-free).
FROM python:3.12-slim

# --- system deps --------------------------------------------------------- #
# git is required for /git/clone; ca-certificates for https github.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
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
    REPO_DIR=/home/studio/workspace/repo

# Workspace owned by the unprivileged user.
RUN mkdir -p /home/studio/workspace/repo \
    && chown -R studio:studio /home/studio /app

WORKDIR /home/studio/workspace
EXPOSE 8090
USER studio

CMD ["sh", "-c", "exec uvicorn animica_studio.agent_http.main:app --host 0.0.0.0 --port ${AGENT_PORT:-8090} --no-access-log"]
