# Qt Wallet Architecture

## Architecture Decision: Sidecar Process Approach

### Decision

We have decided to run the Animica node as a **sidecar child process** launched and managed by the Qt wallet application.

### Implementation Choice

**Primary Implementation**: Standalone Python RPC Server (via `python -m rpc`)

We will launch the Animica RPC server directly as a Python subprocess rather than using Docker Compose. This decision is based on:

1. **Simpler Deployment**: No Docker dependency for end users
2. **Full Process Control**: Direct access to process lifecycle via QProcess
3. **Easier Debugging**: Direct access to stdout/stderr
4. **Lower Resource Usage**: No container overhead
5. **Faster Startup**: No Docker daemon interaction

**Fallback**: Docker Compose mode can be added later for users who prefer isolation.

### Tradeoffs Analysis

| Aspect | Sidecar Process | In-Process Library | External Node |
|--------|----------------|-------------------|---------------|
| **Crash Isolation** | ✅ Excellent - node crash doesn't kill wallet | ❌ Poor - crash takes down wallet | ✅ Excellent - completely separate |
| **Stability** | ✅ Good - independent lifecycles | ❌ Poor - shared memory space | ✅ Best - no coupling |
| **Upgrades** | ✅ Good - can restart node independently | ⚠️ Moderate - requires wallet restart | ✅ Best - upgrade node separately |
| **Security** | ✅ Good - process isolation, localhost-only RPC | ⚠️ Moderate - shared address space | ⚠️ Poor - network exposure risk |
| **Packaging** | ⚠️ Moderate - bundle Python runtime + code | ✅ Easy - single binary | ✅ Easy - wallet only |
| **Resource Usage** | ⚠️ Moderate - two processes | ✅ Low - single process | ✅ Low - wallet only |
| **User Experience** | ✅ Excellent - transparent operation | ✅ Excellent - invisible | ⚠️ Poor - manual node setup |
| **Development Complexity** | ⚠️ Moderate - IPC management | ⚠️ Moderate - C++ binding | ✅ Low - HTTP client only |
| **Cross-platform** | ✅ Good - Qt + Python portable | ⚠️ Hard - need C bindings | ✅ Easy - HTTP is universal |

### Why Sidecar Over In-Process

The Animica node does **not** currently expose a stable in-process library API with C/C++ bindings. The node is designed as a Python application with multiple services (RPC, P2P, consensus, execution) that are not architected for embedding as a library.

Creating in-process integration would require:
- Developing C++ bindings for Python components (complex, fragile)
- Managing Python interpreter lifecycle within Qt (error-prone)
- Handling thread safety between Qt and Python
- Dealing with shared memory and resource conflicts

These costs far outweigh the benefits. **Sidecar process is the pragmatic choice.**

### Why Sidecar Over External Node

While technically possible, requiring users to manually run an external node has severe UX problems:
- Users must learn command-line tools
- Version mismatches between wallet and node
- No automatic node lifecycle management
- Users may forget to start the node
- No integrated error reporting

A bundled sidecar node provides "just works" experience while maintaining proper isolation.

