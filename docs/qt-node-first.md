# Animica Qt “Node-First” Stack

## Architecture (Node-First)

```
Qt Apps (wallet, nodepanel)
   │
   │  (signals/slots)
   ▼
AnimicaNodeKit
   ├─ ProcessManager (native or docker)
   ├─ RpcClient (local-only JSON-RPC)
   ├─ HealthMonitor (watchdog + restart policy)
   ├─ SnapshotManager (download/verify/apply)
   └─ Keystore (encrypted at rest)
   │
   ▼
Local Animica Node
   (datadir scoped per app + chain)
```

## Data directories & ports

Each app owns an isolated node datadir:

```
~/.animica/apps/<app_id>/chain-<chain_id>/
```

NodeKit binds all RPC/WS endpoints to localhost only:

- RPC: `127.0.0.1:<rpcPort>`
- WS: `127.0.0.1:<wsPort>` (optional)

## Build & run (Qt apps)

### Configure

```bash
cmake --preset linux
```

### Build

```bash
cmake --build --preset linux
```

### Run

```bash
./build/linux/qt/apps/wallet/animica_wallet
```

## NodeKit behavior

- Starts a local node per app (no remote RPC by default).
- Health monitor checks head progression and RPC reachability.
- Watchdog emits restart signals on repeated failures.
- Snapshot manager provides download/verify/apply hooks.

## Troubleshooting

- **Ports already in use**: change the configured RPC/WS ports in the app config.
- **Node logs**: view logs in the Logs & Metrics tab, or tail via `ProcessManager::tailLogs()`.
- **Snapshot recovery**: use the Recovery tab to apply snapshot actions.
