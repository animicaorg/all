# Flutter Miner-Wallet Implementation Summary

## Overview

This document describes the translation of the Qt-based miner GUI to a unified Flutter application that combines mining management and wallet functionality.

## Translation Approach

### From Qt (PySide6) to Flutter

The original Qt application (`apps/miner-gui`) has been translated to Flutter (`apps/miner-wallet-flutter`) with the following architectural decisions:

1. **Unified Application**: Combined mining and wallet functionality in a single app
2. **Cross-Platform**: Targets mobile (Android/iOS), desktop (macOS/Windows/Linux), and web
3. **Modern Architecture**: Uses Riverpod for state management, go_router for navigation
4. **Consistent Design**: Follows Animica design tokens with dark theme by default

### Component Mapping

| Qt Component | Flutter Equivalent |
|--------------|-------------------|
| QWidget | Widget |
| QThread | Isolate / async/await |
| Qt Signals/Slots | Riverpod providers / callbacks |
| QSettings | SharedPreferences + FlutterSecureStorage |
| QPushButton | ElevatedButton / OutlinedButton |
| QLabel | Text widget |
| QLineEdit | TextField |
| QTextEdit | TextField (multiline) |
| QTableWidget | DataTable / ListView |
| matplotlib | fl_chart package |
| System tray (Qt) | system_tray package |

## Project Structure

```
apps/miner-wallet-flutter/
├── lib/
│   ├── main.dart                    # App entry point
│   ├── constants.dart               # Application constants
│   ├── pages/                       # UI screens
│   │   ├── mining/
│   │   │   ├── dashboard_page.dart  # Mining dashboard (Qt: dashboard.py)
│   │   │   ├── devices_page.dart    # Device configuration (Qt: devices.py)
│   │   │   ├── pools_page.dart      # Pool settings (Qt: pools.py)
│   │   │   ├── logs_page.dart       # Log viewer (Qt: logs.py)
│   │   │   └── stats_page.dart      # Statistics/graphs (Qt: stats.py)
│   │   ├── wallet/
│   │   │   ├── wallet_page.dart     # Wallet info & send (Qt: wallet.py)
│   │   │   └── receive_page.dart    # Receive/QR code
│   │   ├── settings/
│   │   │   ├── settings_page.dart   # Settings menu
│   │   │   └── config_page.dart     # JSON config editor (Qt: configuration.py)
│   │   └── onboarding/
│   │       └── wizard_page.dart     # First-run wizard (Qt: wizard.py)
│   ├── services/                    # Backend services
│   │   ├── rpc_service.dart         # RPC client (Qt: rpc_client.py)
│   │   ├── miner_service.dart       # Mining management (Qt: miner_runner.py)
│   │   ├── device_service.dart      # Device detection (Qt: device_detection.py)
│   │   └── config_service.dart      # Config management (Qt: config.py)
│   ├── state/                       # Riverpod providers
│   │   ├── app_state.dart           # Global app state
│   │   ├── miner_state.dart         # Mining state
│   │   └── wallet_state.dart        # Wallet state
│   ├── models/                      # Data models
│   │   ├── miner_config.dart        # Configuration model
│   │   ├── mining_event.dart        # Event types
│   │   └── device_info.dart         # Device information
│   ├── theme/
│   │   └── app_theme.dart           # Material3 theme
│   ├── widgets/                     # Reusable widgets
│   │   ├── stat_card.dart
│   │   ├── device_card.dart
│   │   └── log_viewer.dart
│   ├── utils/                       # Utilities
│   │   ├── logger.dart
│   │   └── formatters.dart
│   └── router/
│       └── app_router.dart          # Navigation setup
├── test/                            # Unit tests
├── assets/                          # Images, icons, fonts
├── pubspec.yaml                     # Dependencies
├── analysis_options.yaml            # Linter config
├── Makefile                         # Build commands
└── README.md                        # Documentation
```

## Features Implemented

### Core Functionality (Placeholder)

All UI scaffolding is in place with the following pages:

1. **Dashboard Page** - Mining status, chain info, hashrate, blocks found
2. **Wallet Page** - Address display, balance, send transactions
3. **Settings Page** - Navigation to all configuration sections

### Models & Data Structures

- `MinerConfig` - Complete configuration model with JSON serialization
- `MiningEvent` - Event types for mining updates
- `DeviceInfo` - CPU/GPU device information
- `NetworkConfig`, `MinerSettings`, `CpuConfig`, `GpuConfig`, `PoolConfig`, `UiConfig`

