# =============================================================================
# Animica Contract Python Workspace — Makefile
# =============================================================================
# This Makefile provides a smooth developer loop for a multi-contract workspace.
# Targets are safe-by-default and will:
#  - create a local virtual environment (.venv)
#  - install editable project + dev extras
#  - run linters (ruff), type-checker (mypy), and tests (pytest)
#  - (optionally) compile and deploy a single contract manifest via CLI helpers
#
# Conventions
# -----------
# * Contracts live under ./contracts/<name>/{contract.py,manifest.json}
# * Environment (.env) may define RPC_URL, CHAIN_ID, FAUCET, etc. (exported per recipe)
# * Python 3.10+ recommended
#
# Quickstart
# ----------
#   make help
#   make install
#   make fmt lint typecheck test
#   make build-one MANIFEST=contracts/counter/manifest.json
#   make deploy-one MANIFEST=contracts/counter/manifest.json
# =============================================================================

# ---- Configuration -----------------------------------------------------------

# Python/venv
PY              ?= python3
VENV_DIR        ?= .venv
PIP             := $(VENV_DIR)/bin/pip
PYTHON          := $(VENV_DIR)/bin/python
RUFF            := $(VENV_DIR)/bin/ruff
MYPY            := $(VENV_DIR)/bin/mypy
PYTEST          := $(VENV_DIR)/bin/pytest

# Workspace structure
CONTRACTS_DIR   ?= contracts
BUILD_DIR       ?= build
ARTIFACTS_DIR   ?= $(BUILD_DIR)/artifacts

# Tool options (override via env: e.g., make test TEST_ARGS="-k token")
TEST_ARGS       ?=
RUFF_ARGS       ?=
MYPY_ARGS       ?=
PYTEST_ARGS     ?= -q

# A single manifest.json to act upon for *-one targets (must be supplied)
MANIFEST        ?=

# Export .env values to each recipe if present.
define LOAD_ENV
set -a; [ -f .env ] && . .env; set +a;
endef

