# Animica Miner-Wallet (Flutter)

A unified cross-platform mining and wallet application for the Animica network. Combines mining management (device configuration, real-time stats, logs) with wallet functionality (balance, transactions, QR codes) in a single Flutter application.

## Status

**Implementation: ~90% Complete** 🎉

- ✅ **Mining Features**: Fully implemented (dashboard, devices, pools, stats, logs)
- ✅ **Wallet UI**: Complete (balance, send form, receive QR, import, history)
- ⚠️ **Transaction Signing**: Requires PQ crypto implementation (use CLI wallet as workaround)
- ⚠️ **Transaction History**: Requires RPC methods on node (UI ready)

See [WALLET_IMPLEMENTATION_COMPLETE.md](./WALLET_IMPLEMENTATION_COMPLETE.md) for detailed status.

## Features

### Mining Features
- **Dashboard**: Real-time mining status, hashrate, difficulty, blocks found
- **Device Management**: Configure CPU/GPU/ASIC mining devices with auto-detection
- **Pool Support**: Solo mining and pool configuration
- **Configuration**: JSON config editor with schema validation
- **Logs**: Real-time log viewer with filtering and export
- **Stats/Graphs**: Hashrate visualization and mining statistics
- **Auto-start**: Optional auto-start mining on launch
- **System Tray**: Minimize to tray with notifications (desktop)

### Wallet Features
- **Balance & Info**: View address, balance, and nonce ✅
- **Send Transactions**: Send ANM with form validation and confirmation ⚠️ (requires PQ crypto)
- **Transaction History**: View past transactions ⚠️ (requires RPC methods)
- **Receive QR Code**: Display and share wallet address via QR code ✅
- **Address Management**: Copy address, import wallet ✅
- **Secure Storage**: Encrypted keystore with FlutterSecureStorage ✅

### Cross-Platform
- Android, iOS (mobile)
- macOS, Windows, Linux (desktop)
- Web (browser)

## Prerequisites

- **Flutter** ≥ 3.24 (Dart ≥ 3.5) — https://docs.flutter.dev/get-started/install
- **Platform SDKs**
  - Android: Android Studio + SDK
  - iOS/macOS: Xcode + CocoaPods
  - Windows: Visual Studio with Desktop development with C++
  - Linux: GTK3 dev packages

## Quick Start

```bash
# Install dependencies
flutter pub get

# Run on your platform
flutter run -d <device>

# Available devices: android, ios, macos, windows, linux, chrome
```

### Using Launch Scripts

Convenient bash scripts are provided for launching the app on each platform:

```bash
# Launch on Web
./run_web.sh

# Launch on macOS (macOS only)
./run_macos.sh

# Launch on Windows (Windows only)
./run_windows.sh

# Launch on Linux (Linux only)
./run_linux.sh
```

These scripts automatically check for dependencies and install them if needed.

## Building

### Using Build Scripts

Production-ready build scripts are provided for creating executables:

```bash
# Build for Web (creates tarball and zip in dist/)
./build_web.sh

# Build for macOS (creates .app bundle and DMG in dist/)
./build_macos.sh

# Build for Windows (creates executable and zip in dist/)
./build_windows.sh

# Build for Linux (creates tarball and AppImage in dist/)
./build_linux.sh
```

All build scripts:
- Clean previous builds
- Install dependencies automatically
- Create distribution packages in the `dist/` directory
- Include version information from `pubspec.yaml`

### Manual Building

You can also build manually using Flutter commands:

#### Android
```bash
flutter build apk --release
flutter build appbundle --release
```

#### iOS (on macOS)
```bash
cd ios && pod install && cd ..
flutter build ipa --release
```

#### macOS
```bash
flutter build macos --release
```

#### Windows
```bash
flutter build windows --release
```

#### Linux
```bash
flutter build linux --release
```

#### Web
```bash
flutter build web --release
```

## Configuration

Configuration is stored in platform-specific locations:
- Mobile: Secure storage (Keychain/Keystore)
- Desktop: `~/.animica/miner-wallet/config.json`
- Web: Local storage

### Network Configuration
Set the RPC URL and chain ID through the settings page or via `.env`:

```env
RPC_URL=https://rpc.clearblocker.com
CHAIN_ID=2
```

