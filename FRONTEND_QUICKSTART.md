# Frontend Applications - Quick Start Guide

This document provides build status, setup instructions, and environment configuration for all Animica frontend applications.

## Build Status Matrix

| Application | Status | Build Command | Notes |
|------------|--------|---------------|-------|
| **explorer-web** | ✅ Builds | `pnpm build` | Core pages working (Blocks, Tx, Address, Contracts, Network) |
| **studio-web** | ✅ Builds | `pnpm build` | Full functionality |
| **wallet-extension** | ✅ Builds | `pnpm build:chrome` or `pnpm build:firefox` | Browser extension |
| **miner-dashboard** | ✅ Builds | `pnpm build` | Dashboard for miners |
| **website** | ⚠️ Needs ENV | `pnpm build` | Requires PUBLIC_STUDIO_URL and other env vars |
| **wallet (Flutter)** | 📝 Documented | See Flutter section | Requires Flutter SDK >=3.24.0 |

## Prerequisites

### All Web Apps
- **Node.js**: >= 18.18.0
- **pnpm**: 9.0.0 (install: `npm install -g pnpm@9.0.0`)

### Flutter Wallet
- **Flutter SDK**: >=3.24.0
- **Dart SDK**: >=3.5.0 (included with Flutter)
- Platform-specific tools:
  - **Android**: Android Studio + Android SDK
  - **iOS**: Xcode (macOS only)
  - **Web**: Chrome or Edge

---

## Installation

### Monorepo Setup
```bash
# Clone repository
git clone https://github.com/animicaorg/all.git
cd all

# Install all dependencies (runs in workspace root)
pnpm install
```

---

## Explorer Web

**Purpose**: Live blockchain explorer for viewing blocks, transactions, addresses, and contracts.

### Environment Configuration

Create `explorer-web/.env.local` (copy from `.env.example`):

```env
# Required: JSON-RPC endpoint
VITE_RPC_URL=http://localhost:8545

# Required: Chain ID
VITE_CHAIN_ID=1337

# Optional: WebSocket endpoint for live updates
VITE_RPC_WS=ws://localhost:8546

# Optional: Studio services URL (verification, artifacts)
VITE_SERVICES_URL=http://localhost:8090
```

### Development

```bash
cd explorer-web
pnpm dev
# Opens at http://localhost:3001
```

### Build

```bash
cd explorer-web
pnpm build
# Output: dist/
```

### Preview

```bash
pnpm preview
```

### Features

**Working Pages:**
- ✅ Blocks list and detail
- ✅ Transaction list and detail
- ✅ Address lookup and history
- ✅ Contracts list
- ✅ Network status

**Temporarily Disabled (incomplete):**
- AICF dashboard
- Data Availability viewer
- Beacon/Randomness viewer
- Marketplace

---

## Studio Web

**Purpose**: Browser-based IDE for Python-VM smart contract development, testing, and deployment.

### Environment Configuration

Create `studio-web/.env.local`:

```env
VITE_RPC_URL=http://localhost:8545
VITE_CHAIN_ID=1337
VITE_SERVICES_URL=http://localhost:8090
```

### Development

```bash
cd studio-web
pnpm dev
```

### Build

```bash
cd studio-web
pnpm build
```

---

## Wallet Extension

**Purpose**: Browser extension wallet for Chrome and Firefox.

### Build

```bash
cd wallet-extension

# For Chrome/Edge
pnpm build:chrome
# Output: dist-chrome/

# For Firefox
pnpm build:firefox
# Output: dist-firefox/
```

### Load in Browser

**Chrome/Edge:**
1. Navigate to `chrome://extensions`
2. Enable "Developer mode"
3. Click "Load unpacked"
4. Select `wallet-extension/dist-chrome/`

**Firefox:**
1. Navigate to `about:debugging#/runtime/this-firefox`
2. Click "Load Temporary Add-on"
3. Select `wallet-extension/dist-firefox/manifest.json`

---

## Miner Dashboard

**Purpose**: Dashboard for monitoring mining operations and node health.

### Development

```bash
cd apps/miner-dashboard
pnpm dev
```

### Build

```bash
cd apps/miner-dashboard
pnpm build
```

---

## Website

**Purpose**: Marketing and documentation landing pages (Astro static site).

### Environment Configuration

Create `website/.env`:

