# Animica Native — Makefile
# -------------------------------------------------------------
# Convenience targets for building the Rust crate, producing Python wheels
# via maturin, running benches, and tests. These targets are thin wrappers
# over cargo/maturin with sensible defaults and a few knobs you can tweak
# through environment variables.
#
# Common env vars you can pass:
#   PROFILE=release|debug        # default: release
#   FEATURES="simd,rayon,isal"   # cargo features to enable (comma- or space-separated)
#   TARGET=<rust-target-triple>  # for cross builds (e.g. aarch64-apple-darwin)
#   RUSTFLAGS="..."              # extra rustc flags
#   ANIMICA_NATIVE_FORCE_CPU="avx2,sha3"   # override CPU feature detection for CI
#   COMPAT=manylinux2014|...     # maturin --compatibility (Linux)
#   INTERPRETERS="py3.9 py3.10"  # subset of Python versions for maturin local builds
#   BENCH=?pattern               # run only benches matching pattern
#   TEST=?pattern                # run only tests matching pattern
#   WITH_PY=0|1                  # add "python" cargo feature for cargo builds (default 0)
#
# Examples:
#   make build
#   make build PROFILE=debug FEATURES="simd c_keccak"
#   make wheel FEATURES="simd isal" COMPAT=manylinux_2_28
#   make bench BENCH=keccak
#   make test TEST=codec
# -------------------------------------------------------------

SHELL := bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c

# Tools (override if needed)
CARGO    ?= cargo
MATURIN  ?= maturin
PY       ?= python3

# Detect OS for a few defaults
UNAME_S := $(shell uname -s)

# Profile maps to cargo flags
PROFILE  ?= release
ifeq ($(PROFILE),release)
  CARGO_PROFILE_FLAG := --release
else
  CARGO_PROFILE_FLAG :=
endif

# Features:
# Do not include "python" by default—maturin injects it automatically for wheel builds.
# For plain cargo builds/tests, set WITH_PY=1 if you want to compile Python-facing code paths.
FEATURES ?= simd rayon
# Normalize features: allow both comma or space separation
FEATURES_SPACES := $(subst ',', ,$(FEATURES))
ifeq ($(WITH_PY),1)
  FEATURES_SPACES := $(FEATURES_SPACES) python
endif

# Optional target triple (useful for cross)
ifdef TARGET
  TARGET_FLAG := --target $(TARGET)
else
  TARGET_FLAG :=
endif

# Maturin options
COMPAT        ?= manylinux2014
INTERPRETERS  ?=
MATURIN_OPTS  := --strip $(if $(findstring release,$(PROFILE)),--release,) \
                 $(if $(TARGET),--target $(TARGET),) \
                 $(if $(COMPAT),--compatibility $(COMPAT),)

# If user restricts interpreters, pass them as --interpreter
ifneq ($(strip $(INTERPRETERS)),)
  MATURIN_OPTS += $(foreach i,$(INTERPRETERS),--interpreter $(i))
endif

# Default goal: show help
.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "Animica Native — Make targets"
	@echo
	@echo "  make build            Build Rust crate ($(PROFILE))"
	@echo "  make wheel            Build Python wheel(s) with maturin ($(PROFILE))"
	@echo "  make bench            Run benchmarks (criterion) with optional BENCH=filter"
	@echo "  make test             Run Rust tests with optional TEST=filter"
	@echo
	@echo "Other useful targets:"
	@echo "  make check            cargo check + clippy"
	@echo "  make fmt              rustfmt + cargo fmt"
	@echo "  make clean            Remove target/, dist/, build artifacts"
	@echo "  make print-env        Show effective settings"
	@echo
	@echo "Knobs (env vars): PROFILE, FEATURES, TARGET, RUSTFLAGS, ANIMICA_NATIVE_FORCE_CPU,"
	@echo "                  COMPAT, INTERPRETERS, BENCH, TEST, WITH_PY"
	@echo
	@$(MAKE) print-env

