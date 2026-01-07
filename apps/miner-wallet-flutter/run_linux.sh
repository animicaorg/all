#!/usr/bin/env bash
# Launch Animica Miner-Wallet (Flutter) on Linux
#
# Requirements:
#   - Flutter SDK ≥ 3.24
#   - Linux (x86_64 or aarch64)
#   - GTK3 development packages
#
# Usage:
#   ./run_linux.sh

set -euo pipefail

log()  { printf "\033[1;34m[run-linux]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- Paths ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"

# ---- Platform checks ----
if [[ "$(uname -s)" != "Linux" ]]; then
    die "This script must be run on Linux"
fi

# ---- Check Flutter ----
if ! command -v flutter >/dev/null 2>&1; then
    die "Flutter SDK not found. Install from: https://docs.flutter.dev/get-started/install"
fi

FLUTTER_VERSION=$(flutter --version | head -n 1 | awk '{print $2}')
log "Using Flutter $FLUTTER_VERSION"

# ---- Check GTK dependencies ----
log "Checking GTK3 dependencies..."
MISSING_DEPS=()
if command -v apt-get >/dev/null 2>&1; then
    # Debian/Ubuntu
    for pkg in libgtk-3-dev libglib2.0-dev libblkid-dev liblzma-dev; do
        if ! dpkg -l | grep -q "^ii  $pkg"; then
            MISSING_DEPS+=("$pkg")
        fi
    done
    
    if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
        warn "Missing dependencies: ${MISSING_DEPS[*]}"
        warn "Install with: sudo apt-get install ${MISSING_DEPS[*]}"
        warn "Attempting to continue anyway..."
    fi
elif command -v yum >/dev/null 2>&1; then
    # RHEL/Fedora
    log "Note: Ensure GTK3 development packages are installed"
fi

# ---- Check if dependencies are installed ----
if [[ ! -d "$APP_DIR/.dart_tool" ]] || [[ ! -f "$APP_DIR/pubspec.lock" ]]; then
    log "Dependencies not found. Installing..."
    cd "$APP_DIR"
    flutter pub get
fi

# ---- Launch on Linux ----
log "Launching Animica Miner-Wallet on Linux..."
cd "$APP_DIR"
flutter run -d linux

log "✅ App launched successfully!"
