# Quick Reference: Miner GUI Build Scripts

## Location
```
apps/miner-gui/build-scripts/
```

## Build Commands

### macOS (on Mac)
```bash
cd apps/miner-gui/build-scripts
./build_macos.sh
```
**Output**: `dist/AnimicaMiner.app`, `.dmg`, `.zip`

### Windows (on Windows)
```bash
cd apps/miner-gui/build-scripts
./build_windows.sh
```
**Output**: `dist/AnimicaMiner/AnimicaMiner.exe` and `.zip`

### Windows (cross-compile from Linux, Docker + Wine)
```bash
cd apps/miner-gui/build-scripts
./build_windows_wine.sh
```
**Requires**: Docker (builds a Wine + Windows-Python + PyInstaller image)
**Note**: macOS cannot be cross-built from Linux (Apple toolchain) — use the macOS CI runner.

### Linux (on Linux)
```bash
cd apps/miner-gui/build-scripts
./build_linux.sh
```
**Output**: `dist/AnimicaMiner/`, `.tar.gz`, and `.AppImage` (if `appimagetool` present)

### Manifest (after collecting `dist/artifacts.jsonl` from each OS)
```bash
python make_manifest.py --inputs 'artifacts/**/artifacts.jsonl' \
  --out downloads/manifest.json --repo OWNER/REPO --tag gui-v0.1.0
```
**Output**: `downloads/manifest.json`

## Prerequisites

| Platform | Requirements |
|----------|-------------|
| macOS | macOS 10.15+, Python 3.10+, Xcode CLT |
| Windows | Windows 10+, Python 3.10+, Git Bash |
| Linux | Ubuntu 20.04+/Debian 11+, Python 3.10+ |

## Documentation

- **Full Guide**: `apps/miner-gui/build-scripts/README.md`
- **Implementation Summary**: `MINER_GUI_BUILD_SCRIPTS_SUMMARY.md`
- **Main README**: `apps/miner-gui/README.md`

## Quick Troubleshooting

### macOS: "App is damaged"
```bash
xattr -cr "dist/Animica Miner GUI.app"
```

### Windows: Missing Python (Wine)
Install Python for Windows in Wine first

### Linux: Missing libraries
```bash
sudo apt install libxcb-xinerama0 libxcb-cursor0
```

## What You Get

- **Standalone executables** - No Python needed
- **Platform-specific installers** - DMG, ZIP, AppImage
- **Professional packaging** - Ready for distribution
- **Cross-platform support** - All major desktop OSes

## Support

See `apps/miner-gui/build-scripts/README.md` for detailed troubleshooting and advanced options.
