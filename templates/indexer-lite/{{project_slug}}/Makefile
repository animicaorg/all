# ==============================================================================
# {{ project_slug }} — Indexer Lite
# Makefile: quick dev ergonomics for venv, install, run, lint, test, docker.
# ------------------------------------------------------------------------------

# Python / venv
PYTHON        ?= python3
VENV_DIR      ?= .venv
PY            := $(VENV_DIR)/bin/python
PIP           := $(VENV_DIR)/bin/pip

# Module name (convert project-slug to a valid Python package import)
MODULE        ?= {{ project_slug.replace("-", "_") }}

# Environment
ENV_FILE      ?= .env
ENV_EXAMPLE   ?= .env.example

# Defaults (can be overridden via env or CLI: `make run CONCURRENCY=4`)
CONCURRENCY   ?= 8
BATCH_SIZE    ?= 200
START_HEIGHT  ?= auto
END_HEIGHT    ?=
LOG_LEVEL     ?= INFO

# DB (only used by clean/reset helpers; matches .env defaults)
DB_PATH       ?= indexer.db

# Docker
IMAGE_TAG     ?= {{ project_slug }}:dev

# ------------------------------------------------------------------------------

.PHONY: help
help: ## Show this help
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_\/-]+:.*?## / { printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

# ------------------------------------------------------------------------------
# Setup
# ------------------------------------------------------------------------------

.PHONY: venv
venv: ## Create virtualenv at $(VENV_DIR)
	@test -d "$(VENV_DIR)" || $(PYTHON) -m venv "$(VENV_DIR)"
	@echo "✅ venv ready at $(VENV_DIR)"

.PHONY: install
install: venv ## Install dependencies (requirements + editable package if present)
	@$(PIP) -q install --upgrade pip wheel
	@if [ -f requirements.txt ]; then \
		echo "📦 Installing requirements.txt"; \
		$(PIP) -q install -r requirements.txt; \
	fi
	@if [ -f pyproject.toml ]; then \
		echo "📦 Installing project (editable)"; \
		$(PIP) -q install -e .; \
	fi
	@echo "✅ Dependencies installed"

.PHONY: env
env: ## Create .env from .env.example if missing
	@if [ ! -f "$(ENV_FILE)" ] && [ -f "$(ENV_EXAMPLE)" ]; then \
		cp "$(ENV_EXAMPLE)" "$(ENV_FILE)"; \
		echo "✨ Created $(ENV_FILE) from $(ENV_EXAMPLE)"; \
	else \
		echo "ℹ️  $(ENV_FILE) already exists (or no example present)"; \
	fi

# ------------------------------------------------------------------------------
# Run / Dev
# ------------------------------------------------------------------------------

define LOAD_ENV
set -a; [ -f $(ENV_FILE) ] && . $(ENV_FILE) || true; set +a;
endef

.PHONY: run
run: install env ## Start the indexer (follows head; uses .env)
	@echo "▶️  Starting indexer: module=$(MODULE) concurrency=$(CONCURRENCY) batch=$(BATCH_SIZE) start=$(START_HEIGHT) end=$(END_HEIGHT) log=$(LOG_LEVEL)"
	@bash -c '$(LOAD_ENV) \
		"$(PY)" -m $(MODULE).main \
			--concurrency $(CONCURRENCY) \
			--batch-size $(BATCH_SIZE) \
			--start $(START_HEIGHT) \
			$(if $(END_HEIGHT),--end $(END_HEIGHT),) \
			--log-level $(LOG_LEVEL)'

.PHONY: backfill
backfill: install env ## Backfill from genesis or explicit START_HEIGHT to END_HEIGHT (no WS)
	@echo "⏪ Backfill: start=$(START_HEIGHT) end=$(END_HEIGHT)"
	@bash -c '$(LOAD_ENV) \
		WS_SUBSCRIBE=false "$(PY)" -m $(MODULE).main \
			--concurrency $(CONCURRENCY) \
			--batch-size $(BATCH_SIZE) \
			--start $(START_HEIGHT) \
			$(if $(END_HEIGHT),--end $(END_HEIGHT),) \
			--log-level $(LOG_LEVEL)'

.PHONY: api
api: install env ## (Optional) start read-only API if module provides it
	@echo "🌐 Starting API server (if implemented)"
	@bash -c '$(LOAD_ENV) \
		"$(PY)" -m $(MODULE).api || (echo "API module not found"; exit 1)'

.PHONY: metrics
metrics: install env ## Expose /metrics (if integrated in main/api)
	@echo "📈 Metrics are typically exposed by run/api; see $(ENV_FILE) METRICS_PORT"

# ------------------------------------------------------------------------------
# Quality
# ------------------------------------------------------------------------------

.PHONY: fmt
fmt: venv ## Format code (ruff + black if available)
	@$(VENV_DIR)/bin/ruff format || true
	@$(VENV_DIR)/bin/black . || true

.PHONY: lint
lint: venv ## Lint (ruff) & type-check (mypy) if available
	@$(VENV_DIR)/bin/ruff check . || true
	@$(VENV_DIR)/bin/mypy . || true

.PHONY: test
test: install ## Run unit tests with pytest (if tests/ present)
	@$(VENV_DIR)/bin/pytest -q || true

# ------------------------------------------------------------------------------
# Database helpers (SQLite defaults)
# ------------------------------------------------------------------------------

.PHONY: db-path
db-path: ## Show local DB path (best effort for SQLite)
	@echo "$(DB_PATH)"

.PHONY: db-reset
db-reset: ## Remove local SQLite DB (dangerous)
	@set -e; \
	if [ -f "$(DB_PATH)" ]; then \
		echo "🗑  Removing $(DB_PATH)"; rm -f "$(DB_PATH)"; \
	else \
		echo "ℹ️  No $(DB_PATH) found"; \
	fi

.PHONY: vacuum
vacuum: venv ## VACUUM / optimize SQLite DB
	@$(PY) - <<'PY'
import os, sqlite3
p = os.environ.get("DB_PATH","indexer.db")
if not os.path.exists(p):
    print(f"No DB at {p}")
else:
    print(f"VACUUM {p} ...")
    con = sqlite3.connect(p)
    con.execute("PRAGMA optimize;")
    con.execute("VACUUM;")
    con.close()
    print("✅ Done")
PY

# ------------------------------------------------------------------------------
# Docker (dev convenience)
# ------------------------------------------------------------------------------

.PHONY: docker-build
docker-build: ## Build dev image
	@docker build -t $(IMAGE_TAG) .

.PHONY: docker-run
docker-run: ## Run dev image with local .env mounted
	@docker run --rm -it --name {{ project_slug }} \
		--env-file $(ENV_FILE) \
		-v "$(PWD)":/app \
		-w /app \
		$(IMAGE_TAG) \
		make run

# ------------------------------------------------------------------------------
# Clean
# ------------------------------------------------------------------------------

.PHONY: clean
clean: ## Clean caches and build artifacts
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	@find . -name '*.pyc' -delete
	@rm -rf .pytest_cache .mypy_cache .ruff_cache dist build

# Default target
.DEFAULT_GOAL := help