# Colors for pretty output
COLOR_RESET := \033[0m
COLOR_BLUE  := \033[1;34m
COLOR_GREEN := \033[1;32m
COLOR_YELL  := \033[1;33m

# ---- Meta targets ------------------------------------------------------------

.PHONY: help
help: ## Show this help text
	@echo "$(COLOR_BLUE)Animica Contract Workspace — Targets$(COLOR_RESET)"
	@awk 'BEGIN {FS":.*##"; printf ""} /^[A-Za-z0-9_\/-]+:.*##/ { printf "  \033[1;32m%-28s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: env
env: ## Print resolved tool paths and environment hints
	@echo "$(COLOR_YELL)Python:$(COLOR_RESET)      $(PY)"
	@echo "$(COLOR_YELL)Venv dir:$(COLOR_RESET)    $(VENV_DIR)"
	@echo "$(COLOR_YELL)Pip:$(COLOR_RESET)         $(PIP)"
	@echo "$(COLOR_YELL)Contracts:$(COLOR_RESET)   $(CONTRACTS_DIR)"
	@echo "$(COLOR_YELL)Build dir:$(COLOR_RESET)   $(BUILD_DIR)"
	@echo "$(COLOR_YELL)Artifacts:$(COLOR_RESET)   $(ARTIFACTS_DIR)"
	@echo "$(COLOR_YELL)Manifest:$(COLOR_RESET)    $(MANIFEST)"
	@echo "$(COLOR_YELL).env present?$(COLOR_RESET) $$( [ -f .env ] && echo yes || echo no )"

# ---- Setup -------------------------------------------------------------------

.PHONY: venv
venv: ## Create local virtualenv (.venv) with the selected Python
	@echo "$(COLOR_BLUE)[venv]$(COLOR_RESET) creating $(VENV_DIR)"
	@$(PY) -m venv $(VENV_DIR)
	@$(PIP) install -U pip wheel setuptools

.PHONY: install
install: venv ## Install project (editable) + dev extras
	@echo "$(COLOR_BLUE)[install]$(COLOR_RESET) editable package + dev extras"
	@$(PIP) install -e '.[dev]'
	@echo "$(COLOR_GREEN)✓ Installed$(COLOR_RESET)"

# ---- Code Quality ------------------------------------------------------------

.PHONY: fmt
fmt: ## Auto-format with ruff
	@$(RUFF) format . $(RUFF_ARGS)

.PHONY: lint
lint: ## Lint with ruff (no changes)
	@$(RUFF) check . $(RUFF_ARGS)

.PHONY: typecheck
typecheck: ## Static type-check with mypy
	@$(MYPY) . $(MYPY_ARGS)

# ---- Tests -------------------------------------------------------------------

.PHONY: test
test: ## Run unit tests (pytest)
	@$(PYTEST) $(PYTEST_ARGS) $(TEST_ARGS)

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	@$(PYTEST) --cov=. --cov-report=term-missing $(TEST_ARGS)

# ---- Build / Deploy (single-manifest helpers) --------------------------------
# These helpers act on a single MANIFEST path. They intentionally do not guess
# your layout beyond that. Pass MANIFEST=<path> explicitly.

# Compile a single contract package using vm_py CLI (manifest-driven).
.PHONY: build-one
build-one: guard-manifest dirs ## Compile a single manifest → IR/artifact(s)
	@$(LOAD_ENV) \
	$(PYTHON) -m vm_py.cli.compile --manifest "$(MANIFEST)" --out "$(ARTIFACTS_DIR)"

# Simulate a read-only call against a single manifest (no state writes).
.PHONY: simulate-one
simulate-one: guard-manifest ## Simulate a contract call (requires vm_py CLI)
	@$(LOAD_ENV) \
	$(PYTHON) -m vm_py.cli.run --manifest "$(MANIFEST)" --call get

# Deploy a single manifest via the Python SDK CLI (studio-services-less path).
.PHONY: deploy-one
deploy-one: guard-manifest ## Deploy manifest via SDK (requires RPC_URL/CHAIN_ID in .env)
	@$(LOAD_ENV) \
	$(PYTHON) -m omni_sdk.cli.deploy --manifest "$(MANIFEST)"

# ---- Utilities ---------------------------------------------------------------

.PHONY: dirs
dirs: ## Create build/artifacts directories
	@mkdir -p "$(BUILD_DIR)" "$(ARTIFACTS_DIR)"

.PHONY: clean
clean: ## Remove caches and build artifacts
	@echo "$(COLOR_BLUE)[clean]$(COLOR_RESET) removing caches & artifacts"
	@rm -rf \
		$(BUILD_DIR) \
		.dist dist \
		.pytest_cache .mypy_cache .ruff_cache \
		coverage.xml htmlcov \
		$(ARTIFACTS_DIR)

.PHONY: deep-clean
deep-clean: clean ## Also remove the virtualenv
	@echo "$(COLOR_BLUE)[clean]$(COLOR_RESET) removing venv"
	@rm -rf "$(VENV_DIR)"

.PHONY: check
check: fmt lint typecheck test ## Run formatter, linters, type-checker, and tests

# ---- Safety guards -----------------------------------------------------------

.PHONY: guard-manifest
guard-manifest:
	@if [ -z "$(MANIFEST)" ]; then \
		echo "$(COLOR_YELL)MANIFEST is required. Example:$(COLOR_RESET) make $@ MANIFEST=$(CONTRACTS_DIR)/counter/manifest.json"; \
		exit 2; \
	fi
	@if [ ! -f "$(MANIFEST)" ]; then \
		echo "$(COLOR_YELL)Manifest not found:$(COLOR_RESET) $(MANIFEST)"; \
		exit 3; \
	fi

# End of Makefile
