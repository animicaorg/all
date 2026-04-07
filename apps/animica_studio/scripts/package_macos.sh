#!/usr/bin/env bash
# Package Animica Studio for macOS using PyInstaller
# Usage: bash scripts/package_macos.sh
# Requirements: pip install pyinstaller
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Building version..."
python scripts/build_version.py animica_studio/_version.py

echo "==> Running PyInstaller..."
pyinstaller \
    --clean \
    --noconfirm \
    scripts/pyinstaller.spec

echo "==> Done. Artifact in dist/"