## Module Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          Qt Wallet UI                           │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ Node Screen │  │ Wallet Screen│  │ Settings Screen        │ │
│  │ (Start/Stop)│  │ (future)     │  │ (Network, Paths, etc.) │ │
│  └──────┬──────┘  └──────────────┘  └────────────────────────┘ │
│         │                                                        │
└─────────┼────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Core Modules                              │
│                                                                 │
│  ┌────────────────┐          ┌─────────────────┐               │
│  │  NodeManager   │          │  WalletEngine   │               │
│  │                │          │  (placeholder)  │               │
│  │ • Launch node  │          │                 │               │
│  │ • Monitor PID  │          │ • Key mgmt      │               │
│  │ • Health check │          │ • Tx building   │               │
│  │ • Restart      │          │ • Signing       │               │
│  │ • Logs         │          │                 │               │
│  └───────┬────────┘          └────────┬────────┘               │
│          │                            │                         │
│          ▼                            ▼                         │
│  ┌──────────────────────────────────────────────┐              │
│  │         AnimicaRpcClient                      │              │
│  │                                              │              │
│  │  • HTTP JSON-RPC client (Qt networking)     │              │
│  │  • WebSocket client (for subscriptions)     │              │
│  │  • Request/response handling                │              │
│  │  • Error mapping                            │              │
│  └──────────────────────────────────────────────┘              │
│          │                            │                         │
│          ▼                            ▼                         │
│  ┌──────────────────────────────────────────────┐              │
│  │            AppPaths                          │              │
│  │                                              │              │
│  │  • Cross-platform path resolution           │              │
│  │  • Data dir: node/, wallet/, logs/, run/    │              │
│  │  • OS-specific base directories             │              │
│  └──────────────────────────────────────────────┘              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
          │
          │  QProcess (stdin/stdout/stderr)
          │  Environment variables
          │  
          ▼
┌─────────────────────────────────────────────────────────────────┐
│             Animica Node (Sidecar Process)                      │
│                                                                 │
│      Command: python -m rpc                                     │
│      Env: ANIMICA_RPC_HOST=127.0.0.1                           │
│           ANIMICA_RPC_PORT=<auto-selected>                     │
│           ANIMICA_DATA_DIR=<wallet-managed>                    │
│           ANIMICA_NETWORK=<user-selected>                      │
│                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ RPC Server │  │ P2P Network  │  │ Consensus/Execution    │ │
│  │ :8545/rpc  │  │ (embedded)   │  │ (state, blocks, etc.)  │ │
│  │ :8545/ws   │  │              │  │                        │ │
│  └──────┬─────┘  └──────────────┘  └────────────────────────┘ │
│         │                                                       │
└─────────┼───────────────────────────────────────────────────────┘
          │
          │  HTTP/WebSocket (127.0.0.1 only)
          │
          ▼
    [Localhost-only
     network interface]
```

## Folder Structure

```
wallet-qt/
├── CMakeLists.txt                 # Main CMake build file
├── README.md                      # Build and usage instructions
├── .gitignore                     # Exclude build artifacts
│
├── docs/                          # Documentation
│   ├── node_integration_report.md # Part A: Node entrypoint analysis
│   ├── architecture.md            # This file: architecture decisions
│   ├── interface.md               # Part C: RPC interface documentation
│   └── build.md                   # Build instructions (TODO)
│
├── src/                           # C++/Qt source code
│   ├── main.cpp                   # Application entry point
│   ├── MainWindow.h/.cpp          # Main application window
│   │
│   ├── node/                      # Node management
│   │   ├── NodeManager.h          # Node lifecycle manager
│   │   ├── NodeManager.cpp
│   │   ├── NodeConfig.h           # Node configuration structures
│   │   └── NodeConfig.cpp
│   │
│   ├── rpc/                       # RPC client
│   │   ├── AnimicaRpcClient.h     # HTTP JSON-RPC client
│   │   ├── AnimicaRpcClient.cpp
│   │   ├── RpcTypes.h             # Request/response types
│   │   └── RpcTypes.cpp
│   │
│   ├── platform/                  # Platform-specific code
│   │   ├── AppPaths.h             # Cross-platform path resolution
│   │   ├── AppPaths.cpp
│   │   └── ProcessUtils.h/.cpp    # Process utilities (TODO)
│   │
│   ├── ui/                        # UI components
│   │   ├── NodeControlWidget.h    # Node start/stop UI
│   │   ├── NodeControlWidget.cpp
│   │   ├── LogViewer.h            # Log tailing widget
│   │   ├── LogViewer.cpp
│   │   └── StatusBar.h/.cpp       # Status display (TODO)
│   │
│   └── wallet/                    # Wallet logic (placeholder)
│       ├── WalletEngine.h         # Placeholder for future
│       └── WalletEngine.cpp
│
├── tests/                         # Unit tests (TODO)
│   ├── test_app_paths.cpp
│   ├── test_node_manager.cpp
│   └── test_rpc_client.cpp
│
├── scripts/                       # Build and packaging scripts
│   ├── build.sh                   # Build script (Linux/macOS)
│   ├── build.bat                  # Build script (Windows)
│   └── package.py                 # Packaging helper (TODO)
│
└── resources/                     # Application resources
    ├── icons/                     # Application icons
    ├── qml/                       # QML files (if using Qt Quick)
    └── animica.qrc                # Qt resource file
