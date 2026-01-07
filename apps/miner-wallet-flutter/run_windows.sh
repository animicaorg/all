#!/usr/bin/env bash
# Launch Animica Miner-Wallet (Flutter) on Windows
#
# Requirements:
#   - Flutter SDK ≥ 3.24
#   - Windows 10 or higher
#   - Visual Studio with Desktop development with C++
#
# Usage (on Windows with Git Bash or WSL):
#   ./run_windows.sh

set -euo pipefail

log()  { printf "\033[1;34m[run-windows]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"

# ---- Platform checks ----
OS="$(uname -s)"
case "$OS" in
    MINGW*|MSYS*|CYGWIN*)
        log "Running on Windows"
        ;;
    *)
        die "This script must be run on Windows (via Git Bash, WSL, or similar)"
        ;;
esac

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

# ---- Launch on Windows ----
log "Launching Animica Miner-Wallet on Windows..."
cd "$APP_DIR"
flutter run -d windows

log "✅ App launched successfully!"
