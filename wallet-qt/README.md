# Animica Wallet (Qt Desktop)

A cross-platform desktop wallet for Animica blockchain with embedded node support.

## Overview

This Qt-based wallet application bundles and controls an Animica node, providing a seamless "just works" experience without requiring users to manually manage the node. The wallet communicates with the node via localhost-only RPC, ensuring security by default.

## Features

### Current (v0.1.0)

- ✅ Embedded Animica node management
- ✅ Standalone Python mode (no Docker required)
- ✅ Network selection (mainnet/testnet/devnet)
- ✅ Node lifecycle control (start/stop/restart)
- ✅ Health monitoring via RPC ping
- ✅ Sync progress display
- ✅ Live log viewer
- ✅ Diagnostics export
- ✅ Port conflict detection and auto-increment
- ✅ Lock file to prevent multiple instances
- ✅ Cross-platform path management
- ✅ **Configurable data directory** (macOS/Windows/Linux)
- ✅ **Wallet import/export** (wallets.json)
- ✅ **Network isolation** (prevents mixing mainnet/testnet data)
- ✅ **Automatic backups** (timestamped)
- ✅ **Atomic file operations** (safe wallet imports)

### Planned (Future Releases)

- ⏳ Key management and wallet creation (UI)
- ⏳ Send/receive transactions
- ⏳ Balance display
- ⏳ Transaction history
- ⏳ Address book
- ⏳ Contract interaction
- ⏳ Settings UI (advanced node config)
- ⏳ Packaged installers (AppImage, DMG, MSI)

## Architecture

See [docs/architecture.md](docs/architecture.md) for detailed architecture decisions and module design.

**Key Decisions:**
- **Sidecar Process**: Node runs as separate child process (via `python -m rpc`)
- **Localhost-only RPC**: All communication over `127.0.0.1` (no external exposure)
- **No Docker Dependency**: Uses standalone Python RPC server for simplicity

## Prerequisites

### Required

- **Qt 6.2+** (Core, Widgets, Network)
- **CMake 3.16+**
- **C++ compiler** with C++17 support (GCC 9+, Clang 10+, MSVC 2019+)
- **Python 3.11+** (for running the embedded node)
- **Animica dependencies** (installed via `./setup.sh` in repo root)

### Optional

- **ninja** (faster builds than make)

## Building

The wallet uses a **cross-platform CMake build system** that automatically builds and bundles the Animica node. You don't need to install the node separately.

### Quick Start

Use the platform-specific build scripts for the easiest experience:

#### Linux

```bash
cd wallet-qt
./scripts/build-linux.sh
```

#### macOS

```bash
cd wallet-qt
./scripts/build-mac.sh
```

#### Windows

```powershell
cd wallet-qt
.\scripts\build-windows.ps1
```

The build scripts will:
1. Check for all prerequisites (Qt, CMake, Python, compiler)
2. Provide actionable error messages if anything is missing
3. Build the wallet Qt application
4. Build and bundle the Animica node automatically
5. Create a complete, runnable distribution

### Build Options

All scripts support the following options:

- `--debug` (or `-Debug` on Windows): Build in Debug mode instead of Release
- `--clean` (or `-Clean`): Delete the build directory before building
- `--qt <path>` (or `-QtPath`): Override Qt installation path
- `--jobs <n>` (or `-Jobs`): Set number of parallel build jobs

Example:
```bash
./scripts/build-linux.sh --clean --jobs 8
```

### Manual CMake Build

If you prefer to use CMake directly:

```bash
cd wallet-qt
mkdir -p build && cd build

# Configure (Qt will be auto-detected or set CMAKE_PREFIX_PATH)
cmake .. -DCMAKE_BUILD_TYPE=Release

# Or specify Qt location
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/path/to/qt6

# Build
cmake --build . -j $(nproc)

# Output will be in build/bin/
```

### Prerequisites

The build requires:

- **CMake 3.16+**
- **Qt 6.2+** (or Qt 5.15+)
- **Python 3.10+** with venv module
- **C++17 compiler**:
  - Linux: GCC 9+ or Clang 10+
  - macOS: Xcode Command Line Tools
  - Windows: Visual Studio 2019+ or MinGW-w64

#### Installing Prerequisites

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y \
    build-essential \
    cmake \
    qt6-base-dev \
    qt6-tools-dev \
    libqt6network6 \
    python3.11 \
    python3.11-venv \
    python3-pip
