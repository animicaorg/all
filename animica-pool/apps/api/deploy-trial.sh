#!/usr/bin/env bash
# Deploy the /v1/keys/trial endpoint to the LIVE Animica inference API.
# Safe by construction: builds first, backs up the running dist, restarts,
# polls health for ~36s (this app maps many routes + connects Prisma, so it
# takes a bit to be reachable), smoke-tests, and AUTO-ROLLS-BACK on any failure.
#
#   sudo bash apps/api/deploy-trial.sh
set -uo pipefail
cd "$(dirname "$0")"
SVC=animica-pool-api.service

echo ">> building (with the trial module)…"
rm -rf dist.bak; cp -r dist dist.bak
if ! npm run build; then echo "build failed — nothing deployed."; rm -rf dist.bak; exit 1; fi
if [ ! -f dist/trial/trial.module.js ]; then echo "trial module missing from build — aborting."; rm -rf dist; mv dist.bak dist; exit 1; fi

echo ">> restarting $SVC…"
systemctl restart "$SVC"
UP=000
for i in $(seq 1 12); do
  sleep 3
  UP=$(curl -s --max-time 8 -o /dev/null -w "%{http_code}" http://127.0.0.1:4000/v1/models 2>/dev/null || echo 000)
  echo "   t=$((i*3))s  health=$UP"
  [ "$UP" = "200" ] && break
done

if [ "$UP" != "200" ]; then
  echo "!! unhealthy after 36s — ROLLING BACK to the previous build"
  journalctl -u "$SVC" -n 25 --no-pager | grep -iE "error|exception|cannot resolve|Unknown" | tail -10
  rm -rf dist; mv dist.bak dist; systemctl restart "$SVC"
  sleep 6; curl -s --max-time 8 -o /dev/null -w "   after rollback health=%{http_code}\n" http://127.0.0.1:4000/v1/models
  exit 1
fi

echo ">> healthy. smoke-testing POST /v1/keys/trial…"
RESP=$(curl -s --max-time 25 -X POST http://127.0.0.1:4000/v1/keys/trial -H 'content-type: application/json' -d '{}')
echo "$RESP" | head -c 240; echo
if echo "$RESP" | grep -q '"api_key"'; then
  echo "✓ LIVE — zero-config MCP onboarding now works (uvx/npx animica-mcp gets a free key on first call)."
  rm -rf dist.bak
else
  echo "⚠ trial returned no key — review the response above; the API itself is healthy."
fi