```

## Exact Repository Entrypoints to Reuse

Based on the Node Integration Report, these are the exact files and commands we will use:

### Python Module Entrypoints

1. **RPC Server** (primary choice):
   - **File**: `rpc/__main__.py` → calls `rpc/server.py:main()`
   - **Command**: `python -m rpc`
   - **Purpose**: Standalone RPC/WebSocket server

2. **Configuration**:
   - **File**: `rpc/config.py`
   - **Function**: `load()` - returns `RpcConfig` with all settings
   - **Environment Variables**: See Node Integration Report

3. **Network Configuration**:
   - **File**: `python/animica/config.py`
   - **Function**: `load_network_config()` - returns network-specific settings
   - **Default paths**: `~/.animica/chain-{CHAIN_ID}/`

### Python Interpreter

The wallet will invoke Python via:
- **System Python**: `python3` (if available and version >= 3.11)
- **Bundled Python**: `wallet-qt/bundle/python/bin/python3` (for packaged releases)

### Environment Variable Configuration

The wallet will set these environment variables when launching the node:

```bash
ANIMICA_RPC_HOST=127.0.0.1              # Force localhost-only
ANIMICA_RPC_PORT=<auto-selected>        # 8545, or next available
ANIMICA_DATA_DIR=<wallet-data-dir>      # Wallet-managed directory
ANIMICA_NETWORK=<user-selected>         # mainnet|testnet|devnet
ANIMICA_LOG_LEVEL=INFO                  # Can be changed in settings
ANIMICA_P2P_PORT=<auto-selected>        # 30333, or next available
```

### RPC Endpoints to Use

The wallet will primarily use these JSON-RPC methods:

**Health & Status**:
- `node.ping` → "pong"
- `chain.getHead` → {height, hash, ...}
- `chain.getChainId` → integer
- `sync.getStatus` → {syncing, currentBlock, highestBlock, ...}

**State Queries**:
- `state.getBalance` → balance in wei
- `state.getNonce` → nonce integer
- `state.getCode` → contract bytecode (future)

**Transactions**:
- `tx.sendRawTransaction` → tx hash
- `tx.getTransactionByHash` → tx details
- `tx.getTransactionReceipt` → receipt with status

**P2P**:
- `p2p.listPeers` → array of peer info
- `p2p.getPeerCount` → integer (if available)

See `interface.md` for complete RPC method documentation.

## Security Considerations

### Network Binding

- **RPC Host**: MUST be `127.0.0.1` (enforced by wallet)
- **RPC Port**: Auto-selected from range [8545, 8554] to avoid conflicts
- **P2P Port**: Auto-selected from range [30333, 30342] to avoid conflicts
- **No External Exposure**: Node RPC is not accessible from network

### File Permissions

- **Data Directory**: `0700` (owner read/write/execute only)
- **Key Files**: `0600` (owner read/write only)
- **Config Files**: `0644` (owner read/write, others read)
- **Lock Files**: `0644` with exclusive open flags

### Process Isolation

- **Separate Process**: Node runs in own process with own memory
- **No Privilege Escalation**: Runs with same user as wallet
- **Resource Limits**: OS-enforced limits (no special privileges)

### Authentication

- **Local-Only**: No authentication needed (localhost binding is security boundary)
- **Future Enhancement**: Generate random bearer token in file with `0600` permissions

## Data Directory Layout

The wallet will manage its own data directory separate from the default `~/.animica`:

```
<OS-specific-base>/AnimicaWallet/
├── node/                          # Node data (passed as ANIMICA_DATA_DIR)
│   ├── chain-1/                   # Mainnet
│   │   ├── animica.db
│   │   ├── blocks.db
│   │   └── p2p/
│   │       ├── peer_store.db
│   │       └── node_key
│   ├── chain-2/                   # Testnet
│   │   └── ...
│   └── chain-1337/                # Devnet
│       └── ...
│
├── wallet/                        # Wallet-specific data (future)
│   ├── keystore/                  # Encrypted private keys
│   ├── accounts.db                # Account metadata
│   └── settings.ini               # Wallet settings
│
├── logs/                          # All logs
│   ├── wallet.log                 # Wallet application log
│   ├── node-mainnet.log           # Node log for mainnet
│   ├── node-testnet.log           # Node log for testnet
│   └── node-devnet.log            # Node log for devnet
│
└── run/                           # Runtime state
    ├── node.json                  # Current node info (PID, port, version)
    ├── node.lock                  # Lock file (prevents multiple instances)
    └── node.pid                   # PID file
