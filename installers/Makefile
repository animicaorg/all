# Animica Installers Makefile
# -----------------------------------------------------------------------------
# Targets:
#   make all                 -> wallet-all + explorer-all
#   make wallet-all          -> wallet-linux + wallet-macos + wallet-windows
#   make wallet-linux        -> build & zip MV3 extension (chrome + firefox) labeled "linux"
#   make wallet-macos        -> build & zip MV3 extension (chrome + firefox) labeled "macos"
#   make wallet-windows      -> build & zip MV3 extension (chrome + firefox) labeled "windows"
#   make explorer-all        -> explorer-prod (and explorer-dev)
#   make explorer-prod       -> build production Explorer (falls back to studio-web)
#   make explorer-dev        -> build dev/preview Explorer bundle (falls back to studio-web)
#   make clean               -> remove build artifacts
#
# Notes:
# - We prefer pnpm if available, otherwise npm. Yarn is used only if both are missing.
# - Artifacts land in installers/dist with SHA256SUMS alongside.
# - Optional signing: COSIGN_SIGN=1 COSIGN_KEY=... will cosign each produced zip/tgz.
# - Version is inferred from git describe --tags (fallback "0.0.0+local") and package.json
#   for per-app version labels if jq is available.

SHELL := bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c

# Root and output
ROOT       := $(abspath $(CURDIR)/..)
DIST_DIR   := $(CURDIR)/dist
TMP_DIR    := $(CURDIR)/.tmp

# Tools detection ----------------------------------------------------------------
HAS_PNPM := $(shell command -v pnpm >/dev/null 2>&1 && echo 1 || echo 0)
HAS_NPM  := $(shell command -v npm  >/dev/null 2>&1 && echo 1 || echo 0)
HAS_YARN := $(shell command -v yarn >/dev/null 2>&1 && echo 1 || echo 0)
HAS_JQ   := $(shell command -v jq   >/dev/null 2>&1 && echo 1 || echo 0)
HAS_ZIP  := $(shell command -v zip  >/dev/null 2>&1 && echo 1 || echo 0)
HAS_COSIGN := $(shell command -v cosign >/dev/null 2>&1 && echo 1 || echo 0)

# Choose package manager (pnpm > npm > yarn)
ifeq ($(HAS_PNPM),1)
  PM := pnpm
  PM_INSTALL := pnpm install --frozen-lockfile
else ifeq ($(HAS_NPM),1)
  PM := npm
  PM_INSTALL := npm ci
else ifeq ($(HAS_YARN),1)
  PM := yarn
  PM_INSTALL := yarn install --frozen-lockfile
else
  $(error No package manager found (pnpm/npm/yarn). Please install one.)
endif

# Git/versions -------------------------------------------------------------------
GIT_VERSION := $(shell git -C "$(ROOT)" describe --tags --always --dirty 2>/dev/null || echo "0.0.0+local")

define app_version
  if [[ "$(HAS_JQ)" == "1" && -f "$(1)/package.json" ]]; then
    jq -r '.version // empty' "$(1)/package.json"
  fi
endef

WALLET_DIR := $(ROOT)/wallet-extension
WALLET_VER := $(shell $(call app_version,$(WALLET_DIR)); echo $$([[ -z "$$REPLY" ]] && echo "$(GIT_VERSION)" || echo "$$REPLY"))
EXPLORER_DIR_FALLBACK := $(ROOT)/studio-web
EXPLORER_DIR := $(if $(wildcard $(ROOT)/explorer),$(ROOT)/explorer,$(EXPLORER_DIR_FALLBACK))
EXPLORER_VER := $(shell $(call app_version,$(EXPLORER_DIR)); echo $$([[ -z "$$REPLY" ]] && echo "$(GIT_VERSION)" || echo "$$REPLY"))

# Signing ------------------------------------------------------------------------
COSIGN_ENABLED ?= $(if $(and $(COSIGN_SIGN),$(COSIGN_KEY)),$(COSIGN_SIGN),)
COSIGN_ARGS ?=

