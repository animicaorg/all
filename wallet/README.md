# Animica Wallet (Flutter)

A cross-platform wallet for the Animica network (mobile, desktop, and web).  
It ships with deterministic Python-VM transaction builders, post-quantum key stubs, Riverpod state, and a thin RPC/WS client. This README covers features, structure, and how to run on Android, iOS, macOS, Windows, Linux, and Web.

---

## Features

- **Multi-platform:** Android, iOS, macOS, Windows, Linux, Web
- **Accounts & Keyring:** mnemonic create/import; PQ signature stubs (Dilithium3/SPHINCS+) with optional native hooks
- **Networking:** JSON-RPC (http) + WebSocket (auto-reconnect), light-client hooks
- **Transactions:** transfer/call/deploy builders + receipt waiter
- **DA/Randomness/AICF:** optional clients (feature-flagged)
- **Theming & i18n:** light/dark themes; en/es localization skeleton
- **State:** Riverpod stores for app, network, account, transactions
- **Dev tools:** in-app RPC console (feature-flag), pretty logs
- **Tests:** unit (bech32m, tx builder) + widget + integration smoke

---

## Project layout (high level)

wallet/
├─ lib/ # app code (see /lib/* by domain)
├─ assets/ # icons, images, fonts, json fixtures
├─ l10n/ # localizations
├─ test/ + integration_test/ # unit & integration tests
├─ android/ ios/ macos/ linux/ windows/ web/ # platform runners
├─ tool/ # helpers (env loader, l10n wrapper)
├─ .vscode/ # editor configs (optional)
├─ Makefile # common dev targets
└─ pubspec.yaml # deps & assets

markdown
Copy code

Key modules:
- `lib/services/*` — rpc/ws/tx/randomness/da/aicf/light-client
- `lib/state/*` — Riverpod providers (app/network/account/tx/subscriptions)
- `lib/tx/*` — tx types, builder, signbytes encoder
- `lib/keyring/*` — mnemonic, derivation stubs, secure storage
- `lib/pages/*` — onboarding, home, send/receive, contracts, settings

---

## Prerequisites

- **Flutter** ≥ 3.24 (Dart ≥ 3.5) — <https://docs.flutter.dev/get-started/install>
- **Platform SDKs**
  - Android: Android Studio + SDK / NDK (optional), a device/emulator
  - iOS/macOS: Xcode + CocoaPods (`sudo gem install cocoapods`)
  - Windows: Visual Studio with Desktop dev with C++
  - Linux: GTK3 dev packages (varies by distro)
- **Web:** recent Chrome/Edge/Firefox
- (Optional) **melos** for mono-repo tooling: `dart pub global activate melos`

---

## Environment & Flavors

Runtime is controlled by `.env` + **flavors**:

| Flavor | File                      | Typical use                        |
|-------:|---------------------------|------------------------------------|
| `dev`  | `.env` / `.env.example`   | local dev, hot reload              |
| `test` | (same keys)               | CI smoke & integration tests       |
| `prod` | (same keys)               | releases                           |

**.env keys (example):**
RPC_URL=https://rpc.clearblocker.com
CHAIN_ID=2
FEATURE_FLAGS=devTools,daClient,randomness

yaml
Copy code
> The app also understands the `chains/*.json` registry (if bundled) to pre-fill endpoints for `mainnet`, `testnet`, and `localnet`.

---

## Quickstart (any platform)

```bash
# From repo root or wallet/
flutter --version                 # verify SDK
flutter pub get                  # install deps
make analyze                     # static analysis (or: flutter analyze)
make test                        # run unit & widget tests
Running per platform
Android
bash
Copy code
flutter pub get
flutter run -d android
# Release APK/AAB
flutter build apk --flavor prod --release
flutter build appbundle --flavor prod --release
iOS (on macOS)
bash
Copy code
flutter pub get
cd ios && pod install && cd ..
flutter run -d ios
# Release (codesign required)
flutter build ipa --flavor prod --release
macOS
bash
Copy code
flutter run -d macos
flutter build macos --flavor prod --release
Windows
bash
Copy code
flutter config --enable-windows-desktop
flutter run -d windows
flutter build windows --release
Linux (Debian/Ubuntu example)
bash
Copy code
sudo apt-get update && sudo apt-get install -y clang cmake ninja-build pkg-config libgtk-3-dev liblzma-dev
flutter config --enable-linux-desktop
flutter run -d linux
flutter build linux --release
Web
bash
Copy code
flutter config --enable-web
flutter run -d chrome
flutter build web --release
Makefile targets
Common shortcuts (see wallet/Makefile):

make analyze – static analysis

make test – unit & widget tests

make build-web / build-android / build-ios / build-desktop

make l10n – regenerate localization code

make clean && flutter pub get – clean install

Configuration details
Networks: defaults are read from lib/constants.dart and can be overridden by .env.

Secure storage: flutter_secure_storage (Keychain/Keystore; file-based on desktop/web).

Key derivation: lib/keyring/* implements mnemonic + PQ stubs. For production PQ libs, wire an FFI in lib/native/pq_native.dart.

Light client: lib/services/light_client.dart exposes header checks; implementation is minimal and feature-flagged.

Internationalization (i18n)
Strings live in l10n/intl_*.arb.

After editing, run tool/gen_l10n.sh (or flutter gen-l10n) to regenerate.

Theming & assets
Colors map to lib/theme/colors.dart, derived from contrib/tokens/tokens.json.

App icons originate from contrib/app-icons/wallet/* (iOS/Android/desktop).

Testing
bash
Copy code
# Unit
flutter test test/unit

# Widget & integration smoke
flutter test
flutter test integration_test
CI should at minimum run analyze, test, and a flutter build smoke for target platforms.

Troubleshooting
Android “minSdkVersion” errors: ensure compileSdkVersion and minSdkVersion in android/app/build.gradle match Flutter’s recommended values.

iOS CocoaPods issues: cd ios && pod repo update && pod install.

Desktop build deps (Linux): install GTK3 dev headers as shown above.

Web CORS on RPC: use a local proxy or configure server CORS for your domain.

Security notes
Mnemonics are never logged. Debug logs redact secrets by default (see lib/utils/logger.dart).

PQ crypto in this repo is stubbed; production builds must link audited native libs and enable them via lib/native/pq_native.dart.

License
See repository root license and wallet/NOTICE for third-party notices.

Happy shipping 🚀
