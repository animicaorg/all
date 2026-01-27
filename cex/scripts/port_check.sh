#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-}"
if [[ -z "$PORT" ]]; then
  echo "Usage: pnpm -C cex ports:check <port>"
  exit 1
fi

if command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  exit 0
fi

if command -v ss >/dev/null 2>&1; then
  ss -ltnp | grep ":${PORT}" || true
  exit 0
fi

if command -v netstat >/dev/null 2>&1; then
  netstat -tulpn 2>/dev/null | grep ":${PORT}" || true
  exit 0
fi

echo "No lsof/ss/netstat available to inspect port ${PORT}."
exit 1