```

**macOS:**
```bash
brew install qt@6 cmake python@3.11
export CMAKE_PREFIX_PATH="$(brew --prefix qt@6)"
```

**Windows:**
- Qt 6: https://www.qt.io/download
- CMake: https://cmake.org/download/
- Python 3.11+: https://www.python.org/downloads/
- Visual Studio 2019+: https://visualstudio.microsoft.com/downloads/

### What Gets Built

The build system creates:

1. **Wallet executable**:
   - Linux: `build/linux/bin/animica-wallet`
   - macOS: `build/mac/bin/AnimicaWallet.app`
   - Windows: `build/windows/bin/Release/animica-wallet.exe`

2. **Bundled node** (automatically included):
   - Complete Python virtual environment with all dependencies
   - All Animica node modules (rpc, core, consensus, execution, etc.)
   - Platform-specific wrapper scripts

The wallet will automatically use the bundled node at runtime.

## Running

After building, you can run the wallet:

**Linux:**
```bash
./build/linux/bin/animica-wallet
```

**macOS:**
```bash
open ./build/mac/bin/AnimicaWallet.app
# or
./build/mac/bin/AnimicaWallet.app/Contents/MacOS/AnimicaWallet
```

**Windows:**
```powershell
.\build\windows\bin\Release\animica-wallet.exe
```

## Usage

### Starting the Node

1. Select network from dropdown (Devnet recommended for testing)
2. Click "Start Node"
3. Wait for status to change to "Running" (takes ~5-30 seconds)
4. Node logs will appear in the log viewer

### Stopping the Node

- Click "Stop Node" button
- Or close the wallet application (node will be stopped automatically)

### Viewing Diagnostics

- Click "Diagnostics" button
- Diagnostic info is copied to clipboard and displayed in a message box
- Includes: node state, PID, ports, network, recent logs

### Opening Logs Folder

- Menu: Node → Open Logs Folder
- Or call `NodeManager::openLogsFolder()`

## Directory Structure

The wallet uses OS-appropriate application data directories:

- **macOS**: `~/Library/Application Support/AnimicaWallet/`
- **Windows**: `%APPDATA%\AnimicaWallet\`
- **Linux**: `~/.local/share/AnimicaWallet/`

Structure:
```
AnimicaWallet/
├── node/           # Node data (chain DB, P2P state)
│   ├── chain-1/    # Mainnet
│   ├── chain-2/    # Testnet
│   └── chain-1337/ # Devnet
├── wallet/         # Wallet data (future: keys, accounts)
├── logs/           # All logs
│   ├── wallet.log
│   └── node-*.log
└── run/            # Runtime state
    ├── node.json
    ├── node.lock
    └── node.pid
```

## Troubleshooting

### Node Won't Start

**Symptom**: "Failed to start node process" error

**Solutions**:
1. The wallet should use the bundled node automatically. Check diagnostics to see which Python is being used.
2. If using system Python, ensure it's 3.10+ or rebuild the wallet to use the bundled node.
3. Check if port is in use: `lsof -i :8545` (Linux/macOS) or `netstat -ano | findstr :8545` (Windows)
4. View diagnostics: Click "Diagnostics" button
5. Check that the bundled node exists:
   - Linux: `ls -la build/linux/bin/node/venv/bin/python`
   - macOS: `ls -la build/mac/bin/AnimicaWallet.app/Contents/Resources/node/venv/bin/python`
   - Windows: `dir build\windows\bin\node\venv\Scripts\python.exe`

### Node Crashes on Startup

**Symptom**: State changes to "Error" shortly after starting

**Solutions**:
1. Check node logs: Menu → Node → Open Logs Folder
2. Ensure data directory is writable
3. Try a different network (Devnet recommended)
4. Clear node data: Delete `AnimicaWallet/node/chain-<ID>/` folder

### Health Check Timeout

**Symptom**: "Node failed to become ready (timeout)" after 30 seconds

**Solutions**:
1. Node may be starting but RPC not ready yet (increase timeout in code if needed)
2. Check if Python process is running: `ps aux | grep python` or Task Manager
3. Verify RPC port is listening: `lsof -i :<port>` or `netstat -ano | findstr :<port>`
4. Check node logs for errors

### Multiple Instance Error

**Symptom**: "Node is already running (lock file exists)"

**Solutions**:
1. Stop the other instance of the wallet
2. Or manually remove lock file: `AnimicaWallet/run/node.lock`
3. Ensure no orphaned processes: `pkill -f "python -m rpc"`

## Development

### Code Structure

```
wallet-qt/
├── CMakeLists.txt           # CMake build configuration
├── src/
│   ├── main.cpp             # Application entry point
│   ├── platform/            # Cross-platform abstractions
│   │   └── AppPaths.*       # Path resolution
│   ├── rpc/                 # RPC client
│   │   └── AnimicaRpcClient.*
│   ├── node/                # Node management
│   │   └── NodeManager.*
│   └── ui/                  # UI components
│       └── NodeControlWidget.*
└── docs/                    # Documentation
    ├── node_integration_report.md
    ├── architecture.md
    └── interface.md
