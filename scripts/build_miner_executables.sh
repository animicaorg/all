#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "error: python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS" in
  linux*) PLATFORM="linux" ;;
  darwin*) PLATFORM="macos" ;;
  msys*|mingw*|cygwin*) PLATFORM="windows" ;;
  *)
    echo "error: unsupported platform '$OS'" >&2
    exit 1
    ;;
esac

ENTRYPOINT="animica-miner"
if [[ "$PLATFORM" == "windows" ]]; then
  ENTRYPOINT="${ENTRYPOINT}.exe"
fi

DIST_DIR="$ROOT_DIR/artifacts/miners/bin/$PLATFORM"
BUILD_DIR="$ROOT_DIR/.build/miner-exec/$PLATFORM"
mkdir -p "$DIST_DIR" "$BUILD_DIR"

if [[ "${ANIMICA_SKIP_PYINSTALLER_INSTALL:-0}" != "1" ]]; then
  "$PYTHON_BIN" -m pip install --upgrade pyinstaller
fi

export PYTHONPATH="$ROOT_DIR/python:$ROOT_DIR:${PYTHONPATH:-}"
"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name "${ENTRYPOINT%.*}" \
  --distpath "$BUILD_DIR/dist" \
  --workpath "$BUILD_DIR/work" \
  --specpath "$BUILD_DIR/spec" \
  "$ROOT_DIR/python/animica/stratum_pool/reference_cpu_miner.py"

cp "$BUILD_DIR/dist/$ENTRYPOINT" "$DIST_DIR/$ENTRYPOINT"
chmod +x "$DIST_DIR/$ENTRYPOINT"

echo "Built miner executable:"
echo "  platform: $PLATFORM"
echo "  output:   $DIST_DIR/$ENTRYPOINT"
