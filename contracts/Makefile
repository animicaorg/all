# Animica contracts Makefile
# ------------------------------------------------------------
# Quick targets:
#   make build                 Compile ALL packages under contracts/packages/*
#   make build PKG=counter     Compile a single package by name (or EX=counter)
#   make testnet-deploy EX=token  Compile + deploy to testnet (defaults below)
#   make lint                  Ruff/Black lint (best-effort)
#   make clean                 Remove build artifacts
#
# Conventions:
# - A "package" is a folder under contracts/packages/<name>/ with manifest.json + source.
# - If PKG (or EX) refers to a name that doesn't exist under contracts/packages/,
#   we'll try vm_py/examples/<name>/ as a fallback (handy for first-time deploys).
#
# Requirements:
# - Python >= 3.11
# - vm_py and sdk/python importable (e.g., pip install -e ./vm_py ./sdk/python)
# - For lint: ruff and black (optional)
#
# Notes:
# - testnet-deploy uses the Python SDK CLI to sign and broadcast the deploy tx.
# - You can override RPC_URL/CHAIN_ID/KEYSTORE through the environment.
# ------------------------------------------------------------

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Paths ---------------------------------------------------
ROOT        := $(CURDIR)
PKG_DIR     := $(ROOT)/packages
EX_DIR      := vm_py/examples
BUILD_DIR   := $(ROOT)/build

# --- Tools ---------------------------------------------------
PYTHON      ?= python
VM_COMPILE  := $(PYTHON) -m vm_py.cli.compile
SDK_DEPLOY  := $(PYTHON) -m omni_sdk.cli.deploy

# --- Network / Wallet (override in env) ----------------------
RPC_URL     ?= http://127.0.0.1:8545
CHAIN_ID    ?= 2                    # animica testnet chain id per spec/chains.json
KEYSTORE    ?= $(HOME)/.animica/keystore.json

# --- Selection (PKG takes precedence over EX) ----------------
EX          ?= counter
PKG         ?= $(EX)

# Utility: find manifest for a given package name (prefers contracts/packages/, falls back to vm_py/examples/)
define _resolve_manifest
bash -ceu ' \
  name="$(1)"; \
  m1="$(PKG_DIR)/$${name}/manifest.json"; \
  m2="$(EX_DIR)/$${name}/manifest.json"; \
  if [[ -f "$$m1" ]]; then echo "$$m1"; \
  elif [[ -f "$$m2" ]]; then echo "$$m2"; \
  else echo "ERR: manifest for package \x27$${name}\x27 not found in: $$m1 or $$m2" >&2; exit 44; fi'
endef

# Utility: after compiling, locate the .ir in the out dir
define _resolve_ir
bash -ceu ' \
  name="$(1)"; out_dir="$(BUILD_DIR)/$${name}"; \
  shopt -s nullglob; files=("$${out_dir}"/*.ir); \
  if (( $${#files[@]} == 0 )); then echo "ERR: no .ir produced under $$out_dir" >&2; exit 45; fi; \
  echo "$${files[0]}"'
endef

# ------------------------------------------------------------

.PHONY: help
help:
	@echo ""
	@echo "Contracts Makefile — compile, lint, and deploy"
	@echo ""
	@echo "Usage:"
	@echo "  make build                      # compile ALL packages under contracts/packages/*"
	@echo "  make build PKG=<name>           # compile one package by name (or EX=<name>)"
	@echo "  make testnet-deploy EX=<name>   # build + deploy <name> to testnet (see vars below)"
	@echo "  make lint                       # ruff/black over contracts/packages"
	@echo "  make clean                      # remove build/"
	@echo ""
	@echo "Variables (override: make ... RPC_URL=... CHAIN_ID=... KEYSTORE=...):"
	@echo "  RPC_URL   = $(RPC_URL)"
	@echo "  CHAIN_ID  = $(CHAIN_ID)"
	@echo "  KEYSTORE  = $(KEYSTORE)"
	@echo "  PKG/EX    = $(PKG)"
	@echo ""

