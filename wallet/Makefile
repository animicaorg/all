# Animica Wallet — Makefile
# Common developer tasks for Flutter app.
#
# Usage examples:
#   make                # shows help
#   make setup          # flutter pub get + doctor
#   make analyze        # static analysis
#   make test           # run all tests
#   make run DEVICE=chrome
#   make build-android FLAVOR=prod
#   make build-ios     FLAVOR=prod
#   make build-web
#   make icons splash   # regenerate launcher icons & splash

SHELL := /usr/bin/env bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c
MAKEFLAGS += --no-builtin-rules
.DEFAULT_GOAL := help

# -------- Parameters --------
FLAVOR ?= dev
DEVICE ?= chrome

# -------- Helpers --------
define ensure
	@command -v $(1) >/dev/null 2>&1 || { echo "✖ Required tool not found: $(1)"; exit 127; }
endef

# Load .env into subshells if present
define with_env
	if [ -f .env ]; then set -a; source .env; set +a; fi; \
	$(1)
endef

# -------- Meta --------
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_\-]+:.*?## ' $(MAKEFILE_LIST) \
		| sed -E 's/:.*?## /: /' \
		| awk 'BEGIN{print "\nAnimica Wallet — common tasks\n"} {printf "  \033[36m%-22s\033[0m %s\n", $$1, substr($$0, index($$0,$$2))} END{print ""}'

# -------- Setup & hygiene --------
.PHONY: setup
setup: ## flutter pub get + flutter doctor
	$(call ensure,flutter)
	flutter --version
	flutter pub get
	flutter doctor -v

.PHONY: pubget
pubget: ## flutter pub get only
	$(call ensure,flutter)
	flutter pub get

.PHONY: clean
clean: ## flutter clean + remove build caches
	$(call ensure,flutter)
	flutter clean
	rm -rf .dart_tool build

.PHONY: analyze
analyze: ## Static analysis
	$(call ensure,flutter)
	flutter analyze

.PHONY: format
format: ## dart format (apply fixes)
	$(call ensure,dart)
	dart format --fix .

.PHONY: fix
fix: ## dart fix (safe automated fixes)
	$(call ensure,dart)
	dart fix --apply

# -------- Tests --------
.PHONY: test
test: ## Run unit & widget tests
	$(call ensure,flutter)
	flutter test

.PHONY: test-unit
test-unit: ## Run unit tests only
	$(call ensure,flutter)
	flutter test test/unit

.PHONY: test-integration
test-integration: ## Run integration tests
	$(call ensure,flutter)
	flutter test integration_test

# -------- Run (dev) --------
.PHONY: run
run: ## Run app on a device (DEVICE=chrome|android|ios|macos|linux|windows)
	$(call ensure,flutter)
	$(call with_env, flutter run -d $(DEVICE))

# Convenience run targets
.PHONY: run-web run-android run-ios run-macos run-linux run-windows
run-web:      ## Run on Chrome (web)
	$(MAKE) run DEVICE=chrome
run-android:  ## Run on Android
	$(MAKE) run DEVICE=android
run-ios:      ## Run on iOS
	$(MAKE) run DEVICE=ios
run-macos:    ## Run on macOS
	$(MAKE) run DEVICE=macos
run-linux:    ## Run on Linux
	$(MAKE) run DEVICE=linux
run-windows:  ## Run on Windows
	$(MAKE) run DEVICE=windows

# -------- Build (release) --------
.PHONY: build-web
build-web: ## Build web release
	$(call ensure,flutter)
	$(call with_env, flutter build web --release)

.PHONY: build-android
build-android: ## Build Android APK (FLAVOR=dev|test|prod)
	$(call ensure,flutter)
	$(call with_env, flutter build apk --flavor $(FLAVOR) --release)

.PHONY: build-aab
build-aab: ## Build Android App Bundle (AAB) (FLAVOR=dev|test|prod)
	$(call ensure,flutter)
	$(call with_env, flutter build appbundle --flavor $(FLAVOR) --release)

.PHONY: build-ios
build-ios: ## Build iOS IPA (requires codesign) (FLAVOR=dev|test|prod)
	$(call ensure,flutter)
	$(call with_env, flutter build ipa --flavor $(FLAVOR) --release)

.PHONY: build-macos
build-macos: ## Build macOS app
	$(call ensure,flutter)
	$(call with_env, flutter build macos --release)

.PHONY: build-windows
build-windows: ## Build Windows app
	$(call ensure,flutter)
	$(call with_env, flutter build windows --release)

.PHONY: build-linux
build-linux: ## Build Linux app
	$(call ensure,flutter)
	$(call with_env, flutter build linux --release)

.PHONY: build-desktop
build-desktop: ## Build all available desktop targets
	@echo "→ Building desktop targets that are enabled"
	@if flutter config | grep -q 'enable-macos-desktop.*true'; then $(MAKE) build-macos; fi
	@if flutter config | grep -q 'enable-windows-desktop.*true'; then $(MAKE) build-windows; fi
	@if flutter config | grep -q 'enable-linux-desktop.*true'; then $(MAKE) build-linux; fi

# -------- Assets & localization --------
.PHONY: l10n
l10n: ## Generate localization code
	$(call ensure,flutter)
	if [ -x tool/gen_l10n.sh ]; then bash tool/gen_l10n.sh; else flutter gen-l10n; fi

.PHONY: icons
icons: ## Regenerate launcher icons (uses flutter_launcher_icons)
	$(call ensure,flutter)
	flutter pub run flutter_launcher_icons

.PHONY: splash
splash: ## Regenerate splash screens (uses flutter_native_splash)
	$(call ensure,flutter)
	flutter pub run flutter_native_splash:create

# -------- CI convenience --------
.PHONY: ci
ci: ## CI: analyze + test + web build
	$(MAKE) analyze
	$(MAKE) test
	$(MAKE) build-web
