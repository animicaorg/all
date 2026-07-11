# animica-dev-ai — free GitHub coding-agent backend

Powers https://animica.dev/agent. FastAPI (uvicorn :8793), systemd `animica-dev-ai.service`.
Runs a bounded ReAct loop over the FREE Animica gateway (https://animica.dev/v1, no key),
using a per-request GitHub token that is never stored or logged. Only ever writes to a NEW
branch and opens a PR. Model: qwen2.5:7b (CPU). Endpoints: /agent/health,
/agent/github/repos, /agent/run.
