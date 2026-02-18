#!/bin/bash
#
# ENA Upgrade System - Interactive Demo
#
# Demonstrates the complete ENA upgrade workflow with mock training jobs.
# Safe for development/testing - completes in < 2 minutes.
#
# Usage:
#   ./scripts/ena_upgrade_demo.sh [--keep-data] [--verbose]
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Configuration
KEEP_DATA=false
VERBOSE=false
MOCK_MODE=true  # Always use mock mode in demo
DEMO_DIR="${HOME}/.animica/ena_demo"
STATE_FILE="${DEMO_DIR}/upgrade_state.json"
REGISTRY_DIR="${DEMO_DIR}/registry"
WORK_DIR="${DEMO_DIR}/work"
LOG_FILE="${DEMO_DIR}/demo.log"

# Parse arguments
for arg in "$@"; do
    case $arg in
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --help)
            echo "Usage: $0 [--keep-data] [--verbose]"
            echo ""
            echo "Options:"
            echo "  --keep-data   Keep demo data after completion"
            echo "  --verbose     Show detailed output"
            echo "  --help        Show this help message"
            exit 0
            ;;
    esac
done

# Logging
log() {
    echo -e "${CYAN}[$(date +'%H:%M:%S')]${NC} $*" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${GREEN}✓${NC} $*" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}✗${NC} $*" | tee -a "$LOG_FILE"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $*" | tee -a "$LOG_FILE"
}

log_step() {
    echo -e "\n${BOLD}${BLUE}▶ $*${NC}\n" | tee -a "$LOG_FILE"
}

section() {
    echo "" | tee -a "$LOG_FILE"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD}  $*${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
}

# Cleanup on exit
cleanup() {
    if [ "$KEEP_DATA" = false ]; then
        log_step "Cleaning up demo data..."
        rm -rf "$DEMO_DIR"
        log_success "Demo data cleaned"
    else
        log_warning "Demo data preserved at: $DEMO_DIR"
    fi
}

trap cleanup EXIT

# Check prerequisites
check_prerequisites() {
    section "Checking Prerequisites"
    
    log "Checking Python..."
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 not found. Please install Python 3.8+"
        exit 1
    fi
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    log_success "Python ${PYTHON_VERSION} found"
    
    log "Checking animica CLI..."
    if ! python3 -m animica.cli.main --help &> /dev/null; then
        log_error "animica CLI not found. Please install: pip install -e ."
        exit 1
    fi
    log_success "animica CLI available"
    
    log "Checking ENA module..."
    if ! python3 -c "import ena" 2> /dev/null; then
        log_error "ENA module not found. Please ensure ena/ is in PYTHONPATH"
        exit 1
    fi
    log_success "ENA module available"
}

# Setup demo environment
setup_demo() {
    section "Setting Up Demo Environment"
    
    log "Creating demo directories..."
    mkdir -p "$DEMO_DIR" "$REGISTRY_DIR" "$WORK_DIR"
    log_success "Directories created"
    
    log "Initializing state file..."
    if [ -f "$STATE_FILE" ]; then
        log_warning "Previous state file found, backing up..."
        mv "$STATE_FILE" "${STATE_FILE}.backup.$(date +%s)"
    fi
    log_success "State initialized"
    
    log "Setting environment variables..."
    export ANIMICA_ENA_DIR="$DEMO_DIR"
    export ANIMICA_ENA_REGISTRY_DIR="$REGISTRY_DIR"
    export ANIMICA_ENA_WORK_DIR="$WORK_DIR"
    export ANIMICA_ENA_MOCK_MODE="true"
    log_success "Environment configured"
}

# Initialize telemetry (opt-in)
setup_telemetry() {
    section "Telemetry Configuration"
    
    log "Telemetry is ${BOLD}opt-in${NC} and helps improve ENA"
    log "For this demo, telemetry is ${YELLOW}disabled${NC}"
    
    # In a real scenario, prompt user:
    # read -p "Enable telemetry? [y/N] " -n 1 -r
    # if [[ $REPLY =~ ^[Yy]$ ]]; then
    #     python3 -m animica.cli.ena_upgrade telemetry enable
    # fi
    
    log_success "Telemetry configured"
}

