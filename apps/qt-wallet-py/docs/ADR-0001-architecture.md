# ADR-0001: Qt Wallet (Python) + Wallet Daemon Architecture

## Status
Accepted (proposed for implementation in subsequent prompts)

## Context
Animica already ships a CLI-managed node and a JSON-RPC interface. The goal is to build a desktop wallet that **bundles and controls a local node** while adding a **wallet-focused RPC** layer for signing, balances, and account management. We want to keep consensus/node logic untouched and build a separate app in `apps/qt-wallet-py/` that composes existing node and SDK pieces.

## Decision
We will implement a two-process desktop application:

1) **Qt Wallet UI (PySide6 + qasync)**
   - Desktop GUI (cross-platform) running an asyncio event loop via qasync.
   - Connects to a local **wallet daemon (walletd)** over JSON-RPC (localhost).
   - Uses httpx (and WebSocket if available) to subscribe to node head/mempool streams.

2) **walletd (background daemon)**
   - Supervises the node process (start/stop/health/logs).
   - Exposes a localhost-only JSON-RPC interface for wallet features (sign/send, balances, address book, keystore unlock).
   - Optionally proxies through to the node JSON-RPC and enforces authentication/allowlist.

## Component Model

### Components
- **Qt UI (apps/qt-wallet-py/)**
  - Responsible for UX, routing, dialogs, and presentation.
  - No direct private-key handling except in-memory; it delegates all signing to walletd.

- **walletd (apps/qt-wallet-py/walletd/)**
  - Manages wallets (keystore, mnemonic restore/import, account derivation).
  - Uses SDK primitives for keystore/mnemonic/PQ signing.
  - Controls node lifecycle (start/stop/restart) and streams logs to UI.
  - Provides JSON-RPC for UI and external local clients (optional).

- **Animica Node (existing)**
  - Runs as a separate process, unchanged.
  - Exposes JSON-RPC over HTTP and WS for chain/state/tx/p2p.

### Process Model
- **Process A**: Qt UI (user-facing, interactive).
- **Process B**: walletd (background service; starts on app launch, stops on exit).
- **Process C**: animica node (spawned/managed by walletd, existing CLI/compose path).

### Data Flow
1) UI -> walletd: unlock keystore, derive address, request balances.
2) walletd -> node RPC: chain/state/mempool/peer queries.
3) UI -> walletd: construct transaction request.
4) walletd: sign via PQ signer -> send raw tx to node RPC.

## Ports & Endpoints
- **walletd RPC**: `http://127.0.0.1:18666/rpc` (JSON-RPC 2.0)
- **walletd WS**: `ws://127.0.0.1:18666/ws` (optional, for push events)
- **node RPC**: `http://127.0.0.1:8545/rpc` (configurable)
- **node WS**: `ws://127.0.0.1:8545/ws` (configurable)

> Ports are defaults; walletd will allow overrides via config/env.

## Data Directories
Use OS-specific application data paths with a scoped subdirectory:

- **Linux**: `~/.local/share/Animica/QtWallet/`
- **macOS**: `~/Library/Application Support/Animica/QtWallet/`
- **Windows**: `%APPDATA%\Animica\QtWallet\`

Inside this directory:
- `walletd/` (walletd state, logs, config)
- `wallets/` (keystore files)
- `node/` (node data dir, logs)

## Security Model
- **Localhost-only**: walletd binds to `127.0.0.1` by default; no remote exposure.
- **Token auth**: walletd requires a random per-install token (stored in config) and enforces `Authorization: Bearer <token>`.
- **Optional proxy**: when proxying node RPC, walletd can enforce the same token and allowlist only safe methods for UI.
- **Key handling**: private key material never leaves walletd; UI receives addresses, balances, and signed transaction hashes only.

## Implementation Notes
- Use **qasync** to bridge Qt’s event loop with asyncio so the UI can await walletd calls.
- Use **httpx** for JSON-RPC calls (both to walletd and node). If websockets are available, use httpx/ws or `websockets` for subscriptions.
- The node is managed via CLI entrypoint and existing flags (start/stop/status/logs) rather than embedding consensus logic.

## Acceptance Criteria
- Qt wallet can start/stop the node locally and surface health/logs.
- walletd exposes a stable JSON-RPC API for wallet operations and proxies to node RPC.
- Wallet storage uses the existing keystore/mnemonic/PQ signing primitives without modification.
- All walletd APIs are **localhost-only** and require a token.
- Cross-platform paths are respected for data and logs.

## Out of Scope
- Modifying consensus, chain, or node RPC implementations.
- Remote RPC hosting (non-localhost) for walletd.
- Multi-user wallet management or hardware wallet integration.
- UI/UX polishing and full design system in this ADR.

## Consequences
- Node code remains untouched; we layer walletd and UI on top.
- Wallet functionality is cleanly isolated for future mobile/desktop clients.
- Local security posture improves with token auth + localhost binding.

