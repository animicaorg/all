# ==============================================================================
# {{project_slug}} — Makefile
# ------------------------------------------------------------------------------
# Quick commands to set up a venv, lint, test, compile contracts to IR, package,
# deploy via Python SDK, and run local simulations with the Python VM.
#
# Usage examples:
#   make              # show help
#   make venv         # create .venv and install deps
#   make lint         # ruff lint
#   make typecheck    # mypy type checks
#   make test         # run pytest
#   make build        # compile manifest+source -> IR in build/
#   make package      # bundle manifest+IR into build/{{project_slug}}.tar.zst
#   make deploy       # deploy via omni_sdk to RPC (needs .env)
#   make call FUNC=get ARGS='[]'  # simulate read call with vm_py
#
# ENV:
#   You can set variables inline, in your shell, or in a local .env file.
#   Expected keys (see .env.example):
#     RPC_URL="http://127.0.0.1:8545"
#     CHAIN_ID=1337
#     SERVICES_URL="http://127.0.0.1:8080"   # optional (for studio-services verify)
#     DEPLOYER_MNEMONIC="..."               # for deployments using the SDK
# ==============================================================================

# ---- project layout -----------------------------------------------------------
SRC_DIR      ?= src
BUILD_DIR    ?= build
CONTRACT     ?= $(SRC_DIR)/contract.py
MANIFEST     ?= manifest.json
IR_OUT       ?= $(BUILD_DIR)/code.ir
PKG_OUT      ?= $(BUILD_DIR)/{{project_slug}}.tar.zst

# ---- Python/venv --------------------------------------------------------------
PY           ?= python3
VENV         ?= .venv
PIP          := $(VENV)/bin/pip
PYTHON       := $(VENV)/bin/python
RUFF         := $(VENV)/bin/ruff
MYPY         := $(VENV)/bin/mypy
PYTEST       := $(VENV)/bin/pytest

# ---- .env import (optional) ---------------------------------------------------
# If .env exists, import KEY=VAL lines and export them for recipes.
ifneq (,$(wildcard .env))
include .env
export
endif

# ---- helper macros ------------------------------------------------------------
define _need_venv
	@[ -d "$(VENV)" ] || (echo ">> Creating venv: $(VENV)"; $(PY) -m venv $(VENV))
	@$(PIP) -q --disable-pip-version-check install -U pip >/dev/null
endef

# Pretty print for help
HELP_FMT = printf "  \033[1m%-20s\033[0m %s\n"

# Default target
.DEFAULT_GOAL := help

# ---- meta ---------------------------------------------------------------------
.PHONY: help
help:
	@echo "Targets:"
	@$(HELP_FMT) "venv"              "Create .venv and install requirements"
	@$(HELP_FMT) "install"           "Install/upgrade dependencies into .venv"
	@$(HELP_FMT) "fmt"               "Auto-fix with ruff"
	@$(HELP_FMT) "lint"              "Run ruff checks"
	@$(HELP_FMT) "typecheck"         "Run mypy"
	@$(HELP_FMT) "test"              "Run pytest"
	@$(HELP_FMT) "coverage"          "Run pytest with coverage report"
	@$(HELP_FMT) "build"             "Compile MANIFEST+source to IR file in build/"
	@$(HELP_FMT) "package"           "Create content-addressed package tar.zst in build/"
	@$(HELP_FMT) "deploy"            "Deploy package via omni_sdk to RPC"
	@$(HELP_FMT) "call"              "Simulate a read call using vm_py (FUNC,ARGS env)"
	@$(HELP_FMT) "clean"             "Remove build artifacts and caches"
	@$(HELP_FMT) "freeze"            "Write locked requirements to requirements.lock"
	@$(HELP_FMT) "upgrade-deps"      "pip install -U all deps in requirements.txt"
	@echo
	@echo "Variables:"
	@$(HELP_FMT) "CONTRACT"          "$(CONTRACT)"
	@$(HELP_FMT) "MANIFEST"          "$(MANIFEST)"
	@$(HELP_FMT) "IR_OUT"            "$(IR_OUT)"
	@$(HELP_FMT) "PKG_OUT"           "$(PKG_OUT)"
	@echo
	@echo "ENV (.env): RPC_URL, CHAIN_ID, SERVICES_URL, DEPLOYER_MNEMONIC"

# ---- setup --------------------------------------------------------------------
.PHONY: venv
venv:
	$(call _need_venv)
	@$(PIP) install -r requirements.txt

