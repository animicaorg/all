#!/usr/bin/env bash
set -euo pipefail

# Basic setup for the dev container: install Rust, Node, pnpm, and essential tools
apt-get update
apt-get install -y --no-install-recommends curl ca-certificates git build-essential sudo python3 python3-pip

# Install rustup (non-interactive)
if ! command -v rustc >/dev/null 2>&1; then
  curl https://sh.rustup.rs -sSf | sh -s -- -y
  export PATH="$HOME/.cargo/bin:$PATH"
fi

# Install Node.js (via NodeSource)
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

# Install pnpm
if ! command -v pnpm >/dev/null 2>&1; then
  npm install -g pnpm
fi

# Install Flutter SDK (brief bootstrap, heavy; user can expand)
if [ ! -d "/usr/local/flutter" ]; then
  echo "Skipping heavy Flutter SDK install in default container; see CONTRIBUTING.md for devcontainer Flutter setup."
fi

# Install pre-commit for Python hooks
python3 -m pip install --upgrade pip
python3 -m pip install pre-commit

# Print versions
node --version || true
pnpm --version || true
rustc --version || true
python3 --version || true

exit 0
