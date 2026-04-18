#!/usr/bin/env sh
set -eu

URL="${1:-http://127.0.0.1:8660/healthz}"
if command -v curl >/dev/null 2>&1; then
  curl -fsS "${URL}" >/dev/null
else
  wget -q -O /dev/null "${URL}"
fi

