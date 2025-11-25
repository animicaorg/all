# Animica AICF Provider (FastAPI) — Makefile
# Usage: `make <target>`
#
# Common flows:
#   make init          # create venv + install deps
#   make dev           # run FastAPI with autoreload (dev)
#   make run           # run FastAPI without reload (prod-like)
#   make check         # lint + typecheck + tests
#   make openapi       # export OpenAPI schema to dist/openapi.json
#   make docker-build  # build image
#   make docker-run    # run container locally
#
# Targets are documented; run `make help` to see the list.

SHELL := /bin/bash

# --- Config -------------------------------------------------------------------
PY      ?= python3
PIP     ?= pip3
VENV    ?= .venv
BIN     := $(VENV)/bin
UVICORN := $(BIN)/uvicorn
PYTEST  := $(BIN)/pytest
RUFF    := $(BIN)/ruff
MYPY    := $(BIN)/mypy

# FastAPI ASGI app import path (module:variable)
APP ?= {{project_slug}}.app.main:app

# Server settings
HOST   ?= 0.0.0.0
PORT   ?= 8080
RELOAD ?= 1

# Docker image tag
IMAGE ?= {{project_slug}}:dev

# Coverage package root (adjust if package name differs)
PKG ?= {{project_slug}}

# --- Helpers ------------------------------------------------------------------
define _need_venv
	@test -d "$(VENV)" || { echo ">>> Creating venv: $(VENV)"; $(PY) -m venv "$(VENV)"; }
	@$(BIN)/python -V >/dev/null
endef

define _ensure_dist
	@mkdir -p dist
endef

# --- Meta ---------------------------------------------------------------------
.PHONY: help
help: ## Show this help
	@awk 'BEGIN{FS=":.*## "; printf "\nTargets:\n"} /^[a-zA-Z0-9_.-]+:.*## /{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo

# --- Environment --------------------------------------------------------------
.PHONY: venv
venv: ## Create a local virtualenv (.venv)
	$(call _need_venv)

.PHONY: install
install: venv ## Install runtime (and optional dev) dependencies
	@$(BIN)/python -m pip install --upgrade pip wheel
	@$(BIN)/pip install -r requirements.txt || { echo "Tip: ensure requirements.txt exists."; exit 1; }

.PHONY: init
init: venv install ## Bootstrap dev environment (venv + deps)

.PHONY: update
update: venv ## Update dependencies to latest compatible versions
	@$(BIN)/pip install --upgrade -r requirements.txt

.PHONY: env
env: ## Copy .env.example → .env if not present
	@test -f .env || { test -f .env.example && cp .env.example .env && echo "Wrote .env"; } || echo "No .env.example found."

# --- Quality ------------------------------------------------------------------
.PHONY: fmt
fmt: venv ## Auto-format (ruff)
	@$(BIN)/python -c "import sys,shutil;sys.exit(0 if shutil.which('$(RUFF)') else 1)" || $(BIN)/pip install ruff
	@$(RUFF) --fix .

.PHONY: fmt-check
fmt-check: venv ## Check formatting without modifying files
	@$(RUFF) .

.PHONY: lint
lint: venv ## Lint sources (ruff recommended rule set)
	@$(RUFF) .

.PHONY: typecheck
typecheck: venv ## Static type checking (mypy; optional)
	@$(BIN)/python -c "import sys,shutil;sys.exit(0 if shutil.which('$(MYPY)') else 1)" || $(BIN)/pip install mypy
	@$(MYPY) -p $(PKG) || true

.PHONY: test
test: venv ## Run unit tests (pytest)
	@$(BIN)/python -c "import sys,shutil;sys.exit(0 if shutil.which('$(PYTEST)') else 1)" || $(BIN)/pip install pytest pytest-asyncio
	@$(PYTEST) -q

.PHONY: cov
cov: venv ## Run tests with coverage
	@$(BIN)/pip install -q pytest pytest-cov
	@$(PYTEST) --cov=$(PKG) --cov-report=term-missing

.PHONY: check
check: fmt-check lint typecheck test ## Run all checks (safe to use in CI)

# --- Run ----------------------------------------------------------------------
.PHONY: dev
dev: venv env ## Start dev server with autoreload (UVicorn)
	@echo "→ http://$(HOST):$(PORT)"
	@$(UVICORN) $(APP) --host $(HOST) --port $(PORT) $(if $(filter 1,true,$(RELOAD)) ,--reload,)

.PHONY: run
run: venv env ## Start server without reload (prod-like)
	@echo "→ http://$(HOST):$(PORT)"
	@$(UVICORN) $(APP) --host $(HOST) --port $(PORT)

# --- Artifacts ----------------------------------------------------------------
.PHONY: openapi
openapi: venv ## Export OpenAPI schema to dist/openapi.json
	$(call _ensure_dist)
	@$(BIN)/python - <<'PY'
from importlib import import_module
import json, os
app_path = os.environ.get("APP", "{{project_slug}}.app.main:app")
mod, var = app_path.split(":")
app = getattr(import_module(mod), var)
schema = app.openapi()
with open("dist/openapi.json","w") as f:
    json.dump(schema, f, indent=2)
print("Wrote dist/openapi.json")
PY

# --- Docker -------------------------------------------------------------------
.PHONY: docker-build
docker-build: ## Build local Docker image
	@docker build -t $(IMAGE) .

.PHONY: docker-run
docker-run: ## Run container locally (maps $(PORT))
	@docker run --rm -p $(PORT):$(PORT) -e HOST=$(HOST) -e PORT=$(PORT) --name {{project_slug}} $(IMAGE)

.PHONY: docker-push
docker-push: ## Push image (requires registry auth); set IMAGE=registry/namespace/name:tag
	@[ -n "$(IMAGE)" ] || { echo "IMAGE is required, e.g. IMAGE=ghcr.io/org/{{project_slug}}:v0.1.0"; exit 1; }
	@docker push $(IMAGE)

# --- Clean --------------------------------------------------------------------
.PHONY: clean
clean: ## Remove caches and build artifacts
	@find . -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' -o -name '.pytest_cache' -o -name '.ruff_cache' -o -name 'dist' | xargs -r rm -rf

.PHONY: deepclean
deepclean: clean ## Also remove the virtualenv
	@rm -rf $(VENV)

