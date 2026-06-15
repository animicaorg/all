# Animica Miner GUI - Build Scripts

This directory contains the cross-compile pipeline for the three Animica Miner GUI
desktop binaries (Linux, macOS, Windows) plus a manifest generator and the GitHub
Actions release matrix that drives them.

## Overview

The build scripts use [PyInstaller](https://pyinstaller.org/) to bundle the Python
application and all its dependencies into a standalone, windowed app named
**AnimicaMiner** that runs without a Python installation.

### Files

| File | Purpose |
|------|---------|
| `animica-miner-gui.spec` | Unified, cross-platform PyInstaller spec. Windowed onedir build named `AnimicaMiner`; entry = `animica_miner_gui/main.py`; bundles PySide6/matplotlib/pydantic/httpx/jsonschema **and the `animica` unified-flow package**; excludes tkinter/tests. On macOS it also emits `AnimicaMiner.app` via `BUNDLE()`. |
| `qt_runtime_hook.py` | PyInstaller runtime hook so Qt finds its platform plugins inside the frozen bundle. |
| `build_linux.sh` | Linux build: PyInstaller onedir → AppImage (if `appimagetool` is present) else tarball. Always also produces a tarball. |
| `build_macos.sh` | macOS build: PyInstaller onedir → `.app` → `.dmg` (+ `.zip`). **macOS only.** |
| `build_windows.sh` | Windows build: PyInstaller onedir → `.zip`. Runs natively on Windows or under Wine. |
| `build_windows_wine.sh` + `Dockerfile.windows-wine` | Windows-from-Linux cross-build via a Wine + Windows-Python + PyInstaller Docker image. |
| `make_manifest.py` | Aggregates the per-artifact JSON lines into `downloads/manifest.json`. |

Each per-OS build script computes **SHA256 + size** for every artifact and appends
one JSON line to `dist/artifacts.jsonl`, which `make_manifest.py` aggregates.

### Cross-compile reality

PyInstaller is **not** a cross-compiler — it freezes the interpreter it runs on:

- **Linux** → built natively on Linux.
- **Windows** → built natively on the Windows CI runner, **or** cross-built from
  Linux by running a *Windows* CPython inside Wine (`build_windows_wine.sh`).
- **macOS** → **cannot** be cross-built from Linux. Apple's SDK and code-signing
  toolchain are not redistributable and do not run under Wine, so the `.app`/`.dmg`
  is produced **exclusively on the macOS CI runner** (`build_macos.sh`).

The robust path is the CI matrix (`ubuntu-latest` / `macos-latest` / `windows-latest`)
in `.github/workflows/gui-miner-release.yml`; the Wine image is the Linux→Windows
fallback for local builds without a Windows host.

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

#### Option 2: Cross-Compile from Linux (Wine + Docker)

No Windows host required. `build_windows_wine.sh` builds a Wine + Windows-Python +
PyInstaller image from `Dockerfile.windows-wine`, then runs `build_windows.sh`
inside it in Wine mode against the bind-mounted repo:

```bash
cd apps/miner-gui/build-scripts
./build_windows_wine.sh
```

Under the hood the container runs `WINE=wine PYTHON='wine python' build_windows.sh`,
so the exact same Windows build script is reused for native and cross builds.

**Output:**
- `dist/AnimicaMiner/AnimicaMiner.exe` - Windows onedir executable
- `dist/AnimicaMiner-{version}-windows-x64.zip` - ZIP package
- `dist/artifacts.jsonl` - per-artifact SHA256 + size (one JSON line)

> macOS cannot be cross-built from Linux (Apple toolchain). The `.app`/`.dmg` is
> produced only on the macOS CI runner via `build_macos.sh`.

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

The release pipeline lives at **`.github/workflows/gui-miner-release.yml`**. It is
triggered by `workflow_dispatch` or a `gui-v*` tag push and runs a matrix over
`ubuntu-latest` / `macos-latest` / `windows-latest`. Each matrix leg:

1. `actions/checkout@v4` + `actions/setup-python@v4`
2. installs the GUI package + PyInstaller (and the `animica` package, for bundling)
3. runs the matching OS build script (`build_linux.sh` / `build_macos.sh` / `build_windows.sh`)
4. `actions/upload-artifact@v4` (binaries + `dist/artifacts.jsonl`)

A final `release` job then:

1. downloads all matrix artifacts
2. runs `make_manifest.py` over every `artifacts.jsonl` to produce `downloads/manifest.json`
3. attaches all binaries + `manifest.json` to a GitHub Release via
   `softprops/action-gh-release@v2`

### Manifest generator

`make_manifest.py` reads the per-artifact JSON lines and writes
`downloads/manifest.json`:

```bash
python make_manifest.py \
  --inputs 'artifacts/**/artifacts.jsonl' \
  --out downloads/manifest.json \
  --repo "$GITHUB_REPOSITORY" \
  --tag  gui-v0.1.0 \
  --version 0.1.0
```

Output shape (consumed by the pool.animica.org downloads page):

```json
{
  "generated_at": "2026-06-15T12:00:00Z",
  "version": "0.1.0",
  "miners": [
    {
      "platform": "linux",
      "name": "AnimicaMiner",
      "version": "0.1.0",
      "filename": "AnimicaMiner-0.1.0-linux-x86_64.AppImage",
      "download_url": "https://github.com/<repo>/releases/download/<tag>/<filename>",
      "size_bytes": 12345678,
      "sha256": "deadbeef...",
      "min_os": "Ubuntu 20.04+ / glibc 2.31+"
    }
  ]
}
```

The `download_url` is templated against the GitHub Release (`--url-template` can
override the layout for object-storage hosting).

## Size Optimization

The default builds are optimized but can be further reduced:

1. **Enable UPX compression**: opt-in via `ANIMICA_GUI_UPX=1` (off by default; UPX is unreliable on macOS arm64)
2. **Remove debug symbols**: set `strip=True` in `animica-miner-gui.spec`
3. **Exclude unused modules**: add more to the `excludes` list in the spec
4. **One-file mode**: the spec uses onedir for fast startup; switch to a one-file EXE only if a single artifact is required

## Support

For issues with the build scripts:
1. Check the [Troubleshooting](#troubleshooting) section
2. Review PyInstaller documentation: https://pyinstaller.org/
3. Open an issue: https://github.com/animicaorg/all/issues

## License

See LICENSE.txt in the repository root.