# ------------------------------------------------------------
# Build targets
# ------------------------------------------------------------

# Compile all packages found under contracts/packages/* that contain a manifest.json
.PHONY: build
build:
	@mkdir -p "$(BUILD_DIR)"
	@if [[ -d "$(PKG_DIR)" ]] && compgen -G "$(PKG_DIR)/*/manifest.json" > /dev/null; then \
	  echo ">> Scanning $(PKG_DIR) for packages..."; \
	  for m in $(PKG_DIR)/*/manifest.json; do \
	    name="$$(basename "$$(dirname "$$m")")"; \
	    out="$(BUILD_DIR)/$$name"; \
	    echo "==> Compiling $$name"; \
	    mkdir -p "$$out"; \
	    $(VM_COMPILE) --manifest "$$m" --out-dir "$$out"; \
	  done; \
	else \
	  echo "!! No packages found under $(PKG_DIR). Try: make build PKG=<name> (or EX=<name>)"; \
	fi

# Compile a single package by name (PKG or EX)
.PHONY: build-one
build-one:
	@[[ -n "$(PKG)" ]] || { echo "ERR: provide PKG=<name> (or EX=<name>)"; exit 2; }
	@manifest="$$( $(call _resolve_manifest,$(PKG)) )"; \
	out="$(BUILD_DIR)/$(PKG)"; \
	echo "==> Compiling $(PKG)"; \
	mkdir -p "$$out"; \
	$(VM_COMPILE) --manifest "$$manifest" --out-dir "$$out"; \
	echo "-- ok: $$manifest -> $$out"

# Alias: allow `make build PKG=...` to compile just one
build-%:
	@$(MAKE) --no-print-directory build-one PKG=$*

# ------------------------------------------------------------
# Linting (best-effort; does not fail CI if tools are absent)
# ------------------------------------------------------------

.PHONY: lint
lint:
	@echo ">> Linting contracts/packages (ruff/black where available)"
	@found=0; if command -v ruff >/dev/null 2>&1; then \
	  found=1; echo " - ruff check"; ruff check "$(PKG_DIR)" || exit $$?; \
	else echo " - ruff not found (pip install ruff) — skipping"; fi; \
	if command -v black >/dev/null 2>&1; then \
	  found=1; echo " - black --check"; black --check "$(PKG_DIR)" || exit $$?; \
	else echo " - black not found (pip install black) — skipping"; fi; \
	if [[ $$found -eq 0 ]]; then echo "!! No linters installed — skipped."; fi

# ------------------------------------------------------------
# Deploy — compiles selected PKG/EX and deploys via Python SDK
# ------------------------------------------------------------

.PHONY: testnet-deploy
testnet-deploy:
	@[[ -n "$(PKG)" ]] || { echo "ERR: provide EX=<name> or PKG=<name>"; exit 2; }
	@manifest="$$( $(call _resolve_manifest,$(PKG)) )"; \
	$(MAKE) --no-print-directory build-one PKG="$(PKG)"; \
	ir="$$( $(call _resolve_ir,$(PKG)) )"; \
	echo "==> Deploying $(PKG) to testnet"; \
	echo "    RPC_URL=$(RPC_URL) CHAIN_ID=$(CHAIN_ID) KEYSTORE=$(KEYSTORE)"; \
	$(SDK_DEPLOY) \
	  --rpc "$(RPC_URL)" \
	  --chain-id "$(CHAIN_ID)" \
	  --keystore "$(KEYSTORE)" \
	  --manifest "$$manifest" \
	  --ir "$$ir"

# Convenience alias for devnet (1337) if you run a local node with default ports
.PHONY: devnet-deploy
devnet-deploy:
	@$(MAKE) --no-print-directory testnet-deploy CHAIN_ID=1337 RPC_URL=http://127.0.0.1:8545

# ------------------------------------------------------------
# Housekeeping
# ------------------------------------------------------------

.PHONY: clean
clean:
	@echo ">> Removing build artifacts: $(BUILD_DIR)"
	@rm -rf "$(BUILD_DIR)"
