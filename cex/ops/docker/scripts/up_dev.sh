#!/usr/bin/env bash
# =============================================================================
# Development Environment Startup Script
# =============================================================================
# Starts the complete CEX development stack with all services
# Usage: ./up_dev.sh [--with-monitoring]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/../.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Log functions
log_info() {
    echo -e "${BLUE}ℹ ${NC}$1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! command -v docker compose &> /dev/null; then
        log_error "Docker Compose V2 is not installed. Please install Docker Compose."
        exit 1
    fi
    
    log_success "Prerequisites check passed"
}

# Setup environment file
setup_env() {
    log_info "Setting up environment configuration..."
    
    ENV_FILE="$OPS_DIR/docker/env/.env"
    ENV_EXAMPLE="$OPS_DIR/docker/env/dev.env.example"
    
    if [ ! -f "$ENV_FILE" ]; then
        log_warning ".env file not found, creating from example..."
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        log_success "Created $ENV_FILE"
        log_warning "Please review and update the .env file with your configuration"
    else
        log_success "Environment file already exists"
    fi
}

# Start infrastructure services
start_infrastructure() {
    log_info "Starting infrastructure services (postgres, redis, nats)..."
    
    cd "$OPS_DIR/docker"
    docker compose -f docker-compose.dev.yml up -d postgres redis nats minio mailhog
    
    log_success "Infrastructure services started"
}

# Wait for services to be healthy
wait_for_services() {
    log_info "Waiting for services to be healthy..."
    
    "$SCRIPT_DIR/wait_for.sh" postgres 5432 60
    "$SCRIPT_DIR/wait_for.sh" redis 6379 30
    "$SCRIPT_DIR/wait_for.sh" nats 4222 30
    
    log_success "All infrastructure services are healthy"
}

# Run database migrations
run_migrations() {
    log_info "Running database migrations..."
    
    cd "$OPS_DIR/docker"
    
    # Check if migration service is configured
    if docker compose -f docker-compose.dev.yml config --services | grep -q migrate; then
        docker compose -f docker-compose.dev.yml run --rm migrate
        log_success "Database migrations completed"
    else
        log_warning "No migration service configured, skipping..."
    fi
}

# Seed initial data
seed_database() {
    log_info "Seeding initial database data..."
    
    cd "$OPS_DIR/docker"
    
    if [ -f "$SCRIPT_DIR/seed_admin.sh" ]; then
        "$SCRIPT_DIR/seed_admin.sh"
    else
        log_warning "Seed script not found, skipping..."
    fi
}

# Start application services
start_applications() {
    log_info "Starting application services..."
    
    cd "$OPS_DIR/docker"
    docker compose -f docker-compose.dev.yml up -d
    
    log_success "Application services started"
}

# Start monitoring stack (optional)
start_monitoring() {
    if [ "${WITH_MONITORING:-false}" = "true" ]; then
        log_info "Starting monitoring stack..."
        
        cd "$OPS_DIR/docker"
        
        # Create monitoring network if it doesn't exist
        docker network create cex-internal 2>/dev/null || true
        
        docker compose -f docker-compose.monitoring.yml up -d
        
        log_success "Monitoring stack started"
        log_info "Grafana: http://localhost:3100 (admin/admin)"
        log_info "Prometheus: http://localhost:9090"
    fi
}

# Run smoke tests
run_smoke_tests() {
    log_info "Running smoke tests..."
    
    # Wait a bit for services to stabilize
    sleep 10
    
    if [ -f "$SCRIPT_DIR/smoke_test.sh" ]; then
        "$SCRIPT_DIR/smoke_test.sh"
    else
        log_warning "Smoke test script not found, skipping..."
    fi
}

# Display service status and URLs
display_status() {
    log_success "CEX Development Environment is ready! 🚀"
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Service URLs"
    echo "═══════════════════════════════════════════════════════════════"
    echo "  API Gateway:       http://localhost:3000"
    echo "  Admin Service:     http://localhost:3001"
    echo "  BitGo Ingestor:    http://localhost:3002"
    echo "  MailHog UI:        http://localhost:8025"
    echo "  MinIO Console:     http://localhost:9001"
    echo "  PostgreSQL:        localhost:5432"
    echo "  Redis:             localhost:6379"
    echo "  NATS:              localhost:4222"
    echo "  NATS Monitor:      http://localhost:8222"
    
    if [ "${WITH_MONITORING:-false}" = "true" ]; then
        echo ""
        echo "  Monitoring:"
        echo "  Grafana:           http://localhost:3100 (admin/admin)"
        echo "  Prometheus:        http://localhost:9090"
    fi
    
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "Useful commands:"
    echo "  View logs:         ./scripts/logs.sh [service]"
    echo "  Stop services:     ./scripts/down_dev.sh"
    echo "  Run migrations:    ./scripts/migrate.sh"
    echo "  Backup database:   ./scripts/backup_postgres.sh"
    echo ""
}

# Main execution
main() {
    echo "═══════════════════════════════════════════════════════════════"
    echo "  CEX Development Environment Setup"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --with-monitoring)
                WITH_MONITORING=true
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Usage: $0 [--with-monitoring]"
                exit 1
                ;;
        esac
    done
    
    check_prerequisites
    setup_env
    start_infrastructure
    wait_for_services
    run_migrations
    seed_database
    start_applications
    start_monitoring
    run_smoke_tests
    display_status
    
    log_success "Setup complete!"
}

# Run main function
main "$@"