```env
PUBLIC_STUDIO_URL=http://localhost:3000
PUBLIC_EXPLORER_URL=http://localhost:3001
PUBLIC_DOCS_URL=https://docs.animica.xyz
PUBLIC_RPC_URL=http://localhost:8545
PUBLIC_CHAIN_ID=1337
```

### Development

```bash
cd website
pnpm dev
```

### Build

```bash
cd website
pnpm build
```

---

## Flutter Wallet

**Purpose**: Native mobile/desktop wallet for iOS, Android, macOS, Windows, Linux, and Web.

### Prerequisites

#### Install Flutter

**macOS/Linux:**
```bash
# Download Flutter SDK
git clone https://github.com/flutter/flutter.git -b stable ~/flutter
export PATH="$PATH:$HOME/flutter/bin"

# Verify installation
flutter doctor
```

**Windows:**
Download from https://flutter.dev/docs/get-started/install/windows

### Setup

```bash
cd wallet

# Install dependencies
flutter pub get

# Check setup
flutter doctor
```

### Configuration

The wallet reads configuration from:
1. Environment variables (dev)
2. Flavor-specific configs (prod)

**Dev Configuration** (create `wallet/.env`):

```env
RPC_URL=http://localhost:8545
CHAIN_ID=1337
NETWORK_NAME=Animica Devnet
WS_URL=ws://localhost:8546
```

Configuration is loaded via `flutter_dotenv` package.

### Development

```bash
cd wallet

# Run on connected device/simulator
flutter run

# Run on specific device
flutter devices
flutter run -d <device-id>

# Run on Chrome (web)
flutter run -d chrome

# Run with dev flavor
flutter run --flavor dev
```

### Build

**Android APK:**
```bash
flutter build apk --flavor prod
# Output: build/app/outputs/flutter-apk/app-prod-release.apk
```

**iOS:**
```bash
flutter build ios --flavor prod --release
# Output: build/ios/iphoneos/Runner.app
```

**macOS:**
```bash
flutter build macos --release
# Output: build/macos/Build/Products/Release/animica_wallet.app
```

**Windows:**
```bash
flutter build windows --release
# Output: build/windows/runner/Release/
```

**Linux:**
```bash
flutter build linux --release
# Output: build/linux/x64/release/bundle/
```

**Web:**
```bash
flutter build web --release
# Output: build/web/
```

### Testing

```bash
cd wallet

# Run all tests
flutter test

# Run specific test
flutter test test/wallet_test.dart

# Run with coverage
flutter test --coverage
```

### Core Features

- ✅ Wallet creation/import (mnemonic)
- ✅ PQ (Dilithium3) key generation
- ✅ Primary address display
- ✅ Balance checking (via RPC)
- ✅ Transaction building and signing
- ✅ Network configuration UI
- ✅ Error handling for unreachable RPC
- ✅ Design tokens matching web apps

### Architecture

```
wallet/lib/
├── main.dart              # App entry point
├── app.dart               # Root widget
├── router/                # Go Router navigation
├── pages/                 # Screen widgets
├── widgets/               # Reusable components
├── services/              # RPC, storage, crypto
├── state/                 # Riverpod providers
├── crypto/                # PQ signing, key derivation
├── tx/                    # Transaction builders
└── utils/                 # Helpers
```

---

## Common Development Tasks

### Start Devnet

Required for most frontend development:

```bash
# From repository root
docker-compose -f docker-compose.dev.yml up
```

This starts:
- JSON-RPC on `http://localhost:8545`
- WebSocket on `ws://localhost:8546`
- Studio Services on `http://localhost:8090`

### Run All Frontend Tests

```bash
# Web apps
pnpm --filter "explorer-web" test
pnpm --filter "studio-web" test
pnpm --filter "wallet-extension" test
pnpm --filter "miner-dashboard" test

# Flutter wallet
cd wallet && flutter test
```

### Lint All Code

```bash
# Web apps
pnpm --filter "explorer-web" lint
pnpm --filter "studio-web" lint
pnpm --filter "wallet-extension" lint

# Flutter
cd wallet && dart analyze
```

### Build All Apps

```bash
# From repository root
pnpm --filter "explorer-web" build
pnpm --filter "studio-web" build
pnpm --filter "wallet-extension" build:chrome
pnpm --filter "miner-dashboard" build

cd wallet && flutter build web --release
```

---

## Troubleshooting

### pnpm install fails

