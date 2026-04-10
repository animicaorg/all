#!/bin/bash
# package-mac.sh - ergonomic alias for release-mac.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/release-mac.sh" "$@"
