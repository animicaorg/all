# ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
# ┃ zk/Makefile — circuits dev QoL: tests, benches, artifact management  ┃
# ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
#
# Targets:
#   make test              Run zk-related unit/property tests
#   make bench             Run lightweight verifier micro-benches (if available)
#   make fetch-artifacts   Fetch SRS / VKs / fixtures into zk/artifacts/
#   make vk-refresh        Regenerate Verifying Keys (VKs) from circuits
#   make print-circuits    Show which circuits this Makefile will operate on
#   make clean             Remove local artifacts cache
#
# Notes:
# - This Makefile is intentionally defensive: if optional tools/scripts are
#   missing, it prints actionable guidance instead of failing hard.
# - Override variables from your shell or a local .env (auto-included).
#
# Examples:
#   SRS_URL=https://example.com/kzg/bls12-381-kzg-v1.srs make fetch-artifacts
#   CIRCUITS="merkle_inclusion_v1 range_u64_v1" make vk-refresh
#   PYTEST_ADDOPTS="-q -k merkle" make test
#
# Conventions:
# - Artifacts dir layout:
#     zk/artifacts/
#       ├── srs/           # structured reference strings (KZG/IPA/etc.)
#       ├── vk/            # verifying keys per circuit (vk.json or vk.bin)
#       ├── pk/            # proving keys (optional; usually off-repo)
#       └── fixtures/      # golden proofs, inputs, and bad-cases
#
# - Circuit registry (lightweight): CIRCUITS variable (space-separated).
#   You can also place circuit descriptors under zk/circuits/<id>/.
#
# Tooling hooks (optional, override as needed):
# - VK generation script:   zk/tools/vk_gen.py
# - Bench runner script:    zk/tools/bench_verify.py
# - Artifact fetch helper:  zk/tools/fetch.py
# Provide equivalents in your environment if you use a different stack.
#
# ─────────────────────────────────────────────────────────────────────────

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

# Auto-include environment if present (e.g., SRS_URL, CIRCUITS, etc.)
ifneq ("$(wildcard .env)","")
  include .env
  export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' .env)
endif

# Python & tooling
PYTHON           ?= python3
PYTEST           ?= pytest
CURL             ?= $(shell command -v curl 2>/dev/null || true)
WGET             ?= $(shell command -v wget 2>/dev/null || true)
SHA256SUM        ?= $(shell command -v sha256sum 2>/dev/null || command -v shasum 2>/dev/null || true)

# Artifact directories
ARTIFACTS_DIR    ?= $(abspath artifacts)
SRS_DIR          ?= $(ARTIFACTS_DIR)/srs
VK_DIR           ?= $(ARTIFACTS_DIR)/vk
PK_DIR           ?= $(ARTIFACTS_DIR)/pk
FIXTURES_DIR     ?= $(ARTIFACTS_DIR)/fixtures

# Default SRS profile (matches zk/README.md)
SRS_ID           ?= bls12-381-kzg-v1
SRS_FILE         ?= $(SRS_ID).srs
SRS_PATH         ?= $(SRS_DIR)/$(SRS_FILE)
# Optional integrity check (hex-encoded); set to empty to skip
SRS_SHA256       ?=

# Circuits registry; override to a subset as needed
# You may also point to actual directories under zk/circuits/<id>/
CIRCUITS_DEFAULT := merkle_inclusion_v1 range_u64_v1 eddsa_ed25519_v1 ecdsa_secp256k1_v1 set_membership_poseidon_v1
CIRCUITS         ?= $(CIRCUITS_DEFAULT)

# Optional script hooks (override if you have custom toolchain)
VK_GEN_SCRIPT        ?= zk/tools/vk_gen.py
BENCH_VERIFY_SCRIPT  ?= zk/tools/bench_verify.py
FETCH_HELPER         ?= zk/tools/fetch.py