```bash
# Update pnpm
npm install -g pnpm@9.0.0

# Clear cache
pnpm store prune

# Install with no frozen lockfile
pnpm install --no-frozen-lockfile
```

### Explorer-web build errors

The build process skips TypeScript checking for faster builds. Use this for type checking:

```bash
cd explorer-web
pnpm build:check  # Full type check + build
```

### Flutter doctor issues

```bash
flutter doctor -v  # Verbose output
flutter doctor --android-licenses  # Accept Android licenses
```

### RPC connection fails

1. Verify devnet is running: `docker ps`
2. Check firewall allows port 8545
3. Verify `.env` has correct `VITE_RPC_URL` / `RPC_URL`
4. Check browser console for CORS errors

### Flutter pubspec errors

```bash
cd wallet
flutter clean
flutter pub get
```

---

## Environment Variables Reference

### Explorer Web

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_RPC_URL` | `http://localhost:8545` | JSON-RPC endpoint |
| `VITE_CHAIN_ID` | `1337` | Chain identifier |
| `VITE_RPC_WS` | `ws://localhost:8546` | WebSocket endpoint |
| `VITE_SERVICES_URL` | `http://localhost:8090` | Studio services URL |

### Studio Web

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_RPC_URL` | `http://localhost:8545` | JSON-RPC endpoint |
| `VITE_CHAIN_ID` | `1337` | Chain identifier |
| `VITE_SERVICES_URL` | `http://localhost:8090` | Studio services base |

### Flutter Wallet

| Variable | Default | Description |
|----------|---------|-------------|
| `RPC_URL` | `http://localhost:8545` | JSON-RPC endpoint |
| `CHAIN_ID` | `1337` | Chain identifier |
| `NETWORK_NAME` | `Animica Devnet` | Display name |
| `WS_URL` | `ws://localhost:8546` | WebSocket (optional) |

### Website

| Variable | Required | Description |
|----------|----------|-------------|
| `PUBLIC_STUDIO_URL` | Yes | Studio app URL |
| `PUBLIC_EXPLORER_URL` | Yes | Explorer app URL |
| `PUBLIC_DOCS_URL` | Yes | Documentation URL |
| `PUBLIC_RPC_URL` | Yes | JSON-RPC endpoint |
| `PUBLIC_CHAIN_ID` | Yes | Chain identifier |

---

## Production Deployment

### Static Web Apps (Explorer, Studio, Website)

1. Build production bundle:
   ```bash
   pnpm build
   ```

2. Deploy `dist/` to CDN or static host:
   - Cloudflare Pages
   - Netlify
   - Vercel
   - AWS S3 + CloudFront
   - NGINX

3. Configure headers:
   - `index.html`: `Cache-Control: no-store`
   - `assets/*`: `Cache-Control: public, max-age=31536000, immutable`
   - CSP: Allow `connect-src` for RPC/WS origins

### Browser Extension

1. Build for target platform
2. Create ZIP of `dist-chrome/` or `dist-firefox/`
3. Submit to Chrome Web Store / Firefox Add-ons

### Flutter Wallet

**iOS:**
1. Build: `flutter build ipa --release --flavor prod`
2. Upload to App Store Connect via Xcode
3. Submit for review

**Android:**
1. Build: `flutter build appbundle --release --flavor prod`
2. Upload to Google Play Console
3. Submit for review

**Desktop:**
- Package with installers (DMG/MSI/DEB)
- Distribute via website or Microsoft Store / Mac App Store

---

## Additional Resources

- [Main README](./README.md)
- [Contributing Guide](./CONTRIBUTING.md)
- [Python VM Documentation](./vm_py/README.md)
- [SDK Documentation](./sdk/docs/USAGE.md)
- Flutter Wallet: [wallet/README.md](./wallet/README.md)
- Explorer Web: [explorer-web/README.md](./explorer-web/README.md)

---

## Summary of Recent Changes

### Explorer Web
- Added dependencies: `cborg`, `@noble/hashes`, `vite-tsconfig-paths`
- Fixed TypeScript configuration for ES2022
- Implemented `keccak256Hex` using @noble/hashes
- Fixed Zustand middleware imports
- Temporarily disabled incomplete pages (AICF, DA, Beacon, Marketplace)
- **Build now succeeds** ✅

### Website
- Added default export to `config/links.ts`

### All Apps
- Verified build status
- Documented environment variables
- Created comprehensive quickstart guide