# Demo: Create training plan
demo_create_plan() {
    section "Step 1: Create Training Plan"
    
    log "Creating training plan for ENA v2.0.0..."
    log "  Model: ena"
    log "  Version: 2.0.0"
    log "  Creator: demo_user_anim1test"
    log "  Datasets: demo_dataset_001,demo_dataset_002"
    
    cat > "${WORK_DIR}/demo_plan.json" <<EOF
{
  "plan_id": "demo_plan_$(date +%s)",
  "model_id": "ena",
  "target_version": "2.0.0",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "creator": "demo_user_anim1test",
  "description": "Demo upgrade to ENA v2.0.0",
  "jobs": [
    {
      "job_type": "train_sft",
      "job_id": "demo_train_001",
      "base_model": "qwen2.5-coder-1.5b",
      "dataset_hashes": ["demo_dataset_001", "demo_dataset_002"],
      "hyperparams": {
        "learning_rate": 1e-5,
        "epochs": 3,
        "batch_size": 4
      },
      "max_cost_anm": 5000000000
    },
    {
      "job_type": "eval",
      "job_id": "demo_eval_001",
      "base_model": "output:demo_train_001",
      "depends_on": ["demo_train_001"],
      "hyperparams": {
        "tasks": ["accuracy", "perplexity", "toxicity"]
      },
      "max_cost_anm": 500000000
    },
    {
      "job_type": "distill",
      "job_id": "demo_distill_001",
      "base_model": "output:demo_train_001",
      "depends_on": ["demo_train_001"],
      "hyperparams": {
        "target_size": "0.5x"
      },
      "max_cost_anm": 2000000000
    }
  ],
  "max_total_cost_anm": 10000000000,
  "dataset_commitments": ["demo_dataset_001", "demo_dataset_002"]
}
EOF
    
    log_success "Training plan created"
    
    if [ "$VERBOSE" = true ]; then
        cat "${WORK_DIR}/demo_plan.json"
    fi
    
    log "Plan includes:"
    log "  • SFT training (5 ANM budget)"
    log "  • Evaluation (0.5 ANM budget)"
    log "  • Distillation (2 ANM budget)"
    log "  Total budget: 10 ANM"
}