.PHONY: install
install: venv

# ---- quality ------------------------------------------------------------------
.PHONY: fmt
fmt: venv
	@$(RUFF) check --fix .

.PHONY: lint
lint: venv
	@$(RUFF) check .

.PHONY: typecheck
typecheck: venv
	@$(MYPY) $(SRC_DIR) || true

# ---- tests --------------------------------------------------------------------
.PHONY: test
test: venv
	@$(PYTEST) -q

.PHONY: coverage
coverage: venv
	@$(PYTEST) --cov=$(SRC_DIR) --cov-report=term-missing

# ---- build / package ----------------------------------------------------------
$(BUILD_DIR):
	@mkdir -p $(BUILD_DIR)

# Compiles contract using vm_py CLI.
# Requires vm_py to be importable in the venv (editable install or published pkg).
.PHONY: build
build: venv $(BUILD_DIR)
	@test -f "$(MANIFEST)" || (echo "ERR: Missing $(MANIFEST)"; exit 1)
	@test -f "$(CONTRACT)" || (echo "ERR: Missing $(CONTRACT)"; exit 1)
	@echo ">> Compiling with vm_py..."
	@$(PYTHON) -m vm_py.cli.compile --manifest "$(MANIFEST)" --out "$(IR_OUT)"
	@ls -lh "$(IR_OUT)"

# Packages manifest + IR into a deterministic tar.zst, including a code-hash file.
.PHONY: package
package: build
	@echo ">> Packaging artifact: $(PKG_OUT)"
	@set -e; \
	mkdir -p $(BUILD_DIR)/pkg && \
	cp "$(MANIFEST)" $(BUILD_DIR)/pkg/manifest.json && \
	cp "$(IR_OUT)"   $(BUILD_DIR)/pkg/code.ir && \
	$(PYTHON) - <<'PY' > $(BUILD_DIR)/pkg/code.sha3_256.txt ; \
import hashlib, sys, pathlib; \
p=pathlib.Path("$(BUILD_DIR)/pkg/code.ir"); \
h=hashlib.sha3_256(p.read_bytes()).hexdigest(); \
print(h) \
PY
	@tar --sort=name --owner=0 --group=0 --numeric-owner -C $(BUILD_DIR)/pkg -I 'zstd -19 -T0' -cf "$(PKG_OUT)" .
	@echo ">> Wrote: $(PKG_OUT)"
	@rm -rf $(BUILD_DIR)/pkg

# ---- deploy & simulate --------------------------------------------------------
# Deploy via Python SDK CLI (requires omni_sdk installed in venv).
# Uses RPC_URL, CHAIN_ID, DEPLOYER_MNEMONIC from .env.
.PHONY: deploy
deploy: package
	@test -n "$(RPC_URL)" || (echo "ERR: RPC_URL not set (see .env.example)"; exit 1)
	@test -n "$(CHAIN_ID)" || (echo "ERR: CHAIN_ID not set (see .env.example)"; exit 1)
	@test -n "$(DEPLOYER_MNEMONIC)" || (echo "ERR: DEPLOYER_MNEMONIC not set"; exit 1)
	@echo ">> Deploying via omni_sdk..."
	@$(PYTHON) -m omni_sdk.cli.deploy --manifest "$(MANIFEST)" --rpc "$(RPC_URL)" --chain-id "$(CHAIN_ID)" --mnemonic "$(DEPLOYER_MNEMONIC)"

# Simulate a read call using vm_py (no chain state writes).
# Example: make call FUNC=get ARGS='[]'
FUNC ?= get
ARGS ?= []
.PHONY: call
call: venv build
	@echo ">> Simulating call: $(FUNC)$(ARGS)"
	@$(PYTHON) -m vm_py.cli.run --manifest "$(MANIFEST)" --call "$(FUNC)" --args '$(ARGS)'

# ---- maintenance --------------------------------------------------------------
.PHONY: clean
clean:
	@rm -rf $(BUILD_DIR) .pytest_cache .mypy_cache .ruff_cache .coverage dist

.PHONY: freeze
freeze: venv
	@$(PIP) freeze > requirements.lock
	@echo ">> Wrote requirements.lock"

.PHONY: upgrade-deps
upgrade-deps: venv
	@$(PIP) install -U -r requirements.txt
	@$(PIP) freeze | sed 's/@ file:.*$$//' > requirements.lock
	@echo ">> Dependencies upgraded and locked"

