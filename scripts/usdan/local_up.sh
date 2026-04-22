#!/usr/bin/env bash
set -euo pipefail

docker compose -f ops/docker/docker-compose.usdan.yml up -d --build

echo "USDAN local stack started:"
echo "- API: http://127.0.0.1:8098/healthz"
echo "- Web: http://127.0.0.1:5188"