define sign_if_enabled
  if [[ -n "$(COSIGN_ENABLED)" && "$(HAS_COSIGN)" == "1" ]]; then
    echo "Cosign: signing $1"
    cosign sign-blob $(COSIGN_ARGS) --key "$(COSIGN_KEY)" "$1" \
      --output-signature "$1.sig" \
      --output-certificate "$1.pem"
  else
    echo "Cosign: skip signing $1 (set COSIGN_SIGN=1 and COSIGN_KEY=... to enable)"
  fi
endef

# Helpers ------------------------------------------------------------------------
define ensure_zip
  if [[ "$(HAS_ZIP)" != "1" ]]; then
    echo "zip not found. Please install 'zip'." >&2
    exit 1
  fi
endef

define ensure_node
  node --version >/dev/null
  echo "Using $(PM) as package manager"
endef

define checksum
  local file="$1"
  sha256sum "$$file" >> "$(DIST_DIR)/SHA256SUMS"
endef

# Default meta targets -----------------------------------------------------------
.PHONY: all wallet-all explorer-all clean help
all: wallet-all explorer-all

help:
	@awk 'BEGIN{FS=":.*##"; printf "\n\033[1mAnimica installers targets\033[0m\n\n"} /^[a-zA-Z0-9_\-]+:.*?##/{printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

wallet-all: wallet-linux wallet-macos wallet-windows  ## Build & zip wallet for all OS labels

explorer-all: explorer-prod explorer-dev              ## Build explorer prod+dev bundles

# Wallet (MV3 extension) ---------------------------------------------------------
# Uses the repo's wallet-extension package scripts:
#   build:chrome, build:firefox
# Outputs:
#   installers/dist/wallet-<platform>-chrome-<ver>.zip
#   installers/dist/wallet-<platform>-firefox-<ver>.zip

.PHONY: wallet-linux wallet-macos wallet-windows
wallet-linux:  ## Build wallet zips labeled "linux"
	$(call ensure_node)
	$(call ensure_zip)
	mkdir -p "$(DIST_DIR)" "$(TMP_DIR)"
	cd "$(WALLET_DIR)"
	$(PM_INSTALL)
	$(PM) run build:chrome
	$(PM) run build:firefox
	cd "$(WALLET_DIR)/dist"
	zip -r "$(DIST_DIR)/wallet-linux-chrome-$(WALLET_VER).zip" chrome
	zip -r "$(DIST_DIR)/wallet-linux-firefox-$(WALLET_VER).zip" firefox
	$(call checksum,$(DIST_DIR)/wallet-linux-chrome-$(WALLET_VER).zip)
	$(call checksum,$(DIST_DIR)/wallet-linux-firefox-$(WALLET_VER).zip)
	$(call sign_if_enabled,$(DIST_DIR)/wallet-linux-chrome-$(WALLET_VER).zip)
	$(call sign_if_enabled,$(DIST_DIR)/wallet-linux-firefox-$(WALLET_VER).zip)

wallet-macos:  ## Build wallet zips labeled "macos"
	$(call ensure_node)
	$(call ensure_zip)
	mkdir -p "$(DIST_DIR)" "$(TMP_DIR)"
	cd "$(WALLET_DIR)"
	$(PM_INSTALL)
	$(PM) run build:chrome
	$(PM) run build:firefox
	cd "$(WALLET_DIR)/dist"
	zip -r "$(DIST_DIR)/wallet-macos-chrome-$(WALLET_VER).zip" chrome
	zip -r "$(DIST_DIR)/wallet-macos-firefox-$(WALLET_VER).zip" firefox
	$(call checksum,$(DIST_DIR)/wallet-macos-chrome-$(WALLET_VER).zip)
	$(call checksum,$(DIST_DIR)/wallet-macos-firefox-$(WALLET_VER).zip)
	$(call sign_if_enabled,$(DIST_DIR)/wallet-macos-chrome-$(WALLET_VER).zip)
	$(call sign_if_enabled,$(DIST_DIR)/wallet-macos-firefox-$(WALLET_VER).zip)

