# Animica Miner-Wallet (Flutter)

A unified cross-platform mining and wallet application for the Animica network. Combines mining management (device configuration, real-time stats, logs) with wallet functionality (balance, transactions, QR codes) in a single Flutter application.

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
- **Balance & Info**: View address, balance, and nonce
- **Send Transactions**: Send ANM with QR code scanning
- **Transaction History**: View past transactions
- **Address Management**: Copy address, view QR code
- **Secure Storage**: Encrypted keystore with biometric support

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

## Building

### Android
```bash
flutter build apk --release
flutter build appbundle --release
```

### iOS (on macOS)
```bash
cd ios && pod install && cd ..
flutter build ipa --release
```

### macOS
```bash
flutter build macos --release
```

### Windows
```bash
flutter build windows --release
```

### Linux
```bash
flutter build linux --release
```

### Web
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
