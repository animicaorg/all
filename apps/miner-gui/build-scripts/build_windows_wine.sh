#!/usr/bin/env bash
# Cross-build the Windows Animica Miner GUI .exe FROM Linux, using a Dockerized
# Wine + Windows-Python + PyInstaller image.
#
# This is the only way to produce a Windows artifact without a Windows host.
# (PyInstaller is not a cross-compiler: it freezes the interpreter it runs on,
#  so we run a *Windows* CPython inside Wine and let PyInstaller emit a PE/.exe.)
#
# It builds the image from Dockerfile.windows-wine, then runs build_windows.sh
# inside the container in Wine mode against the bind-mounted repository.
#
# macOS CANNOT be cross-built from Linux: Apple's SDK / code-signing toolchain
# is not redistributable and cannot run under Wine. The macOS .app/.dmg is built
# exclusively on the macOS CI runner (see build_macos.sh).
#
# Requirements:
#   - Docker
#
# Usage:
#   ./build_windows_wine.sh
#
# Env overrides:
#   IMAGE_TAG      docker image tag to build/use (default: animica-miner-wine)
#   PYWINE_TAG     base tobix/pywine tag (default: 3.12)
#   NO_BUILD       set to 1 to skip `docker build` (reuse an existing image)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # apps/miner-gui
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"        # repo root
IMAGE_TAG="${IMAGE_TAG:-animica-miner-wine}"
PYWINE_TAG="${PYWINE_TAG:-3.12}"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.windows-wine"

log()  { printf "\033[1;34m[wine-cross]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

command -v docker >/dev/null 2>&1 || die "Docker is required for the Wine cross-build"
[[ -f "$DOCKERFILE" ]] || die "Dockerfile not found: $DOCKERFILE"

if [[ "${NO_BUILD:-0}" != "1" ]]; then
    log "Building Wine cross-build image '$IMAGE_TAG' (pywine $PYWINE_TAG)..."
    docker build \
        --build-arg "PYWINE_TAG=${PYWINE_TAG}" \
        -f "$DOCKERFILE" \
        -t "$IMAGE_TAG" \
        "$SCRIPT_DIR"
else
    log "NO_BUILD=1: reusing existing image '$IMAGE_TAG'"
fi

log "Running PyInstaller in Wine against the bind-mounted repo..."
# Bind-mount the repo at /src (matches the Dockerfile's WORKDIR / CMD).
docker run --rm \
    -v "$REPO_ROOT":/src \
    -w /src \
    "$IMAGE_TAG" \
    "WINE=wine PYTHON='wine python' /src/apps/miner-gui/build-scripts/build_windows.sh"

log "Done. Windows artifacts written to: $APP_DIR/dist"
log "  (manifest log: $APP_DIR/dist/artifacts.jsonl)"
