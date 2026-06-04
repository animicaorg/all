#!/usr/bin/env bash
# Animica Pool CLI installer.
#   curl -fsSL https://pool.animica.org/install-cli.sh | bash
set -euo pipefail
BASE="${ANIMICA_BASE:-https://pool.animica.org}"
DEST="${DEST:-/usr/local/bin/animica-pool}"

command -v node >/dev/null 2>&1 || { echo "Node.js >= 20 is required (https://nodejs.org)"; exit 1; }
echo "▶ Downloading CLI from $BASE/animica-pool.mjs …"
curl -fsSL "$BASE/animica-pool.mjs" -o "$DEST"
chmod +x "$DEST"
echo "✓ Installed: $DEST"
echo "  Try: animica-pool help"