### Mining Configuration
- **Network**: RPC URL, chain ID
- **Miner**: Payout address, auto-start, blocks per batch
- **CPU**: Thread count, affinity
- **GPU**: Device selection, intensity
- **Pool**: Stratum server URL (optional)

### Wallet Setup

#### Using CLI Wallet (Recommended for now)
1. Create wallet with CLI:
   ```bash
   animica wallet create
   # Save your private key and address
   ```

2. Import into Flutter app:
   - Open Settings → Wallet Setup
   - Choose "Import Wallet"
   - Enter your address and private key
   - Tap "Import Wallet"

3. View balance and receive funds:
   - Go to Wallet tab
   - See your balance update in real-time
   - Tap QR icon to share your address

#### Transaction Operations
- **View Balance**: Automatic in Wallet tab
- **Receive Funds**: Tap QR icon, share address or QR code
- **Send Funds**: Use CLI wallet until PQ crypto is implemented in Flutter
  ```bash
  animica wallet send <to-address> <amount>
  ```

## Architecture

```
lib/
├── main.dart                    # App entry point
├── constants.dart               # App constants
├── pages/                       # UI pages
│   ├── mining/                  # Mining-related pages
│   │   ├── dashboard_page.dart
│   │   ├── devices_page.dart
│   │   ├── pools_page.dart
│   │   ├── logs_page.dart
│   │   └── stats_page.dart
│   ├── wallet/                  # Wallet-related pages
│   │   ├── wallet_page.dart
│   │   ├── send_page.dart
│   │   └── receive_page.dart
│   ├── settings/
│   │   ├── settings_page.dart
│   │   └── config_page.dart
│   └── onboarding/
│       └── wizard_page.dart
├── services/                    # Backend services
│   ├── rpc_service.dart         # RPC client
│   ├── miner_service.dart       # Mining process management
│   ├── device_service.dart      # Device detection
│   └── wallet_service.dart      # Wallet operations
├── state/                       # Riverpod providers
│   ├── app_state.dart
│   ├── miner_state.dart
│   └── wallet_state.dart
├── models/                      # Data models
│   ├── miner_config.dart
│   ├── device_info.dart
│   └── mining_event.dart
├── theme/                       # Theming
│   └── app_theme.dart
├── widgets/                     # Reusable widgets
│   ├── stat_card.dart
│   ├── device_card.dart
│   └── log_viewer.dart
└── utils/                       # Utilities
    ├── logger.dart
    └── formatters.dart
```

## Development

### Run Tests
```bash
flutter test
```

### Static Analysis
```bash
flutter analyze
```

### Format Code
```bash
dart format lib/
```

## Translation from Qt

This Flutter app is a translation of the Qt-based miner GUI (`apps/miner-gui`) with integrated wallet functionality. Key mappings:

### Qt → Flutter
- PySide6 widgets → Flutter widgets (Material/Cupertino)
- QThread → Dart isolates + async/await
- Qt signals/slots → Riverpod state management
- QSettings → SharedPreferences + FlutterSecureStorage
- Matplotlib → fl_chart package
- System tray (Qt) → system_tray package

### Backend Services
The original Qt backend modules are translated:
- `backend/config.py` → `models/miner_config.dart` + `services/config_service.dart`
- `backend/miner_runner.py` → `services/miner_service.dart`
- `backend/device_detection.py` → `services/device_service.dart`
- `backend/rpc_client.py` → `services/rpc_service.dart`

### UI Tabs
The Qt tabs are now Flutter pages:
- Dashboard tab → `pages/mining/dashboard_page.dart`
- Wallet tab → `pages/wallet/wallet_page.dart`
- Devices tab → `pages/mining/devices_page.dart`
- Pools tab → `pages/mining/pools_page.dart`
- Configuration tab → `pages/settings/config_page.dart`
- Logs tab → `pages/mining/logs_page.dart`
- Stats tab → `pages/mining/stats_page.dart`

## Security

- Private keys encrypted with FlutterSecureStorage (Keychain/Keystore)
- Config files with secure permissions
- No secrets in logs
- Biometric authentication support (mobile)
- Post-quantum crypto stubs (Dilithium3/SPHINCS+)

## License

See LICENSE.txt in the repository root.

## Support

- GitHub Issues: https://github.com/animicaorg/all/issues
- Documentation: https://docs.animica.org
