# Animica Miner GUI - Build Scripts

This directory contains build scripts for creating standalone executables of the Animica Miner GUI for macOS, Windows, and Linux.

## Overview

The build scripts use [PyInstaller](https://pyinstaller.org/) to bundle the Python application and all its dependencies into standalone executables that can run without requiring a Python installation.

## Prerequisites

### All Platforms
- Git
- Python 3.10 or higher
- Sufficient disk space (~500MB for build artifacts)

### macOS
- macOS 10.15 (Catalina) or later
- Xcode Command Line Tools: `xcode-select --install`
- Homebrew (recommended): `brew install python@3.10`

### Windows
- Windows 10/11 or Windows Server 2022
- Python 3.10+ from [python.org](https://www.python.org/downloads/windows/)
- Git Bash or WSL for running the script

### Linux
- Ubuntu 20.04+, Debian 11+, Fedora 35+, or similar
- Python 3.10+ with development headers
- Qt dependencies (automatically installed by the script)

## Usage

### Building for macOS

Run on a Mac:

```bash
cd apps/miner-gui/build-scripts
./build_macos.sh
```

**Output:**
- `dist/Animica Miner GUI.app` - macOS application bundle
- `dist/Animica-Miner-GUI-{version}-macOS-{arch}.dmg` - Disk image installer

**Testing:**
```bash
open "dist/Animica Miner GUI.app"
```

`build_macos.sh` runs a packaged verification pass (build + simulate + RPC check) using the bundled app. To skip this step, set `ANIMICA_SKIP_VERIFY=1`.

### Building for Windows

#### Option 1: Native Windows Build

Run on Windows using Git Bash or PowerShell with bash:

```bash
cd apps/miner-gui/build-scripts
./build_windows.sh
```

#### Option 2: Cross-Compile from Mac/Linux

Requires Wine:

```bash
# Install Wine first
# macOS: brew install wine
# Ubuntu: sudo apt install wine

cd apps/miner-gui/build-scripts
./build_windows.sh --cross-compile
```

**Output:**
- `dist/Animica-Miner-GUI.exe` - Windows executable
- `dist/Animica-Miner-GUI-{version}-Windows-x64.zip` - ZIP package

**Testing:**
```bash
# On Windows:
./dist/Animica-Miner-GUI.exe

# With Wine (cross-compile):
wine dist/Animica-Miner-GUI.exe
```

### Building for Linux

Run on Linux:

```bash
cd apps/miner-gui/build-scripts
./build_linux.sh
```

**Output:**
- `dist/animica-miner-gui` - Standalone executable
- `dist/Animica-Miner-GUI-{version}-Linux-{arch}.tar.gz` - Tarball archive
- `dist/Animica-Miner-GUI-{version}-{arch}.AppImage` - AppImage (portable)

**Testing:**
```bash
./dist/animica-miner-gui
```

## Build Process

Each script follows these steps:

1. **Dependency Installation**: Installs PyInstaller and required Python packages
2. **Version Detection**: Reads version from `pyproject.toml`
3. **Spec File Generation**: Creates a PyInstaller spec file with proper configuration
4. **PyInstaller Build**: Bundles the application and dependencies
5. **Packaging**: Creates platform-specific installers (DMG, ZIP, tarball, AppImage)

## Output Structure

After building, you'll find artifacts in the `apps/miner-gui/dist/` directory:

```
dist/
├── macOS:
│   ├── Animica Miner GUI.app/
│   └── Animica-Miner-GUI-0.1.0-macOS-arm64.dmg
├── Windows:
│   ├── Animica-Miner-GUI.exe
│   └── Animica-Miner-GUI-0.1.0-Windows-x64.zip
└── Linux:
    ├── animica-miner-gui
    ├── Animica-Miner-GUI-0.1.0-Linux-x86_64.tar.gz
    └── Animica-Miner-GUI-0.1.0-x86_64.AppImage
```

## Configuration

The build scripts generate PyInstaller spec files with optimized settings:

- **Hidden imports**: Includes PySide6, matplotlib, pydantic, httpx
- **Excluded modules**: Removes unused tkinter, test, unittest
- **Compression**: UPX compression enabled for smaller binaries
- **Console**: Disabled (GUI-only application)
- **Icon**: Uses `logo.png` from the app directory

## Customization

### Changing the Version

Version is automatically read from `pyproject.toml`. To change it:

```bash
cd apps/miner-gui
# Edit pyproject.toml, change the version line:
# version = "0.2.0"
```

### Adding Application Icon

1. Place your icon file in the app directory:
   - macOS: `icon.icns`
   - Windows: `icon.ico`
   - Linux: `icon.png`

2. Update the spec file template in the build script to reference the icon:
   ```python
   icon='path/to/icon.ico'  # or .icns, .png
   ```

### Code Signing (Production)

For production releases, you should sign the executables:

#### macOS
```bash
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name" \
  "dist/Animica Miner GUI.app"

# Notarize with Apple
xcrun notarytool submit "dist/Animica-Miner-GUI-0.1.0-macOS-arm64.dmg" \
  --apple-id "your@email.com" \
  --password "app-specific-password" \
  --team-id "TEAMID"
```

#### Windows
```bash
signtool sign /f certificate.pfx /p password /tr http://timestamp.digicert.com \
  /td sha256 /fd sha256 "dist/Animica-Miner-GUI.exe"
```

#### Linux
AppImages can be signed with `appimagetool --sign`:
```bash
appimagetool --sign "dist/Animica-Miner-GUI-0.1.0-x86_64.AppImage"
```

## Troubleshooting

### macOS: "App is damaged and can't be opened"

This happens when running unsigned apps. To bypass for testing:
```bash
xattr -cr "dist/Animica Miner GUI.app"
```

### Windows: Antivirus False Positives

PyInstaller executables sometimes trigger antivirus warnings. For production:
1. Sign the executable with a code signing certificate
2. Submit to antivirus vendors for whitelisting

### Linux: Missing Libraries

If the executable fails with missing library errors:
```bash
# Check dependencies
ldd dist/animica-miner-gui

# Install missing packages
sudo apt install libxcb-xinerama0 libxcb-cursor0
```

### AppImage: "FUSE is not available"

AppImages require FUSE. If not available:
```bash
# Extract and run directly
./Animica-Miner-GUI-0.1.0-x86_64.AppImage --appimage-extract
./squashfs-root/AppRun
```

### Build Fails: ModuleNotFoundError

Ensure all dependencies are installed:
```bash
cd apps/miner-gui
pip install -e ".[dev]"
```

## CI/CD Integration

These scripts can be integrated into CI/CD pipelines:

### GitHub Actions Example

```yaml
name: Build Executables

on:
  push:
    tags:
      - 'v*'

jobs:
  build-macos:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Build
        run: |
          cd apps/miner-gui/build-scripts
          ./build_macos.sh
      - uses: actions/upload-artifact@v3
        with:
          name: macos-build
          path: apps/miner-gui/dist/*.dmg

  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Build
        shell: bash
        run: |
          cd apps/miner-gui/build-scripts
          ./build_windows.sh
      - uses: actions/upload-artifact@v3
        with:
          name: windows-build
          path: apps/miner-gui/dist/*.zip

  build-linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Build
        run: |
          cd apps/miner-gui/build-scripts
          ./build_linux.sh
      - uses: actions/upload-artifact@v3
        with:
          name: linux-build
          path: |
            apps/miner-gui/dist/*.tar.gz
            apps/miner-gui/dist/*.AppImage
```

## Size Optimization

The default builds are optimized but can be further reduced:

1. **Enable UPX compression**: Already enabled in spec files
2. **Remove debug symbols**: Add `strip=True` to PyInstaller spec
3. **Exclude unused modules**: Add more to `excludes` list
4. **One-file mode**: Change to single executable (increases startup time)

## Support

For issues with the build scripts:
1. Check the [Troubleshooting](#troubleshooting) section
2. Review PyInstaller documentation: https://pyinstaller.org/
3. Open an issue: https://github.com/animicaorg/all/issues

## License

See LICENSE.txt in the repository root.
