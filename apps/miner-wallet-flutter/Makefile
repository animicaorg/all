# Makefile for Animica Miner-Wallet Flutter App

.PHONY: help install clean test analyze format run build-all

help:
	@echo "Animica Miner-Wallet Flutter - Development Commands"
	@echo ""
	@echo "  make install       - Install dependencies"
	@echo "  make clean         - Clean build artifacts"
	@echo "  make test          - Run tests"
	@echo "  make analyze       - Run static analysis"
	@echo "  make format        - Format code"
	@echo "  make run           - Run on default device"
	@echo "  make build-all     - Build for all platforms"
	@echo ""

install:
	flutter pub get

clean:
	flutter clean
	rm -rf build/

test:
	flutter test

analyze:
	flutter analyze

format:
	dart format lib/ test/

run:
	flutter run

# Platform-specific builds
build-android:
	flutter build apk --release

build-ios:
	flutter build ipa --release

build-macos:
	flutter build macos --release

build-windows:
	flutter build windows --release

build-linux:
	flutter build linux --release

build-web:
	flutter build web --release

build-all: build-android build-macos build-windows build-linux build-web
	@echo "All platform builds complete"
