# Miner-Wallet-Flutter Build & Launch Scripts

This directory contains 8 bash scripts for launching and building the Animica Miner-Wallet Flutter application across all supported platforms.

## Scripts Overview

### Launch Scripts (Run in Development Mode)

These scripts launch the application in development mode with hot-reload enabled:

1. **run_web.sh** - Launch on Web (Chrome)
2. **run_macos.sh** - Launch on macOS
3. **run_windows.sh** - Launch on Windows
4. **run_linux.sh** - Launch on Linux

### Build Scripts (Create Production Executables)

These scripts create production-ready executables and distribution packages:

5. **build_web.sh** - Build for Web deployment
6. **build_macos.sh** - Build macOS .app bundle and DMG installer
7. **build_windows.sh** - Build Windows executable and ZIP package
8. **build_linux.sh** - Build Linux executable, tarball, and AppImage

## Features

All scripts include:
- ✅ Automatic dependency checking and installation
- ✅ Platform validation (ensures scripts run on correct OS)
- ✅ Clean error handling with colored output
- ✅ Verbose logging of all operations
- ✅ Version extraction from `pubspec.yaml`
- ✅ Automatic cleaning of previous builds

## Usage

### Launch Scripts

```bash
# Make scripts executable (first time only)
chmod +x *.sh

# Launch on your platform
./run_web.sh       # Web (any OS with Chrome)
./run_macos.sh     # macOS only
./run_windows.sh   # Windows only
./run_linux.sh     # Linux only
```

Launch scripts will:
1. Check for Flutter SDK
2. Install dependencies if needed (`flutter pub get`)
3. Install platform-specific dependencies (e.g., CocoaPods for macOS)
4. Launch the app with hot-reload enabled

### Build Scripts

```bash
# Build for your platform
./build_web.sh       # Creates web deployment package
./build_macos.sh     # Creates .app bundle and DMG
./build_windows.sh   # Creates .exe and ZIP
./build_linux.sh     # Creates executable, tarball, and AppImage
```

Build scripts will:
1. Clean previous builds (`flutter clean`)
2. Install all dependencies
3. Build in release mode
4. Create distribution packages in `dist/` directory
5. Provide instructions for testing and deployment

## Output Locations

### Web Build
- **Build directory**: `build/web/`
- **Distribution**: `dist/Animica-Miner-Wallet-{version}-Web.tar.gz`
- **Distribution**: `dist/Animica-Miner-Wallet-{version}-Web.zip`

### macOS Build
- **App Bundle**: `dist/Animica Miner Wallet.app`
- **DMG Installer**: `dist/Animica-Miner-Wallet-{version}-macOS-{arch}.dmg`

### Windows Build
- **Executable**: `dist/Animica-Miner-Wallet-Windows/animica_miner_wallet.exe`
- **ZIP Package**: `dist/Animica-Miner-Wallet-{version}-Windows-x64.zip`

### Linux Build
- **Bundle**: `build/linux/x64/release/bundle/`
- **Tarball**: `dist/Animica-Miner-Wallet-{version}-Linux-{arch}.tar.gz`
- **AppImage**: `dist/Animica-Miner-Wallet-{version}-{arch}.AppImage`

## Requirements

### All Platforms
- Flutter SDK ≥ 3.24.0
- Dart SDK ≥ 3.5.0

### Platform-Specific

#### macOS
- macOS 10.15 or higher
- Xcode with Command Line Tools
- CocoaPods (`gem install cocoapods`)
- For DMG creation: `hdiutil` (included with macOS)

#### Windows
- Windows 10 or higher
- Visual Studio with "Desktop development with C++" workload
- Git Bash or WSL for running bash scripts

#### Linux
- Linux (x86_64 or aarch64)
- GTK3 development packages:
  ```bash
  # Ubuntu/Debian
  sudo apt-get install libgtk-3-dev libglib2.0-dev libblkid-dev liblzma-dev
  
  # Fedora/RHEL
  sudo dnf install gtk3-devel glib2-devel
  ```
- For AppImage: FUSE (`sudo apt-get install fuse`)

#### Web
- Any OS with Chrome or another modern browser
- Python 3 (for local testing: `python3 -m http.server`)

## Script Details

### Launch Scripts

#### run_web.sh
- Launches app in Chrome with hot-reload
- No platform restrictions (works on any OS)
- Default Flutter web debugging port: 8080

#### run_macos.sh
- Validates macOS platform
- Installs CocoaPods if needed
- Launches native macOS app window

#### run_windows.sh
- Validates Windows platform (MINGW/MSYS/CYGWIN)
- Launches native Windows app window
- Requires Visual Studio components

