# Animica Pool worker — registers, benchmarks, heartbeats, and serves
# inference jobs against a local OpenAI-compatible model (LOCAL_INFERENCE_URL).
# Build from the repo root:
#   docker build -f docker/worker.Dockerfile -t animicaorg/worker:latest .
FROM node:20-slim
WORKDIR /app
RUN npm install -g tsx@4.19.0
COPY packages/worker-agent/src ./src
# Defaults; override at `docker run -e ...`:
ENV ANIMICA_API_BASE=https://pool.animica.org
ENV HEARTBEAT_MS=15000
# WORKER_TOKEN (required), LOCAL_INFERENCE_URL, LOCAL_INFERENCE_MODEL are
# provided at runtime.
CMD ["tsx", "src/agent.ts"]
