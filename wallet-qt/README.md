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

### Planned (Future Releases)

- ⏳ Key management and wallet creation
- ⏳ Account import/export
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

### 1. Install Dependencies

#### Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install -y \
    build-essential \
    cmake \
    qt6-base-dev \
    qt6-tools-dev \
    libqt6network6 \
    python3 \
    python3-pip
```

#### macOS

```bash
brew install qt@6 cmake python@3.11
export PATH="/usr/local/opt/qt@6/bin:$PATH"
```

#### Windows

- Install Qt 6 from https://www.qt.io/download
- Install CMake from https://cmake.org/download/
- Install Python 3.11+ from https://www.python.org/downloads/
- Install Visual Studio 2019+ or MinGW

### 2. Set Up Animica Node

From the repository root:

```bash
# Install Animica Python package and dependencies
./setup.sh --with-pq
source .venv/bin/activate

# Verify Python installation
python -m rpc --help
```

### 3. Build Wallet

```bash
cd wallet-qt
mkdir build && cd build

# Configure
cmake ..

# Or with specific Qt path (if not in PATH)
cmake .. -DCMAKE_PREFIX_PATH=/path/to/qt6

# Build
cmake --build .

# Or with ninja (faster)
cmake .. -G Ninja
ninja
```

### 4. Run

```bash
# From build directory
./bin/animica-wallet

# Or from project root
cd wallet-qt
./build/bin/animica-wallet
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
1. Check Python installation: `python3 --version` (must be 3.11+)
2. Verify Animica is installed: `python -m rpc --help`
3. Check if port is in use: `lsof -i :8545` (Linux/macOS) or `netstat -ano | findstr :8545` (Windows)
4. View diagnostics: Click "Diagnostics" button

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