#### run_linux.sh
- Validates Linux platform
- Checks for GTK3 dependencies
- Launches native Linux app window

### Build Scripts

#### build_web.sh
- Builds optimized web bundle
- Creates both .tar.gz and .zip archives
- Output is ready for web server deployment
- Includes instructions for local testing

#### build_macos.sh
- Creates signed .app bundle (if certificates available)
- Generates DMG installer with drag-to-Applications
- Includes codesigning instructions
- Detects architecture (x86_64 or arm64)

#### build_windows.sh
- Creates standalone .exe with all dependencies
- Packages everything in a ZIP file
- Includes DLL dependencies automatically
- Provides code signing instructions

#### build_linux.sh
- Creates executable bundle with shared libraries
- Generates portable tarball
- Attempts to create AppImage (if tools available)
- Detects architecture (x86_64 or aarch64)
- Includes system installation instructions

## Examples

### Development Workflow
```bash
# Day-to-day development on Linux
./run_linux.sh

# Make changes, hot-reload happens automatically
# Ctrl+C to stop when done
```

### Production Build Workflow
```bash
# Build for all platforms (run on each platform)

# On macOS:
./build_macos.sh
./build_web.sh

# On Windows:
./build_windows.sh

# On Linux:
./build_linux.sh

# Distribute the files from dist/ directory
```

### Testing a Build
```bash
# After building, test the executable

# macOS
open "dist/Animica Miner Wallet.app"

# Windows
"dist/Animica-Miner-Wallet-Windows/animica_miner_wallet.exe"

# Linux
./dist/Animica-Miner-Wallet-*-Linux-*.AppImage
# or
./build/linux/x64/release/bundle/animica_miner_wallet

# Web (local testing)
cd build/web && python3 -m http.server 8000
# Then open http://localhost:8000
```

## Troubleshooting

### "Flutter SDK not found"
Install Flutter from https://docs.flutter.dev/get-started/install

### "This script must be run on {platform}"
You're trying to run a platform-specific script on the wrong OS. Use the appropriate script for your platform.

### macOS: "CocoaPods not installed"
```bash
sudo gem install cocoapods
```

### Windows: "Visual Studio components not found"
Install Visual Studio with the "Desktop development with C++" workload.

### Linux: "Missing GTK dependencies"
```bash
sudo apt-get install libgtk-3-dev libglib2.0-dev libblkid-dev liblzma-dev
```

### Build fails with "version not found"
Ensure `pubspec.yaml` has a valid `version:` field.

### AppImage creation fails on Linux
Install appimagetool dependencies or use the tarball instead.

## Code Signing & Distribution

### macOS
```bash
# Sign the app
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name" \
  "dist/Animica Miner Wallet.app"

# Notarize for distribution (store password in keychain first with --store-password-in-keychain)
xcrun notarytool submit "dist/Animica-Miner-Wallet-*.dmg" \
  --apple-id your@email.com --keychain-profile "notary-profile" --team-id TEAMID

# Or use environment variable:
# xcrun notarytool submit "dist/Animica-Miner-Wallet-*.dmg" \
#   --apple-id your@email.com --password "$NOTARY_PASSWORD" --team-id TEAMID
```

### Windows
```bash
# Sign the executable (use environment variable or certificate store)
signtool sign /f certificate.pfx /p "$CERT_PASSWORD" \
  /tr http://timestamp.digicert.com /td SHA256 /fd SHA256 \
  "dist/Animica-Miner-Wallet-Windows/animica_miner_wallet.exe"

# Or use Windows certificate store (more secure):
# signtool sign /sha1 CERT_THUMBPRINT \
#   /tr http://timestamp.digicert.com /td SHA256 /fd SHA256 \
#   "dist/Animica-Miner-Wallet-Windows/animica_miner_wallet.exe"
```

### Linux
AppImage is self-contained and doesn't require signing, but you can sign the package:
```bash
gpg --detach-sign "dist/Animica-Miner-Wallet-*.AppImage"
```

## CI/CD Integration

These scripts can be integrated into CI/CD pipelines:

```yaml
# GitHub Actions example
- name: Build macOS
  run: |
    cd apps/miner-wallet-flutter
    ./build_macos.sh
    
- name: Upload DMG
  uses: actions/upload-artifact@v3
  with:
    name: macos-dmg
    path: apps/miner-wallet-flutter/dist/*.dmg
```

## License

See LICENSE.txt in the repository root.

## Support

For issues or questions:
- GitHub Issues: https://github.com/animicaorg/all/issues
- Documentation: https://docs.animica.org
