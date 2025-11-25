# Animica SDK Makefile
# Targets:
#   make py        - build & test Python SDK
#   make ts        - build & test TypeScript SDK
#   make rs        - build & test Rust SDK
#   make e2e       - run cross-language end-to-end harness (py + ts + rs)
#   make clean     - remove local build artifacts
#   make help      - show this help

SHELL := bash
.ONESHELL:
.SILENT:

PY      ?= python3
PIP     ?= $(PY) -m pip
NPM     ?= npm
CARGO   ?= cargo
NODE    ?= node

VENV        ?= .venv
PYTHON_BIN  := $(VENV)/bin/python
PIP_BIN     := $(VENV)/bin/pip
PYTEST_BIN  := $(VENV)/bin/pytest

RPC_URL    ?= http://127.0.0.1:8545
CHAIN_ID   ?= 31337

.DEFAULT_GOAL := help

.PHONY: help
help:
	echo ""
	echo "Animica SDK — build/test helpers"
	echo ""
	echo "  make py      - build & unit test Python SDK"
	echo "  make ts      - build & unit test TypeScript SDK"
	echo "  make rs      - build & unit test Rust SDK"
	echo "  make e2e     - run cross-language end-to-end harness"
	echo "  make clean   - remove build artifacts"
	echo ""
	echo "Vars (override like VAR=value make e2e):"
	echo "  RPC_URL=$(RPC_URL)"
	echo "  CHAIN_ID=$(CHAIN_ID)"
	echo ""

# -----------------------------
# Python SDK
# -----------------------------

.PHONY: py venv py.test
py: venv
	echo "→ Installing Python SDK (editable) & test deps"
	$(PIP_BIN) install -U pip wheel
	# Minimal deps for tests & examples
	$(PIP_BIN) install -e ./python
	$(PIP_BIN) install pytest httpx websockets anyio
	$(MAKE) py.test

venv:
	if [[ ! -d "$(VENV)" ]]; then
		echo "→ Creating venv at $(VENV)"
		$(PY) -m venv $(VENV)
	fi

py.test:
	echo "→ Running Python unit tests"
	cd python && $(PYTEST_BIN) -q

# -----------------------------
# TypeScript SDK
# -----------------------------

.PHONY: ts ts.build ts.test
ts:
	$(MAKE) ts.build
	$(MAKE) ts.test

ts.build:
	echo "→ Installing TS deps & building"
	cd typescript
	$(NPM) ci
	$(NPM) run -s build

ts.test:
	echo "→ Running TS unit tests"
	cd typescript
	$(NPM) test --silent

# -----------------------------
# Rust SDK
# -----------------------------

.PHONY: rs
rs:
	echo "→ Building Rust SDK"
	cd rust
	$(CARGO) build --all-features
	echo "→ Running Rust tests"
	$(CARGO) test --all-features

# -----------------------------
# Cross-language E2E Harness
# -----------------------------

.PHONY: e2e e2e-py e2e-ts e2e-rs
e2e: e2e-py e2e-ts e2e-rs
	echo "✓ E2E (py + ts + rs) completed"

e2e-py: venv
	echo "→ E2E (Python): deploy+call via Python SDK"
	cd test-harness
	RPC_URL=$(RPC_URL) CHAIN_ID=$(CHAIN_ID) $(PYTHON_BIN) run_e2e_py.py

e2e-ts:
	echo "→ E2E (TypeScript): deploy+call via TS SDK"
	cd test-harness
	RPC_URL=$(RPC_URL) CHAIN_ID=$(CHAIN_ID) $(NODE) run_e2e_ts.mjs

e2e-rs:
	echo "→ E2E (Rust): deploy+call via Rust SDK example"
	cd test-harness
	RPC_URL=$(RPC_URL) CHAIN_ID=$(CHAIN_ID) bash run_e2e_rs.sh

# -----------------------------
# Clean
# -----------------------------

.PHONY: clean
clean:
	echo "→ Cleaning SDK build artifacts"
	rm -rf $(VENV)
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find python -type d \( -name "dist" -o -name "build" -o -name "*.egg-info" \) -prune -exec rm -rf {} +
	cd typescript && rm -rf node_modules dist 2>/dev/null || true
	cd rust && $(CARGO) clean || true
	echo "✓ Clean"