# -------------------------------------------------------------
# Core targets
# -------------------------------------------------------------

.PHONY: build
build: ## cargo build of the library/binaries
	@echo "==> cargo build ($(PROFILE)) with features: $(FEATURES_SPACES)"
	$(CARGO) build $(CARGO_PROFILE_FLAG) $(TARGET_FLAG) --features "$(FEATURES_SPACES)"

.PHONY: wheel
wheel: ## Build Python wheel(s) using maturin (respects pyproject config)
	@echo "==> maturin build $(if $(TARGET),for $(TARGET),) ($(PROFILE))"
	@echo "    features: $(FEATURES_SPACES) (python feature injected by maturin)"
	# maturin injects 'python' feature per pyproject; we pass extra features via env
	MATURIN_DISABLE_DYNAMIC_METADATA=1 \
	$(MATURIN) build $(MATURIN_OPTS) --features "$(FEATURES_SPACES)"

.PHONY: bench
bench: ## Run criterion benches; filter with BENCH=substring
	@echo "==> cargo bench ($(PROFILE)) with features: $(FEATURES_SPACES)"
	$(CARGO) bench $(CARGO_PROFILE_FLAG) $(TARGET_FLAG) --features "$(FEATURES_SPACES)" -- $(if $(BENCH),$(BENCH),)

.PHONY: test
test: ## Run cargo tests; filter with TEST=substring
	@echo "==> cargo test ($(PROFILE)) with features: $(FEATURES_SPACES)"
	$(CARGO) test $(CARGO_PROFILE_FLAG) $(TARGET_FLAG) --features "$(FEATURES_SPACES)" -- $(if $(TEST),$(TEST),)

# -------------------------------------------------------------
# Quality-of-life targets
# -------------------------------------------------------------

.PHONY: check
check: ## Fast static checks
	@echo "==> cargo check"
	$(CARGO) check $(TARGET_FLAG) --features "$(FEATURES_SPACES)"
	@echo "==> cargo clippy"
	$(CARGO) clippy $(TARGET_FLAG) --features "$(FEATURES_SPACES)" -- -D warnings

.PHONY: fmt
fmt: ## Format sources
	@echo "==> cargo fmt"
	$(CARGO) fmt --all

.PHONY: clean
clean: ## Remove build artifacts
	@echo "==> cleaning target/ and dist/"
	rm -rf target dist

.PHONY: print-env
print-env:
	@echo "PROFILE=$(PROFILE)"
	@echo "FEATURES=$(FEATURES)"
	@echo "WITH_PY=$(if $(WITH_PY),$(WITH_PY),0)"
	@echo "TARGET=$(TARGET)"
	@echo "COMPAT=$(COMPAT)"
	@echo "INTERPRETERS=$(INTERPRETERS)"
	@echo "RUSTFLAGS=$(RUSTFLAGS)"
	@echo "ANIMICA_NATIVE_FORCE_CPU=$(ANIMICA_NATIVE_FORCE_CPU)"
	@echo "CARGO=$(CARGO)"
	@echo "MATURIN=$(MATURIN)"
	@echo "OS=$(UNAME_S)"

# -------------------------------------------------------------
# Platform hints (docs)
# -------------------------------------------------------------
# macOS (universal2) wheels:
#   make wheel
#
# Linux manylinux (default):
#   make wheel
#   # or stricter/newer policy:
#   make wheel COMPAT=manylinux_2_28
#
# Cross-compiling (example):
#   rustup target add aarch64-apple-darwin
#   make build TARGET=aarch64-apple-darwin
#
# Enabling optional acceleration:
#   make build FEATURES="simd isal c_keccak rayon"
#   make wheel FEATURES="simd isal"
#
# For CI you can force advertised CPU features (build.rs reads it):
#   make build ANIMICA_NATIVE_FORCE_CPU="avx2,sha3"
# -------------------------------------------------------------

