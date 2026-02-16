#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
docker compose -f docker/docker-compose.yml up -d
npx prisma migrate dev
npm run dev
