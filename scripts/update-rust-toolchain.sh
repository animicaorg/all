#!/usr/bin/env bash
# Helper script to update Rust toolchain to the latest stable version.
# Ensures rustup is installed and sets the default toolchain to stable.

set -euo pipefail

BLUE='\033[34m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
RESET='\033[0m'

log() { echo -e "${BLUE}[update-rust]${RESET} $*"; }
success() { echo -e "${GREEN}[success]${RESET} $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET} $*"; }
fail() { echo -e "${RED}[error]${RESET} $*"; exit 1; }

# Check if rustup is installed
if ! command -v rustup >/dev/null 2>&1; then
  warn "rustup not found. Installing rustup..."
  
  # Install rustup using the official install script
  if command -v curl >/dev/null 2>&1; then
    log "Downloading rustup installer..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
    
    # Source the cargo env to make rustup available
    if [ -f "$HOME/.cargo/env" ]; then
      # shellcheck disable=SC1091
      source "$HOME/.cargo/env"
    fi
  else
    fail "curl is required to install rustup. Please install curl and try again."
  fi
else
  log "rustup found: $(rustup --version 2>&1 | head -1)"
fi

# Verify rustup is now available
if ! command -v rustup >/dev/null 2>&1; then
  fail "rustup installation failed or is not in PATH. Please install manually from https://rustup.rs/"
fi

# Update rustup itself
log "Updating rustup..."
rustup self update || warn "Could not update rustup (may require sudo)"

# Update all installed toolchains
log "Updating all installed Rust toolchains..."
rustup update

# Set the default toolchain to latest stable
log "Setting default toolchain to stable..."
rustup default stable

# Show the current version
log "Current Rust version:"
rustc --version
cargo --version

success "Rust toolchain updated successfully!"
success "Default toolchain is now set to: $(rustup show active-toolchain)"
