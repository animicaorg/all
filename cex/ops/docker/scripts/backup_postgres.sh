#!/usr/bin/env bash
# =============================================================================
# PostgreSQL Backup Script
# =============================================================================
# Creates a compressed backup of the PostgreSQL database

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
DB_USER="${DB_USER:-cex}"
DB_NAME="${DB_NAME:-cex_exchange}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/cex-backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${1:-$BACKUP_DIR/postgres_backup_${TIMESTAMP}.sql.gz}"

# S3 configuration (optional)
S3_BUCKET="${S3_BUCKET:-}"
S3_ENABLED="${S3_ENABLED:-false}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${NC}ℹ $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }

# Create backup directory
mkdir -p "$(dirname "$BACKUP_FILE")"

log_info "Starting PostgreSQL backup..."
log_info "Database: $DB_NAME"
log_info "Backup file: $BACKUP_FILE"

# Run pg_dump and compress
if docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres \
    pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"; then
    
    log_success "Backup created successfully"
    
    # Get file size
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log_info "Backup size: $SIZE"
    
    # Calculate checksum
    CHECKSUM=$(sha256sum "$BACKUP_FILE" | cut -d' ' -f1)
    echo "$CHECKSUM" > "${BACKUP_FILE}.sha256"
    log_info "Checksum: $CHECKSUM"
    
    # Upload to S3 if configured
    if [ "$S3_ENABLED" = "true" ] && [ -n "$S3_BUCKET" ]; then
        log_info "Uploading to S3..."
        
        if command -v aws &> /dev/null; then
            if aws s3 cp "$BACKUP_FILE" "s3://${S3_BUCKET}/postgres/$(basename "$BACKUP_FILE")"; then
                aws s3 cp "${BACKUP_FILE}.sha256" "s3://${S3_BUCKET}/postgres/$(basename "$BACKUP_FILE").sha256"
                log_success "Backup uploaded to S3"
            else
                log_error "S3 upload failed"
            fi
        else
            log_error "AWS CLI not found, skipping S3 upload"
        fi
    fi
    
    log_success "Backup completed: $BACKUP_FILE"
    exit 0
else
    log_error "Backup failed"
    exit 1
fi
