# ==============================================================================
# Oracle DA Poster — Makefile
# ------------------------------------------------------------------------------
# Quick dev commands for creating a venv, installing deps, and running the poster
# loop that:
#   1) fetches or reads source data (file/command/custom fetcher),
#   2) posts it to DA (Data Availability) to obtain a commitment,
#   3) calls the on-chain oracle contract to update the latest value.
#
# The poster implementation is expected to be exposed as a Python module:
#   python -m oracle_da_poster            # one-shot (see POST_MODE / CLI flags)
#   python -m oracle_da_poster --loop     # continuous watch/loop
#
# Configure environment via .env (copy .env.example first).
# ==============================================================================

# ---- Settings -----------------------------------------------------------------
PYTHON      ?= python3
VENV_DIR    ?= .venv
PIP         := $(VENV_DIR)/bin/pip
PY          := $(VENV_DIR)/bin/python

# If you are using uv instead of pip, uncomment the next line and adjust targets.
# UV        ?= uv

# Default log level if not set in .env
LOG_LEVEL   ?= INFO

# When running via `make run`/`post-once`, you can override the source at call time:
#   make post-once SOURCE_FILE=./data.json
SOURCE_FILE ?=
SOURCE_CMD  ?=

# Default target
.DEFAULT_GOAL := help

# Shell
SHELL := bash

# ---- Helpers ------------------------------------------------------------------
# Export .env key=value into the environment for the single command invocation.
define WITH_ENV
	env $$( test -f .env && grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env | xargs ) \
	    LOG_LEVEL=$(LOG_LEVEL)
endef

# Guard to fail early if .env missing.
define REQUIRE_ENV
	@test -f .env || { \
	  echo "✗ .env not found. Run:  make env"; \
	  exit 1; }
endef

# Guard to fail early if venv missing.
define REQUIRE_VENV
	@test -d $(VENV_DIR) || { \
	  echo "✗ $(VENV_DIR) not found. Run:  make venv install"; \
	  exit 1; }
endef

# ---- Phony targets -------------------------------------------------------------
.PHONY: help env print-env venv install upgrade dev run post-once watch lint fmt typecheck test clean distclean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\n\033[1mOracle DA Poster — Make targets\033[0m\n\n"} \
	/^[a-zA-Z0-9_\-]+:.*##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } \
	END { printf "\n" }' $(MAKEFILE_LIST)

env: ## Create a fresh .env from .env.example (does not overwrite)
	@[ -f .env ] && { echo "• .env already exists; not overwriting."; exit 0; } || true
	@cp -n .env.example .env
	@echo "✓ Wrote .env (copied from .env.example). Edit it before running."

print-env: ## Print effective environment that will be passed to the app
	@$(REQUIRE_ENV)
	@$(WITH_ENV) $(PY) - <<'PY'
import os, json
keys = [
  "RPC_URL","WS_URL","CHAIN_ID","ORACLE_MNEMONIC","KEYSTORE_PATH","KEYSTORE_PASSWORD",
  "ORACLE_CONTRACT_ADDR","ORACLE_ABI_PATH","ORACLE_UPDATE_METHOD",
  "DA_API_URL","DA_NAMESPACE_ID","DA_MAX_BLOB_BYTES","BLOB_MIME_TYPE",
  "SOURCE_FILE_PATH","SOURCE_COMMAND","POLL_INTERVAL_SEC","POST_IF_CHANGED","MIN_CHANGE_BPS",
  "HTTP_TIMEOUT_SEC","RETRY_MAX","RETRY_BACKOFF_INITIAL_MS","RETRY_BACKOFF_MAX_MS",
  "LOG_LEVEL","PROMETHEUS_PORT","METRIC_LABEL_SERVICE","METRIC_LABEL_INSTANCE",
  "ALLOW_ORIGINS","TX_GAS_PRICE_GWEI","LOCAL_NONCE_MANAGER","SIGNER_ALG_ID"
]
cfg = {k: os.getenv(k) for k in keys}
print(json.dumps(cfg, indent=2))
PY

venv: ## Create a virtualenv in $(VENV_DIR)
	@$(PYTHON) -m venv $(VENV_DIR)
	@echo "✓ Created $(VENV_DIR)"

install: venv ## Install Python dependencies (pip)
	@$(PIP) install --upgrade pip wheel
	@[ -f requirements.txt ] && $(PIP) install -r requirements.txt || true
	@[ -f pyproject.toml ] && $(PIP) install -e . || true
	@echo "✓ Dependencies installed"

upgrade: venv ## Upgrade all dependencies to latest compatible (pip-tools/uv optional)
	@$(PIP) install --upgrade -r requirements.txt || true
	@echo "✓ Dependencies upgraded"

dev: install ## Install + developer extras (ruff/mypy/pytest if present)
	@$(PIP) install ruff mypy pytest >/dev/null 2>&1 || true
	@echo "✓ Dev tools ready"

run: ## Start the poster in continuous loop mode (uses .env)
	@$(REQUIRE_ENV)
	@$(REQUIRE_VENV)
	@echo "→ Starting poster loop… (Ctrl+C to stop)"
	@$(WITH_ENV) $(PY) -m oracle_da_poster --loop

post-once: ## Perform a single cycle: fetch → DA post → on-chain update
	@$(REQUIRE_ENV)
	@$(REQUIRE_VENV)
	@echo "→ Single update (once)."
	@$(WITH_ENV) SOURCE_FILE="$(SOURCE_FILE)" SOURCE_COMMAND="$(SOURCE_CMD)" $(PY) -m oracle_da_poster --once

watch: ## Re-run on code changes (requires 'entr' or 'watchexec' installed)
	@$(REQUIRE_ENV)
	@$(REQUIRE_VENV)
	@if command -v watchexec >/dev/null 2>&1; then \
	  echo "→ Using watchexec to watch *.py"; \
	  watchexec -e py -r -- $(MAKE) run; \
	elif command -v entr >/dev/null 2>&1; then \
	  echo "→ Using entr to watch *.py"; \
	  find . -name '*.py' | entr -r $(MAKE) run; \
	else \
	  echo "✗ Neither 'watchexec' nor 'entr' is installed. Running once."; \
	  $(MAKE) run; \
	fi

lint: ## Run static lint checks (ruff if available)
	@$(REQUIRE_VENV)
	@$(VENV_DIR)/bin/ruff check . || true

fmt: ## Format code (ruff format if available)
	@$(REQUIRE_VENV)
	@$(VENV_DIR)/bin/ruff format . || true
	@$(VENV_DIR)/bin/ruff check --fix . || true

typecheck: ## Run mypy (if available)
	@$(REQUIRE_VENV)
	@$(VENV_DIR)/bin/mypy . || true

test: ## Run tests (pytest if available)
	@$(REQUIRE_VENV)
	@$(VENV_DIR)/bin/pytest -q || true

clean: ## Remove caches & build artifacts
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + || true
	@rm -rf .pytest_cache .ruff_cache dist build *.egg-info || true
	@echo "✓ Cleaned."

distclean: clean ## Clean everything including the venv
	@rm -rf $(VENV_DIR)
	@echo "✓ Removed $(VENV_DIR)."

# ------------------------------------------------------------------------------
# Tips:
#  - Copy .env.example to .env and edit values.
#  - Override source at runtime: make post-once SOURCE_FILE=./data.json
#  - For verbose logs: make run LOG_LEVEL=DEBUG
# ------------------------------------------------------------------------------