# Demo: Run upgrade workflow (mock mode)
demo_run_upgrade() {
    section "Step 2: Run Upgrade Workflow"
    
    log "Starting automated upgrade workflow..."
    log "Mode: ${YELLOW}MOCK${NC} (no actual training)"
    
    # Simulate workflow stages
    stages=(
        "PLANNING:Creating training plan:2"
        "ALLOCATING_BUDGET:Allocating AICF budget:3"
        "SUBMITTING_JOBS:Submitting jobs to AICF:4"
        "MONITORING:Monitoring job progress:8"
        "VERIFYING:Verifying results:3"
        "PUBLISHING:Publishing to registry:2"
        "CANARY:Rolling out canary:2"
        "COMPLETED:Upgrade complete:1"
    )
    
    for stage_info in "${stages[@]}"; do
        IFS=':' read -r state message duration <<< "$stage_info"
        
        log_step "$message..."
        
        # Simulate work
        for ((i=1; i<=duration; i++)); do
            echo -n "."
            sleep 0.5
        done
        echo ""
        
        # Create mock state file
        cat > "$STATE_FILE" <<EOF
{
  "upgrade_id": "demo_upgrade_$(date +%s)",
  "model_id": "ena",
  "target_version": "2.0.0",
  "current_state": "$state",
  "plan_id": "demo_plan_001",
  "budget_allocated": 10000000000,
  "budget_used": 7500000000,
  "job_statuses": {
    "demo_train_001": {
      "job_id": "demo_train_001",
      "state": "completed",
      "result_hash": "mock_hash_train"
    },
    "demo_eval_001": {
      "job_id": "demo_eval_001",
      "state": "completed",
      "result_hash": "mock_hash_eval"
    },
    "demo_distill_001": {
      "job_id": "demo_distill_001",
      "state": "completed",
      "result_hash": "mock_hash_distill"
    }
  },
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
        
        log_success "$message complete"
    done
    
    log_success "Workflow completed successfully!"
}

# Demo: Show registry operations
demo_registry() {
    section "Step 3: Registry Operations"
    
    # Create mock manifest
    log "Creating model manifest..."
    mkdir -p "${REGISTRY_DIR}/ena"
    
    cat > "${REGISTRY_DIR}/ena/manifest_2.0.0.json" <<EOF
{
  "modelId": "ena",
  "version": "2.0.0",
  "createdAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "creator": "demo_user_anim1test",
  "description": "ENA model v2.0.0 - Demo upgrade",
  "baseModel": "qwen2.5-coder-1.5b",
  "modelType": "causal_lm",
  "quantization": "none",
  "artifactHashes": {
    "modelWeights": "mock_hash_weights_abc123",
    "tokenizer": "mock_hash_tokenizer_def456",
    "config": "mock_hash_config_ghi789"
  },
  "evalMetrics": {
    "accuracy": 0.95,
    "perplexity": 2.3,
    "toxicityScore": 0.03,
    "regressionPassRate": 0.98
  },
  "trainingProvenance": {
    "planId": "demo_plan_001",
    "datasetHashes": ["demo_dataset_001", "demo_dataset_002"],
    "trainingJobId": "demo_train_001",
    "aicfProof": "mock_proof_training"
  }
}
EOF
    
    # Create pinned file
    echo "2.0.0" > "${REGISTRY_DIR}/ena/pinned.txt"
    
    log_success "Manifest created"
    
    # Show registry list
    log_step "Listing versions..."
    echo "Available versions for 'ena':"
    echo "  • 2.0.0 ${GREEN}[pinned]${NC}"
    log_success "1 version found"
    
    # Show manifest details
    log_step "Showing manifest for ena@2.0.0..."
    if [ "$VERBOSE" = true ]; then
        cat "${REGISTRY_DIR}/ena/manifest_2.0.0.json" | python3 -m json.tool
    else
        echo "Model: ena"
        echo "Version: 2.0.0"
        echo "Base Model: qwen2.5-coder-1.5b"
        echo "Metrics:"
        echo "  Accuracy: 95%"
        echo "  Perplexity: 2.3"
        echo "  Toxicity: 3%"
    fi
    log_success "Manifest loaded"
    
    # Pin version
    log_step "Pinning version 2.0.0..."
    log_success "Version 2.0.0 is now active"
}

# Demo: Check upgrade status
demo_status() {
    section "Step 4: Upgrade Status"
    
    log "Current upgrade status:"
    echo ""
    
    cat <<EOF
  Upgrade ID:       demo_upgrade_$(date +%s)
  Model:            ena
  Target Version:   2.0.0
  Current State:    ${GREEN}COMPLETED${NC}
  
  Budget:
    Allocated:      10.0 ANM
    Used:           7.5 ANM
    Remaining:      2.5 ANM
  
  Jobs:
    ✓ demo_train_001     [completed]
    ✓ demo_eval_001      [completed]  
    ✓ demo_distill_001   [completed]
  
  Artifacts:
    ✓ Model weights     (hash: mock_hash_weights_abc123)
    ✓ Tokenizer         (hash: mock_hash_tokenizer_def456)
    ✓ Configuration     (hash: mock_hash_config_ghi789)
  
  Quality Gates:
    ✓ Accuracy ≥ 90%            (95%)
    ✓ Perplexity ≤ 3.0          (2.3)
    ✓ Toxicity ≤ 10%            (3%)
    ✓ Regression pass ≥ 95%     (98%)
EOF
    
    echo ""
    log_success "All checks passed"
}

# Demo: Telemetry data curation
demo_telemetry() {
    section "Step 5: Telemetry Data (Dry Run)"
    
    log "Showing what telemetry would be collected..."
    log "Note: Telemetry is ${BOLD}opt-in${NC} and ${BOLD}privacy-preserving${NC}"
    
    cat <<EOF

Collected Data (anonymized):
  • Training job durations
  • Success/failure rates
  • Error categories (no stack traces)
  • Model metrics (accuracy, perplexity)
  • Resource usage estimates

NOT collected:
  ✗ Private keys or addresses
  ✗ Dataset contents
  ✗ Model weights
  ✗ User prompts
  ✗ Personal information

This data helps improve:
  • Job scheduling efficiency
  • Cost estimation accuracy
  • Error handling
  • Documentation
EOF
    
    echo ""
    log_success "Telemetry summary shown"
}

# Demo: Safety features
demo_safety() {
    section "Step 6: Safety Features"
    
    log_step "Testing failure scenario..."
    
    # Simulate a failed job
    cat > "${STATE_FILE}.failed" <<EOF
{
  "upgrade_id": "demo_upgrade_failed",
  "model_id": "ena",
  "target_version": "2.1.0",
  "current_state": "FAILED",
  "errors": [
    "Accuracy gate failed: 0.85 < 0.90 (minimum)",
    "Safety check failed: Model did not meet quality thresholds"
  ],
  "previous_version": "2.0.0",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
    
    log_error "Simulated failure: Accuracy below threshold"
    echo ""
    
    log_step "Testing rollback..."
    sleep 1
    log "Rolling back to version 2.0.0..."
    sleep 1
    echo "2.0.0" > "${REGISTRY_DIR}/ena/pinned.txt"
    log_success "Rollback complete - active version: 2.0.0"
    
    echo ""
    log_step "Testing resume capability..."
    
    cat > "${STATE_FILE}.resume" <<EOF
{
  "upgrade_id": "demo_upgrade_resume",
  "model_id": "ena",
  "target_version": "2.2.0",
  "current_state": "MONITORING",
  "job_statuses": {
    "job_001": {"state": "completed"},
    "job_002": {"state": "running"},
    "job_003": {"state": "pending"}
  },
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
    
    log "Workflow interrupted at MONITORING state"
    sleep 1
    log "Resuming from checkpoint..."
    sleep 1
    log_success "Resume capability verified"
}

# Demo: Verification summary
demo_verification() {
    section "Step 7: Verification Summary"
    
    log "Verifying all artifacts and state..."
    echo ""
    
    checks=(
        "State file exists:${STATE_FILE}:true"
        "Registry directory:${REGISTRY_DIR}:true"
        "Manifest file:${REGISTRY_DIR}/ena/manifest_2.0.0.json:true"
        "Pinned version:${REGISTRY_DIR}/ena/pinned.txt:true"
        "Work directory:${WORK_DIR}:true"
    )
    
    for check in "${checks[@]}"; do
        IFS=':' read -r name path expected <<< "$check"
        
        if [ -e "$path" ]; then
            log_success "$name"
        else
            log_error "$name (not found)"
        fi
    done
    
    echo ""
    log_success "All verifications passed"
}

# Show demo summary
show_summary() {
    section "Demo Complete!"
    
    cat <<EOF
${GREEN}✓${NC} Training plan created
${GREEN}✓${NC} Workflow executed (MOCK mode)
${GREEN}✓${NC} Model published to registry
${GREEN}✓${NC} Version pinned and active
${GREEN}✓${NC} Safety features tested
${GREEN}✓${NC} State management verified

${BOLD}Next Steps:${NC}

1. Review the implementation:
   ${CYAN}cat docs/ENA_UPGRADE.md${NC}

2. Explore the registry:
   ${CYAN}ls -la ${REGISTRY_DIR}/${NC}

3. Check state file:
   ${CYAN}cat ${STATE_FILE} | python3 -m json.tool${NC}

4. Run real upgrade (requires AICF setup):
   ${CYAN}animica ena upgrade auto --help${NC}

5. Read architecture docs:
   ${CYAN}cat docs/ENA_UPGRADE_ARCHITECTURE.md${NC}

${BOLD}Demo Data Location:${NC}
  $DEMO_DIR

${BOLD}Logs:${NC}
  $LOG_FILE

EOF
    
    if [ "$KEEP_DATA" = false ]; then
        echo "Demo data will be cleaned up on exit (use --keep-data to preserve)"
    else
        echo "${YELLOW}Demo data preserved${NC} (delete manually when done)"
    fi
    
    echo ""
}

# Main execution
main() {
    echo ""
    echo -e "${BOLD}${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║                                                           ║"
    echo "║         ENA UPGRADE SYSTEM - INTERACTIVE DEMO             ║"
    echo "║                                                           ║"
    echo "║  Demonstrates the complete model upgrade workflow         ║"
    echo "║  Mode: MOCK (no actual training)                          ║"
    echo "║  Duration: ~2 minutes                                     ║"
    echo "║                                                           ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    
    # Initialize log
    mkdir -p "$DEMO_DIR"
    echo "ENA Upgrade Demo - $(date)" > "$LOG_FILE"
    
    # Run demo steps
    check_prerequisites
    setup_demo
    setup_telemetry
    demo_create_plan
    demo_run_upgrade
    demo_registry
    demo_status
    demo_telemetry
    demo_safety
    demo_verification
    show_summary
    
    log_success "Demo completed successfully!"
}

# Run
main "$@"