### Utilities

- Logger setup with structured output
- Formatters for ANM amounts, hashrate, duration, addresses
- Address validation

### Theme

- Dark theme with Animica design tokens
- Material3 design system
- Custom colors (teal primary, indigo secondary)
- Consistent spacing and typography

## Next Steps

To complete the implementation:

1. **Services Layer**:
   - Implement RPC client service
   - Implement miner process management service
   - Implement device detection service
   - Implement configuration persistence service

2. **State Management**:
   - Create Riverpod providers for app state
   - Create Riverpod providers for miner state
   - Create Riverpod providers for wallet state

3. **Complete UI Pages**:
   - Devices page (CPU/GPU configuration)
   - Pools page (pool settings)
   - Logs page (real-time log viewer)
   - Stats page (hashrate graphs)
   - Config editor page (JSON editor)
   - Wizard page (first-run setup)

4. **Integration**:
   - Connect UI to services via state providers
   - Implement real-time updates for mining events
   - Add WebSocket support for live data
   - Implement system tray (desktop platforms)

5. **Testing**:
   - Add service tests
   - Add widget tests
   - Add integration tests
   - Add platform-specific tests

6. **Build & Deploy**:
   - Set up CI/CD for all platforms
   - Create platform-specific build scripts
   - Generate installers (DMG, MSI, DEB, etc.)

## Key Differences from Qt Version

### Advantages

1. **Cross-Platform**: Single codebase for mobile, desktop, and web
2. **Modern UI**: Material Design 3 with smooth animations
3. **Better Performance**: Dart VM and native compilation
4. **Easier Maintenance**: Simpler async model than Qt threads
5. **Better Testing**: Built-in widget testing framework
6. **Hot Reload**: Faster development iteration

### Considerations

1. **System Integration**: Some desktop features require platform channels
2. **Process Management**: Need to use platform-specific APIs for subprocess management
3. **Native Libraries**: GPU detection may need platform channels or FFI

## Dependencies

Key Flutter packages used:

- `flutter_riverpod` - State management
- `go_router` - Navigation
- `fl_chart` - Charts and graphs
- `system_tray` - System tray support (desktop)
- `process_run` - Process management
- `flutter_secure_storage` - Secure storage
- `http` - HTTP client
- `web_socket_channel` - WebSocket client

## Building

```bash
# Install dependencies
flutter pub get

# Run on current platform
flutter run

# Build for specific platform
flutter build apk --release      # Android
flutter build ios --release      # iOS
flutter build macos --release    # macOS
flutter build windows --release  # Windows
flutter build linux --release    # Linux
flutter build web --release      # Web
```

## Testing

```bash
# Run all tests
flutter test

# Run with coverage
flutter test --coverage

# Static analysis
flutter analyze
```

## Architecture Patterns

### State Management

Using Riverpod for reactive state:

```dart
// Provider definition
final minerStatusProvider = StateProvider<MiningStatus>((ref) => MiningStatus.stopped);

// Usage in widget
final status = ref.watch(minerStatusProvider);
```

### Service Communication

Services expose streams for real-time updates:

```dart
class MinerService {
  Stream<MiningEvent> get events => _eventController.stream;
  Future<void> startMining() async { ... }
  Future<void> stopMining() async { ... }
}
```

### Configuration Persistence

Using shared preferences for config and secure storage for keys:

```dart
final configService = ConfigService();
await configService.save(config);
final config = await configService.load();
```

## Security Considerations

1. **Private keys** stored in FlutterSecureStorage (Keychain/Keystore)
2. **Config files** use secure permissions on desktop
3. **No secrets in logs** - logger redacts sensitive data
4. **Address validation** before transactions
5. **RPC timeout** to prevent hanging requests

## Performance Optimizations

1. **Lazy loading** for heavy widgets
2. **Efficient list rendering** with ListView.builder
3. **Stream debouncing** for high-frequency updates
4. **Image caching** for assets
5. **Background isolates** for heavy computation

## Compatibility

- **Flutter SDK**: ≥ 3.24.0
- **Dart SDK**: ≥ 3.5.0
- **Android**: API level 21+ (Android 5.0+)
- **iOS**: 12.0+
- **macOS**: 10.14+
- **Windows**: Windows 10+
- **Linux**: Ubuntu 20.04+, or equivalent
- **Web**: Modern browsers (Chrome, Firefox, Safari, Edge)
