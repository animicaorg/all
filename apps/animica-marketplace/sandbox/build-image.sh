#!/usr/bin/env bash
# Build the Animica Python Cloud sandbox image.
#   ./sandbox/build-image.sh [tag]
# The tag must match CLOUD_SANDBOX_IMAGE (lib/cloud/config.ts, default anm-pycloud-runtime:1).
set -euo pipefail
TAG="${1:-${CLOUD_SANDBOX_IMAGE:-anm-pycloud-runtime:1}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "building $TAG from $HERE"
docker build -t "$TAG" "$HERE"
echo "--- smoke: the image must have no network clients and must refuse to write to / ---"
docker run --rm --network none --read-only --tmpfs /tmp:size=16m \
  --entrypoint python3 "$TAG" -c \
  'import importlib,sys
for bad in ("requests","httpx","aiohttp"):
    try:
        importlib.import_module(bad); print("FAIL: %s importable" % bad); sys.exit(1)
    except ImportError: pass
try:
    open("/proof","w"); print("FAIL: rootfs writable"); sys.exit(1)
except OSError: pass
import numpy, pandas, yaml  # noqa: F401
print("sandbox image OK")'