```

**OS-specific base paths**:
- **macOS**: `~/Library/Application Support/AnimicaWallet/`
- **Windows**: `%APPDATA%\AnimicaWallet\`
- **Linux**: `~/.local/share/AnimicaWallet/`

## Implementation Phases

### Phase 1: Foundation (Current PR)
- ✅ Node Integration Report
- ✅ Architecture document
- ⏳ AppPaths implementation
- ⏳ NodeManager skeleton
- ⏳ AnimicaRpcClient skeleton
- ⏳ Minimal UI with node control

### Phase 2: Node Control (Next PR)
- Full NodeManager implementation
- Process monitoring and restart logic
- Health check and sync status
- Log tailing
- Error handling and diagnostics

### Phase 3: RPC Integration (Later)
- Complete RPC client methods
- WebSocket subscriptions
- Request queuing and retries
- Response caching

### Phase 4: Wallet Features (Future)
- Key management (WalletEngine)
- Account creation/import
- Transaction building and signing
- Balance display
- Send/receive UI

### Phase 5: Polish (Future)
- Settings UI (network, paths, logging)
- Advanced features (contract interaction)
- Packaging for distribution
- Auto-update mechanism

## Build System

We use **CMake** for cross-platform builds:

```cmake
cmake_minimum_required(VERSION 3.16)
project(AnimicaWallet)

find_package(Qt6 REQUIRED COMPONENTS Core Widgets Network)

add_executable(animica-wallet
    src/main.cpp
    src/MainWindow.cpp
    src/node/NodeManager.cpp
    # ... other sources
)

target_link_libraries(animica-wallet
    Qt6::Core
    Qt6::Widgets
    Qt6::Network
)
```

**Build commands**:
```bash
mkdir build && cd build
cmake ..
make
./animica-wallet
```

## Next Steps

1. ✅ Complete Part A (Node Integration Report)
2. ✅ Complete Part B & F (Architecture document)
3. ⏳ Implement Part D (AppPaths)
4. ⏳ Implement Part E (NodeManager skeleton)
5. ⏳ Implement Part C (AnimicaRpcClient skeleton + interface.md)
6. ⏳ Implement Part G (Minimal UI)
7. Test on Linux
8. Test on macOS (if available)
9. Test on Windows (if available)

## References

- Node Integration Report: `wallet-qt/docs/node_integration_report.md`
- RPC Interface Documentation: `wallet-qt/docs/interface.md` (to be created)
- Qt Documentation: https://doc.qt.io/qt-6/
- Animica RPC Implementation: `rpc/server.py`, `rpc/config.py`