```

### Adding Features

1. **New RPC method**: Add to `AnimicaRpcClient.h/.cpp`
2. **New UI screen**: Create widget in `src/ui/`
3. **Platform-specific code**: Add to `src/platform/`

### Testing

Currently, testing is manual. To test:

1. Build and run the wallet
2. Start node on devnet
3. Verify node becomes ready (green "Running" status)
4. Check sync progress updates
5. Stop node and verify clean shutdown
6. Test diagnostics export
7. Test restart functionality

Future: Unit tests using Qt Test framework.

## Known Issues

- No Windows testing yet (Linux/macOS only)
- Health check timeout is fixed (should be configurable)
- Log viewer doesn't handle very large logs efficiently
- No automatic node restart on crash (requires manual restart)

## Documentation

- **[Build and Bundle Guide](docs/build_and_bundle.md)**: Complete guide to the build system and node bundling
- **[CI Build Guide](docs/ci_build.md)**: CI/CD integration and automated builds
- **[Architecture](docs/architecture.md)**: Technical architecture and design decisions
- **[RPC Interface](docs/interface.md)**: Node RPC communication interface
- **[Node Integration Report](docs/node_integration_report.md)**: Details on node integration

## Testing

### Smoke Test (No Qt Required)

To test that the node can be built and run without building the full wallet:

```bash
cd wallet-qt
./scripts/test-node-build.sh
```

This script:
- Creates a test Python venv
- Installs all node dependencies
- Copies repository modules
- Verifies imports work correctly
- Starts and stops the node to ensure it runs

### Manual Testing

See [README.md § Usage](#usage) for wallet testing procedures.

## Contributing

See main repository [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.

## License

See [LICENSE.txt](../LICENSE.txt) in the repository root.

## References

- **Architecture**: [docs/architecture.md](docs/architecture.md)
- **RPC Interface**: [docs/interface.md](docs/interface.md)
- **Node Integration**: [docs/node_integration_report.md](docs/node_integration_report.md)
- **Qt Documentation**: https://doc.qt.io/qt-6/
- **Animica Repository**: https://github.com/animicaorg/all

## Data Directory Management

The wallet stores all data in a configurable data directory. See [docs/data_directory.md](docs/data_directory.md) for complete documentation.

### Default Locations

- **macOS**: `~/Library/Application Support/Animica/`
- **Windows**: `%APPDATA%\Animica\`
- **Linux**: `~/.animica/` (backward compatible with CLI)

### Changing Data Directory

1. Open **Settings → Change Data Directory**
2. Choose a folder
3. Restart the wallet

Or set environment variable:

```bash
export ANIMICA_DATA_DIR=/path/to/custom/dir
animica-wallet
```

### What's Stored

```
<data_dir>/
├── wallets.json       # Wallet keys
├── chain-1/           # Mainnet data
├── chain-2/           # Testnet data
├── chain-1337/        # Devnet data
├── logs/              # Node logs
└── snapshots/         # Chain snapshots
```

## Wallet Import/Export

### Importing Wallets

1. **Wallet → Import wallets.json**
2. Select file
3. Choose: Replace, Merge, or Cancel
4. Automatic backup created

Features:
- JSON validation
- Duplicate detection (merge mode)
- Atomic writes
- Timestamped backups

### Exporting Wallets

1. **Wallet → Export wallets.json**
2. Choose destination
3. ⚠️ Keep file secure (contains private keys!)

### Security

- Restrictive file permissions (0600 on Unix)
- Warnings before sensitive operations
- Automatic backups before overwrites
- No logging of private keys

## Network Isolation

The wallet prevents mixing data from different networks:

- Network marker file (`.network_id`) tracks current network
- Attempting to start wrong network shows error
- Separate `chain-*` directories per network
- Safe to switch networks by changing data directory

