#!/usr/bin/env bash
# =============================================================================
# Seed Admin User Script
# =============================================================================
# Creates an initial SUPERADMIN user for the exchange

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Generate secure random password
ADMIN_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@cex.local}"

echo "═══════════════════════════════════════════════════════════════"
echo "  Creating SUPERADMIN User"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Email: $ADMIN_EMAIL"
echo "Password: $ADMIN_PASSWORD"
echo ""
echo "⚠️  IMPORTANT: Save these credentials securely!"
echo ""

# Run seed container (adjust based on your actual seeding mechanism)
cd "$OPS_DIR/docker"

if docker compose -f docker-compose.dev.yml run --rm \
    -e ADMIN_EMAIL="$ADMIN_EMAIL" \
    -e ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    seed; then
    
    echo "✓ Admin user created successfully"
    
    # Save credentials to a file for reference
    CREDS_FILE="/tmp/cex-admin-credentials.txt"
    cat > "$CREDS_FILE" <<EOF
CEX Admin Credentials
=====================
Email:    $ADMIN_EMAIL
Password: $ADMIN_PASSWORD
Created:  $(date)

Delete this file after saving credentials securely!
EOF
    
    echo ""
    echo "Credentials saved to: $CREDS_FILE"
else
    echo "✗ Failed to create admin user"
    exit 1
fi
