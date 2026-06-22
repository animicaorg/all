#!/bin/sh
# Entrypoint for the anm-studio-ide agent sidecar container.
# Starts the headless FastAPI agent on AGENT_PORT (default 8090), bound to all
# interfaces so the broker can reach it across the docker network.
set -eu

AGENT_PORT="${AGENT_PORT:-8090}"
export REPO_DIR="${REPO_DIR:-/home/studio/workspace/repo}"

# Ensure the repo dir exists and is writable by the running user.
mkdir -p "${REPO_DIR}"

exec uvicorn animica_studio.agent_http.main:app \
    --host 0.0.0.0 \
    --port "${AGENT_PORT}" \
    --no-access-log