wallet-windows:  ## Build wallet zips labeled "windows"
	$(call ensure_node)
	$(call ensure_zip)
	mkdir -p "$(DIST_DIR)" "$(TMP_DIR)"
	cd "$(WALLET_DIR)"
	$(PM_INSTALL)
	$(PM) run build:chrome
	$(PM) run build:firefox
	cd "$(WALLET_DIR)/dist"
	zip -r "$(DIST_DIR)/wallet-windows-chrome-$(WALLET_VER).zip" chrome
	zip -r "$(DIST_DIR)/wallet-windows-firefox-$(WALLET_VER).zip" firefox
	$(call checksum,$(DIST_DIR)/wallet-windows-chrome-$(WALLET_VER).zip)
	$(call checksum,$(DIST_DIR)/wallet-windows-firefox-$(WALLET_VER).zip)
	$(call sign_if_enabled,$(DIST_DIR)/wallet-windows-chrome-$(WALLET_VER).zip)
	$(call sign_if_enabled,$(DIST_DIR)/wallet-windows-firefox-$(WALLET_VER).zip)

# Explorer (web app) -------------------------------------------------------------
# If repo contains ./explorer, use it; otherwise fall back to ./studio-web.
# Outputs:
#   installers/dist/explorer-<env>-<ver>.zip

.PHONY: explorer-prod explorer-dev
explorer-prod:  ## Build production Explorer bundle (or studio-web fallback)
	$(call ensure_node)
	$(call ensure_zip)
	mkdir -p "$(DIST_DIR)" "$(TMP_DIR)"
	cd "$(EXPLORER_DIR)"
	$(PM_INSTALL)
	# Prefer "build" script; allow "build:prod" alias if present
	if jq -e '.scripts["build:prod"]' package.json >/dev/null 2>&1; then \
	  $(PM) run build:prod; \
	else \
	  $(PM) run build; \
	fi
	cd "$(EXPLORER_DIR)"
	if [[ -d dist ]]; then SRC=dist; elif [[ -d build ]]; then SRC=build; else echo "No dist/build dir found" && exit 1; fi
	cd "$$SRC"
	zip -r "$(DIST_DIR)/explorer-prod-$(EXPLORER_VER).zip" .
	$(call checksum,$(DIST_DIR)/explorer-prod-$(EXPLORER_VER).zip)
	$(call sign_if_enabled,$(DIST_DIR)/explorer-prod-$(EXPLORER_VER).zip)

explorer-dev:  ## Build dev/preview Explorer bundle (same build with DEV flag)
	$(call ensure_node)
	$(call ensure_zip)
	mkdir -p "$(DIST_DIR)" "$(TMP_DIR)"
	cd "$(EXPLORER_DIR)"
	$(PM_INSTALL)
	# Use regular build but with a flag to toggle non-minified or dev settings if supported
	if jq -e '.scripts["build:dev"]' package.json >/dev/null 2>&1; then \
	  $(PM) run build:dev; \
	else \
	  NODE_ENV=development VITE_ENV=dev $(PM) run build || $(PM) run build; \
	fi
	cd "$(EXPLORER_DIR)"
	if [[ -d dist ]]; then SRC=dist; elif [[ -d build ]]; then SRC=build; else echo "No dist/build dir found" && exit 1; fi
	cd "$$SRC"
	zip -r "$(DIST_DIR)/explorer-dev-$(EXPLORER_VER).zip" .
	$(call checksum,$(DIST_DIR)/explorer-dev-$(EXPLORER_VER).zip)
	$(call sign_if_enabled,$(DIST_DIR)/explorer-dev-$(EXPLORER_VER).zip)

# Cleanup ------------------------------------------------------------------------
clean:  ## Remove installer artifacts
	rm -rf "$(DIST_DIR)" "$(TMP_DIR)"

# Pretty help footer -------------------------------------------------------------
.DEFAULT_GOAL := all
