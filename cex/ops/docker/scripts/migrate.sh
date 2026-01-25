#!/usr/bin/env bash
# =============================================================================
# Database Migration Script
# =============================================================================
# Runs database migrations safely with pre and post checks

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/../.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}ℹ${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }

# Check if postgres is running
check_postgres() {
    log_info "Checking PostgreSQL connection..."
    
    if ! docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres pg_isready -U cex &>/dev/null; then
        log_error "PostgreSQL is not ready. Please start the services first."
        exit 1
    fi
    
    log_success "PostgreSQL is ready"
}

# Backup database before migration
backup_before_migration() {
    log_info "Creating pre-migration backup..."
    
    BACKUP_FILE="/tmp/pre-migration-$(date +%Y%m%d_%H%M%S).sql.gz"
    
    if "$SCRIPT_DIR/backup_postgres.sh" "$BACKUP_FILE"; then
        log_success "Pre-migration backup created: $BACKUP_FILE"
    else
        log_warning "Backup failed, but continuing..."
    fi
}

# Get current migration version
get_migration_version() {
    log_info "Checking current migration version..."
    
    # This would query your migration table
    # Adjust based on your ORM/migration tool
    docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres \
        psql -U cex -d cex_exchange -t -c \
        "SELECT version FROM migrations ORDER BY version DESC LIMIT 1;" 2>/dev/null || echo "none"
}

# Run migrations
run_migrations() {
    log_info "Running database migrations..."
    
    cd "$OPS_DIR/docker"
    
    # Run migration container
    if docker compose -f docker-compose.dev.yml run --rm migrate; then
        log_success "Migrations completed successfully"
        return 0
    else
        log_error "Migration failed!"
        return 1
    fi
}

# Verify migrations
verify_migrations() {
    log_info "Verifying database state..."
    
    # Check if we can connect and query
    if docker compose -f "$OPS_DIR/docker/docker-compose.dev.yml" exec -T postgres \
        psql -U cex -d cex_exchange -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';" &>/dev/null; then
        log_success "Database state is valid"
        return 0
    else
        log_error "Database state verification failed"
        return 1
    fi
}

# Main execution
main() {
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Database Migration"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    
    check_postgres
    
    CURRENT_VERSION=$(get_migration_version)
    log_info "Current migration version: $CURRENT_VERSION"
    
    backup_before_migration
    
    if run_migrations; then
        NEW_VERSION=$(get_migration_version)
        log_info "New migration version: $NEW_VERSION"
        
        if verify_migrations; then
            log_success "Migration completed successfully!"
        else
            log_error "Migration verification failed. Please check the database state."
            exit 1
        fi
    else
        log_error "Migration failed. Database may be in an inconsistent state."
        log_warning "Consider restoring from backup if needed."
        exit 1
    fi
}

main "$@"