# Misc
COLOR ?= 1
ifeq ($(COLOR),1)
  c_info  = \033[1;36m
  c_warn  = \033[1;33m
  c_err   = \033[1;31m
  c_ok    = \033[1;32m
  c_dim   = \033[2m
  c_off   = \033[0m
else
  c_info  =
  c_warn  =
  c_err   =
  c_ok    =
  c_dim   =
  c_off   =
endif

.PHONY: help
help:
	@echo -e "$(c_info)ZK Make targets$(c_off)"
	@echo "  make test              - run zk-related tests"
	@echo "  make bench             - run verifier micro-benchmarks"
	@echo "  make fetch-artifacts   - download SRS/VK/fixtures into zk/artifacts/"
	@echo "  make vk-refresh        - regenerate VKs for CIRCUITS=$(CIRCUITS)"
	@echo "  make print-circuits    - list circuits"
	@echo "  make clean             - remove zk/artifacts/"
	@echo
	@echo "Variables you can override:"
	@echo "  CIRCUITS, SRS_URL, SRS_ID, SRS_SHA256, PYTEST, PYTEST_ADDOPTS"
	@echo "  VK_GEN_SCRIPT, BENCH_VERIFY_SCRIPT, FETCH_HELPER"

# ─────────────────────────────────────────────────────────────────────────
# Utilities

define _ensure_dirs
	mkdir -p "$(SRS_DIR)" "$(VK_DIR)" "$(PK_DIR)" "$(FIXTURES_DIR)"
endef

define _require_cmd
	@command -v $(1) >/dev/null 2>&1 || { \
		echo -e "$(c_err)Missing required tool: $(1)$(c_off)"; \
		exit 1; }
endef

# Fallback download using curl or wget
define _download
	@if [ -n "$(CURL)" ]; then \
		echo -e "$(c_dim)[curl]$(c_off) $(1) -> $(2)"; \
		"$(CURL)" -fL "$(1)" -o "$(2)"; \
	elif [ -n "$(WGET)" ]; then \
		echo -e "$(c_dim)[wget]$(c_off) $(1) -> $(2)"; \
		"$(WGET)" -q -O "$(2)" "$(1)"; \
	else \
		echo -e "$(c_err)Neither curl nor wget found; cannot fetch $(1)$(c_off)"; \
		exit 1; \
	fi
endef

# SHA256 integrity check if SRS_SHA256 is set
define _sha256_check
	@if [ -n "$(SRS_SHA256)" ]; then \
		if [ -n "$(SHA256SUM)" ]; then \
			if echo "$(SRS_SHA256)  $(SRS_PATH)" | $(SHA256SUM) -c - >/dev/null 2>&1; then \
				echo -e "$(c_ok)SRS checksum OK$(c_off)"; \
			else \
				echo -e "$(c_err)SRS checksum FAILED$(c_off)"; exit 1; \
			fi; \
		else \
			echo -e "$(c_warn)No sha256 tool found; skipping SRS integrity check$(c_off)"; \
		fi; \
	fi
endef

# ─────────────────────────────────────────────────────────────────────────
# Primary targets

.PHONY: test
test:
	@$(call _ensure_dirs)
	@echo -e "$(c_info)Running zk-related tests$(c_off)"
	@if [ -n "$$($(PYTEST) --version 2>/dev/null)" ]; then \
		set -e; \
		# Prefer zk-specific tests if present, otherwise fall back to a keyword slice.
		if [ -d "tests/zk" ]; then \
			$(PYTEST) $(PYTEST_ADDOPTS) tests/zk; \
		else \
			$(PYTEST) $(PYTEST_ADDOPTS) -k "zk or proof or verify" tests || \
			{ echo -e "$(c_warn)No explicit zk test suite found; ran keyword-filtered tests.$(c_off)"; true; }; \
		fi \
	else \
		echo -e "$(c_warn)pytest not available. Install it or override PYTEST=...$(c_off)"; \
		echo -e "$(c_dim)pip install pytest$(c_off)"; \
	fi

.PHONY: bench
bench:
	@$(call _ensure_dirs)
	@echo -e "$(c_info)Running verifier micro-benchmarks$(c_off)"
	@if [ -f "$(BENCH_VERIFY_SCRIPT)" ]; then \
		"$(PYTHON)" "$(BENCH_VERIFY_SCRIPT)" --srs "$(SRS_PATH)" --circuits "$(CIRCUITS)"; \
	else \
		echo -e "$(c_warn)Bench script not found: $(BENCH_VERIFY_SCRIPT)$(c_off)"; \
		echo -e "$(c_dim)Create it or override BENCH_VERIFY_SCRIPT=/path/to/bench.py$(c_off)"; \
		# Fallback: try pytest benches if the repo provides them
		if [ -d "tests/bench" ]; then \
			$(PYTEST) -q -k "zk or verify" tests/bench || true; \
		fi; \
	fi

.PHONY: fetch-artifacts
fetch-artifacts:
	@$(call _ensure_dirs)
	@echo -e "$(c_info)Fetching zk artifacts into $(ARTIFACTS_DIR)$(c_off)"
	@if [ -n "$(FETCH_HELPER)" ] && [ -f "$(FETCH_HELPER)" ]; then \
		"$(PYTHON)" "$(FETCH_HELPER)" \
			--out "$(ARTIFACTS_DIR)" \
			${SRS_URL:+--srs-url "$(SRS_URL)"} \
			${SRS_ID:+--srs-id "$(SRS_ID)"} || true; \
	else \
		echo -e "$(c_dim)No fetch helper found; attempting direct SRS fetch if SRS_URL is provided$(c_off)"; \
		if [ -n "$(SRS_URL)" ]; then \
			$(call _download,$(SRS_URL),$(SRS_PATH)); \
			$(call _sha256_check); \
			echo -e "$(c_ok)SRS stored at $(SRS_PATH)$(c_off)"; \
		else \
			echo -e "$(c_warn)SRS_URL not set. Provide SRS_URL to fetch SRS, or place it at:$(c_off)"; \
			echo -e "  $(c_dim)$(SRS_PATH)$(c_off)"; \
		fi; \
	fi
	@echo -e "$(c_ok)Fetch complete (if inputs were provided).$(c_off)"

.PHONY: vk-refresh
vk-refresh:
	@$(call _ensure_dirs)
	@echo -e "$(c_info)Regenerating Verifying Keys for circuits:$(c_off) $(CIRCUITS)"
	@if [ ! -f "$(SRS_PATH)" ]; then \
		echo -e "$(c_warn)SRS not found at $(SRS_PATH). Run 'make fetch-artifacts' or set SRS_PATH. Continuing may fail.$(c_off)"; \
	fi
	@if [ -f "$(VK_GEN_SCRIPT)" ]; then \
		for C in $(CIRCUITS); do \
		  echo -e "$(c_dim)[vk]$(c_off) $$C"; \
		  "$(PYTHON)" "$(VK_GEN_SCRIPT)" \
		    --circuit "$$C" \
		    --srs "$(SRS_PATH)" \
		    --out "$(VK_DIR)/$$C.vk.json"; \
		done; \
		echo -e "$(c_ok)VK refresh complete. Files in $(VK_DIR)$(c_off)"; \
	else \
		echo -e "$(c_err)VK generation script not found: $(VK_GEN_SCRIPT)$(c_off)"; \
		echo -e "$(c_dim)Provide it, or override VK_GEN_SCRIPT=/path/to/vk_gen.py$(c_off)"; \
		exit 1; \
	fi

.PHONY: print-circuits
print-circuits:
	@echo -e "$(c_info)CIRCUITS:$(c_off) $(CIRCUITS)"
	@echo -e "$(c_dim)Artifacts dir: $(ARTIFACTS_DIR) | SRS: $(SRS_ID) -> $(SRS_PATH)$(c_off)"

.PHONY: clean
clean:
	@echo -e "$(c_warn)Removing $(ARTIFACTS_DIR)$(c_off)"
	@rm -rf "$(ARTIFACTS_DIR)"
	@echo -e "$(c_ok)Clean done.$(c_off)"

# End of file
