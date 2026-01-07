#!/usr/bin/env bash
# Launch Animica Miner-Wallet (Flutter) on macOS
#
# Requirements:
#   - Flutter SDK ≥ 3.24
#   - macOS 10.15 or higher
#   - Xcode and CocoaPods
#
# Usage:
#   ./run_macos.sh

set -euo pipefail

log()  { printf "\033[1;34m[run-macos]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"

# ---- Platform checks ----
if [[ "$(uname -s)" != "Darwin" ]]; then
    die "This script must be run on macOS"
fi

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

# ---- Check if pods need to be installed ----
if [[ -d "$APP_DIR/macos" ]] && [[ ! -d "$APP_DIR/macos/Pods" ]]; then
    log "CocoaPods not installed. Installing pods..."
    cd "$APP_DIR/macos"
    pod install
    cd "$APP_DIR"
fi

# ---- Launch on macOS ----
log "Launching Animica Miner-Wallet on macOS..."
cd "$APP_DIR"
flutter run -d macos

log "✅ App launched successfully!"
