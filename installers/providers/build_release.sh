#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-0.2.0}"
./apps/aicf-provider/scripts/build_bundles.sh "$VERSION"
