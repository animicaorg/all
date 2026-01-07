#!/usr/bin/env bash
# Launch Animica Miner-Wallet (Flutter) on Web
#
# Requirements:
#   - Flutter SDK ≥ 3.24
#   - Chrome or another web browser
#
# Usage:
#   ./run_web.sh

set -euo pipefail

log()  { printf "\033[1;34m[run-web]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"

# ---- Check Flutter ----
if ! command -v flutter >/dev/null 2>&1; then
    die "Flutter SDK not found. Install from: https://docs.flutter.dev/get-started/install"
fi

FLUTTER_VERSION=$(flutter --version | head -n 1 | awk '{print $2}')
log "Using Flutter $FLUTTER_VERSION"

# ---- Check if dependencies are installed ----
if [[ ! -d "$APP_DIR/.dart_tool" ]] || [[ ! -f "$APP_DIR/pubspec.lock" ]]; then
    log "Dependencies not found. Installing..."
    cd "$APP_DIR"
    flutter pub get
fi

# ---- Launch on web ----
log "Launching Animica Miner-Wallet on Web (Chrome)..."
cd "$APP_DIR"
flutter run -d chrome

log "✅ App launched successfully!"
