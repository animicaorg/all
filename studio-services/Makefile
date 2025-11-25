# Animica Studio Services — Makefile
# Usage: cd studio-services; make help

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Config -------------------------------------------------------------------
APP_MODULE ?= studio_services.main:app
HOST ?= 0.0.0.0
PORT ?= 8000
WORKERS ?= 3
ENV_FILE ?= .env

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
GUNICORN := $(VENV)/bin/gunicorn
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
BLACK := $(VENV)/bin/black
MYPY := $(VENV)/bin/mypy

# --- Helpers ------------------------------------------------------------------
$(VENV)/bin/python: requirements.txt pyproject.toml
	@echo "🐍 Creating virtualenv in $(VENV)"
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev]"

.PHONY: help
help:
	@echo ""
	@echo "Animica Studio Services"
	@echo "───────────────────────"
	@echo "make venv        # create venv and install deps"
	@echo "make dev         # run FastAPI with reload (uvicorn)"
	@echo "make run         # run with Gunicorn+Uvicorn workers"
	@echo "make test        # run pytest suite"
	@echo "make lint        # ruff/black check"
	@echo "make fmt         # auto-format (ruff --fix, black)"
	@echo "make typecheck   # mypy strict typing"
	@echo "make migrate     # apply DB migrations"
	@echo "make fixtures    # load local fixtures (optional)"
	@echo "make docker-build# build Docker image"
	@echo "make docker-run  # run Docker image (reads .env)"
	@echo "make clean       # clean caches and build artifacts"
	@echo ""

.PHONY: venv
venv: $(VENV)/bin/python

# --- Run modes ----------------------------------------------------------------
.PHONY: dev
dev: venv
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT) --reload --env-file $(ENV_FILE)

.PHONY: run
run: venv
	$(GUNICORN) "$(APP_MODULE)" -k uvicorn.workers.UvicornWorker -w $(WORKERS) -b $(HOST):$(PORT) --access-logfile - --error-logfile -

# --- Quality ------------------------------------------------------------------
.PHONY: test
test: venv
	$(PYTEST) -q

.PHONY: lint
lint: venv
	$(RUFF) check .
	$(BLACK) --check .

.PHONY: fmt
fmt: venv
	$(RUFF) check --fix .
	$(BLACK) .

.PHONY: typecheck
typecheck: venv
	$(MYPY) studio_services

# --- Ops ----------------------------------------------------------------------
.PHONY: migrate
migrate: venv
	$(PYTHON) -m studio_services.cli migrate

.PHONY: fixtures
fixtures: venv
	@if [ -f scripts/load_fixtures.py ]; then \
	  $(PYTHON) scripts/load_fixtures.py ; \
	else \
	  echo "No fixtures loader found at scripts/load_fixtures.py"; \
	fi

# --- Docker -------------------------------------------------------------------
IMAGE ?= animica/studio-services:local

.PHONY: docker-build
docker-build:
	docker build -f Dockerfile -t $(IMAGE) .

.PHONY: docker-run
docker-run:
	docker run --rm -p $(PORT):$(PORT) --env-file $(ENV_FILE) $(IMAGE)

# --- Clean --------------------------------------------------------------------
.PHONY: clean
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
