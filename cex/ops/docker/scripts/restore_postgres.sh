#!/usr/bin/env bash
# =============================================================================
# PostgreSQL Restore Script
# =============================================================================
# Restores PostgreSQL database from a backup file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${NC}ℹ $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }

# Check arguments
if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup_file>"
    echo ""
    echo "Example:"
    echo "  $0 /tmp/cex-backups/postgres_backup_20240101_120000.sql.gz"
    exit 1
fi

BACKUP_FILE="$1"
DB_USER="${DB_USER:-cex}"
DB_NAME="${DB_NAME:-cex_exchange}"

# Validate backup file
if [ ! -f "$BACKUP_FILE" ]; then
    log_error "Backup file not found: $BACKUP_FILE"
    exit 1
fi

log_warning "═══════════════════════════════════════════════════════════════"
log_warning "  DATABASE RESTORE - DESTRUCTIVE OPERATION"
log_warning "═══════════════════════════════════════════════════════════════"
log_warning "This will DROP and recreate the database: $DB_NAME"
log_warning "Backup file: $BACKUP_FILE"
log_warning ""
read -p "Are you sure you want to continue? (type 'yes' to confirm): " -r
echo

if [ "$REPLY" != "yes" ]; then
    log_info "Restore cancelled"
    exit 0
fi

# Verify checksum if available
if [ -f "${BACKUP_FILE}.sha256" ]; then
    log_info "Verifying backup checksum..."
    EXPECTED_CHECKSUM=$(cat "${BACKUP_FILE}.sha256")
    ACTUAL_CHECKSUM=$(sha256sum "$BACKUP_FILE" | cut -d' ' -f1)
    
    if [ "$EXPECTED_CHECKSUM" = "$ACTUAL_CHECKSUM" ]; then
        log_success "Checksum verified"
    else
        log_error "Checksum mismatch! Backup file may be corrupted."
        exit 1
    fi
fi

# Create a backup of current database before restore
log_info "Creating backup of current database..."
CURRENT_BACKUP="/tmp/pre-restore-$(date +%Y%m%d_%H%M%S).sql.gz"
"$SCRIPT_DIR/backup_postgres.sh" "$CURRENT_BACKUP" || true

# Drop and recreate database
log_info "Dropping existing database..."
docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres \
    psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"

log_info "Creating fresh database..."
docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres \
    psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME;"

# Restore from backup
log_info "Restoring database from backup..."
if gunzip < "$BACKUP_FILE" | docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres \
    psql -U "$DB_USER" -d "$DB_NAME"; then
    
    log_success "Database restored successfully"
    
    # Verify restore
    log_info "Verifying restored database..."
    TABLE_COUNT=$(docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres \
        psql -U "$DB_USER" -d "$DB_NAME" -t -c \
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';")
    
    log_info "Tables in restored database: $(echo "$TABLE_COUNT" | tr -d ' ')"
    log_success "Restore completed successfully!"
    log_info "Pre-restore backup saved to: $CURRENT_BACKUP"
else
    log_error "Restore failed!"
    log_warning "Attempting to restore from pre-restore backup..."
    
    if gunzip < "$CURRENT_BACKUP" | docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres \
        psql -U "$DB_USER" -d "$DB_NAME"; then
        log_success "Rolled back to pre-restore state"
    else
        log_error "Rollback failed! Database may be in an inconsistent state."
    fi
    
    exit 1
fi
