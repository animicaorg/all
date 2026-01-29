# Animica Node Command Surface Inventory

This document inventories all command surfaces available for the Qt Wallet Diagnostics UI to interact with the embedded Animica node.

## Execution Model

### Node Process Management

The Animica node runs as a **subprocess/background process** managed by the Qt wallet:

- **Launch**: Via `animica node up --detach` (Python CLI wrapper)
- **Control**: Process managed via `NodeManager` class in wallet
- **Communication**: Local RPC over HTTP (loopback only, default: `http://127.0.0.1:8545/rpc`)
- **Logging**: Captured via process stdout/stderr or log file in data directory
- **PID Tracking**: PID file in `$DATA_DIR/logs/animica-p2p.pid`
- **Graceful Shutdown**: Via `animica node down` or SIGTERM
- **Port Management**: Auto-detection and conflict resolution

### Security Model

**Console Execution:**
- All commands run in sandboxed subprocess with strict timeouts
- RPC calls preferred over CLI subprocess for speed and structured output
- CLI subprocess only used for commands without RPC equivalents

**Network Binding:**
- Node binds RPC to loopback only (`127.0.0.1`)
- No external RPC access by default
- P2P service runs on separate port (default: `30333`)

---

## CLI Commands

All commands are invoked via `animica <command>` executable/wrapper.

### Node Lifecycle (`animica node`)

| Command | Purpose | Read-Only | Notes |
|---------|---------|-----------|-------|
| `status` | Show chain head, block info, and sync state | ✅ | Primary diagnostics command |
| `head` | Print current chain head summary | ✅ | Quick chain tip check |
| `block <height\|hash>` | Fetch and display a block | ✅ | Block inspection |
| `tx <hash>` | Fetch and display a transaction | ✅ | TX inspection |
| `bootstrap` | Bootstrap node from public bootstrap RPC | ❌ | Network sync operation |
| `up` | Start node process | ❌ | Launch daemon |
| `up-all` | Start node with all services | ❌ | Launch with extras |
| `down` | Stop node process | ❌ | Graceful shutdown |
| `reset` | Reset node state | ❌ | **DANGEROUS** - deletes chain data |

### Peer Management (`animica peer`)

| Command | Purpose | Read-Only | Notes |
|---------|---------|-----------|-------|
| `list` | List connected peers via RPC | ✅ | Shows peer IDs, addresses |
| `info <peer_id>` | Get peer information | ✅ | Detailed peer stats |
| `diagnose` | Diagnose peer connection issues | ✅ | Network troubleshooting |
| `test-latency <peer>` | Test peer latency | ✅ | Connection quality |
| `add <address>` | Dial a peer address via RPC | ❌ | Manual peer connection |
| `remove <peer_id>` | Disconnect a peer by ID | ❌ | Drop connection |
| `bootstrap` | Bootstrap peers from config | ❌ | Auto-connect to seeds |

### Sync Status (`animica sync`)

| Command | Purpose | Read-Only | Notes |
|---------|---------|-----------|-------|
| `status` | Get detailed sync progress and state | ✅ | Phase, progress %, queue depth |
| `pause` | Pause background sync | ❌ | Operator action |
| `resume` | Resume background sync | ❌ | Operator action |
| `force` | Force trigger a sync round | ❌ | Operator action |

### Mempool (`animica mempool`)

| Command | Purpose | Read-Only | Notes |
|---------|---------|-----------|-------|
| `list` | List pending transaction hashes | ✅ | Optional verbose output |
| `stats` | Show mempool statistics | ✅ | Count, size, oldest tx age |
| `drop <hash>` | Drop a transaction by hash | ❌ | Mempool management |

### RPC Call (`animica rpc`)

| Command | Purpose | Read-Only | Notes |
|---------|---------|-----------|-------|
| `call <method> [params...]` | Make raw JSON-RPC 2.0 call | Depends | Must check method allowlist |

---

## RPC Methods

All methods are invoked via JSON-RPC 2.0 over HTTP.

### Node & Health (Read-Only)

| Method | Returns | Purpose |
|--------|---------|---------|
| `node.ping` | `{ok: true, timestamp}` | Lightweight liveness check |
| `node.getStatus` | Complete node snapshot | **Primary diagnostics call** - chain, P2P, sync, hashrate |
| `node.syncStatus` | Sync status | Alias for sync status |

**Example `node.getStatus` Response:**
```json
{
  "chain": {
    "chainId": 1337,
    "head": {"height": 12345, "hash": "0x...", "timestamp": 1234567890},
    "bestHeader": {"height": 12346, "hash": "0x..."}
  },
  "sync": {
    "phase": "SYNCING",
    "progress": 0.95,
    "currentHeight": 12345,
    "targetHeight": 13000,
    "inFlightHeaders": 10,
    "queueDepth": 50
  },
  "p2p": {
    "peersInbound": 3,
    "peersOutbound": 5,
    "totalPeers": 8,
    "listenAddrs": ["/ip4/127.0.0.1/tcp/30333"]
  },
  "mempool": {
    "txCount": 25,
    "rejectedLast1h": 5
  },
  "hashrate": {
    "hashrate_hsps": 1234567.89,
    "window_blocks": 100
  }
}
```

### Chain Info (Read-Only)

| Method | Returns | Purpose |
|--------|---------|---------|
| `chain.getHead` | Current canonical head block | Latest confirmed block |
| `chain.getParams` | Chain parameters | Config values |
| `chain.getChainId` | Active chain ID | Network identifier |
| `chain.getChainIdentity` | Full identity (ID, genesis hash) | Chain verification |
| `chain.getNetworkHashrate` | Network hashrate | Mining power stats |
| `chain.getForks` | Fork history | Chain reorg tracking |
| `chain.getCheckpoints` | Checkpoint status | Sync checkpoints |
| `chain.getBlockByHeight` | Block by height | Block retrieval |
| `block.getBlockByNumber` | Block by number (alias) | Block retrieval |
| `block.getBlockByHash` | Block by hash | Block retrieval |

### Sync Control (Read-Only + Write)

| Method | Read-Only | Purpose |
|--------|-----------|---------|
| `sync.getStatus` | ✅ | Get current sync phase/progress |
| `sync.force` | ❌ | Trigger P2P sync, return status |
| `sync.trigger` | ❌ | Canonical alias for force |
| `sync.start` | ❌ | Legacy alias |
| `sync.pause` | ❌ | Pause background sync |
| `sync.resume` | ❌ | Resume background sync |
| `sync.setTarget` | ❌ | Set target height for sync |

### P2P Network (Read-Only + Write)

**Status (Read-Only):**

| Method | Returns | Purpose |
|--------|---------|---------|
| `p2p.getStatus` | Live P2P snapshot | Peer counts, listen addrs |
| `p2p.getPeerStats` | Detailed peer statistics | Per-peer metrics |
| `p2p.debugStatus` | TX relay debug info | Propagation diagnostics |
| `p2p.syncDebug` | Sync debug details | Sync state machine |
| `net.peerCount` | Total connected peer count | Quick peer count |
| `net.peers` | List connected peers | Peer enumeration |
| `net.getBootstrapSeeds` | Canonical bootstrap seeds | Seed peer list |

**Peer Management (Write):**

| Method | Purpose | Risk |
|--------|---------|------|
| `p2p.addPeer` | Add peer by address | Low - temporary connection |
| `p2p.removePeer` | Remove peer by ID | Low - drops connection |
| `p2p.getPeerInfo` | Get peer details | Read-only |
| `p2p.importPeers` | Persist and dial peers | Medium - persists to DB |
| `p2p.addPeers` | Add multiple peers | Medium - bulk operation |
| `p2p.getBans` | Current ban list | Read-only |
| `p2p.banPeer` | Ban peer for duration | Medium - security action |
| `p2p.unbanPeer` | Remove from ban list | Low - unban |
| `p2p.getVerifierSeeds` | Verifier seed status | Read-only |

### Transaction (Read-Only + Write)

| Method | Read-Only | Purpose |
|--------|-----------|---------|
| `tx.getTransactionByHash` | ✅ | Get tx by hash |
| `tx.getTransaction` | ✅ | Alias for hash lookup |
| `tx.getTransactionStatus` | ✅ | Get tx status (pending/confirmed) |
| `tx.getStatus` | ✅ | Status alias |
| `tx.decodeRawTransaction` | ✅ | Decode raw tx bytes |
| `tx.debugVerifyRawTransaction` | ✅ | Verify tx signatures |
| `tx.sendRawTransaction` | ❌ | Submit signed transaction |

### State/Account (Read-Only)

| Method | Returns | Purpose |
|--------|---------|---------|
| `state.getBalance` | Account balance | Balance query |
| `state.getNonce` | Account nonce | Nonce for new tx |
| `state.getPendingNonce` | Pending nonce | Nonce including mempool |
| `state.getNextNonce` | Next available nonce | TX building |
| `state.getAccount` | Full account state | Complete account info |
| `state.getRichList` | Top holders by balance | Wealth distribution |
| `state.getTotalSupply` | Total coin supply | Economic stats |

### Mempool (Read-Only + Write)

| Method | Read-Only | Purpose |
|--------|-----------|---------|
| `mempool.getPending` | ✅ | List pending transactions with metadata |
| `mempool.getStats` | ✅ | Mempool statistics (count, bytes, oldest age) |
| `mempool.getInfo` | ✅ | Mempool info (id, path) |
| `mempool.dropTransaction` | ❌ | Drop transaction |

### Mining (Write)

| Method | Purpose | Risk |
|--------|---------|------|
| `miner.mine` | Mine N blocks locally | High - devnet only |
| `miner.getBlockTemplate` | Get block template | Read-only (template fetch) |
| `miner.stop` | Stop mining | Low - stops mining |
| `miner.get_sha256_job` | Bitcoin Stratum v1 job | Read-only (template fetch) |
| `mining.getTemplateStatus` | Template readiness | Read-only |
| `mining.getCredits` | Mining credits audit trail | Read-only |

### Bootstrap (Read-Only, Special Permission)

| Method | Purpose | Notes |
|--------|---------|-------|
| `bootstrap.getManifest` | Lightweight chain manifest | Requires `allow_bootstrap_methods` |
| `bootstrap.getSeeds` | Public seed peers | Requires `allow_bootstrap_methods` |
| `bootstrap.getSnapshotManifest` | Snapshot manifest | Requires `allow_bootstrap_methods` |

---

## WebSocket Endpoints

### Endpoint: `/ws`

**Protocol:** JSON-RPC 2.0 over WebSocket

**Supported Topics:**
- `newHeads` - Broadcasts when new canonical head is finalized
- `pendingTxs` - Broadcasts new pending transactions admitted by RPC

**Client → Server Operations:**
```json
{"op": "sub", "topics": ["newHeads", "pendingTxs"]}
{"op": "unsub", "topics": ["pendingTxs"]}
{"op": "ping", "ts": 1234567890}
```

**Server → Client Events:**
```json
{"op": "hello", "topics": [...], "serverTime": 1234567890}
{"op": "pong", "ts": 1234567890}
{"topic": "newHeads", "data": {...}, "ts": 1234567890}
{"topic": "pendingTxs", "data": {...}, "ts": 1234567890}
```

**Features:**
- Backpressure-safe with bounded queue (256 events default)
- Lossy under pressure (newest events overwrite oldest)
- Keeps UI responsive during high load

---

## Command Allowlists for Console

### Safe CLI Commands (User Role - Read-Only)

```
node status
node head
node block <height|hash>
node tx <hash>
peer list
peer info <peer_id>
peer diagnose
peer test-latency <peer>
sync status
mempool list
mempool stats
rpc call <allowed_method> [params]
```

### Operator CLI Commands (Requires Unlock)

```
peer add <address>
peer remove <peer_id>
peer bootstrap
sync pause
sync resume
sync force
node bootstrap
```

### Developer CLI Commands (Dev Builds Only)

```
node reset (with extra confirmation)
mempool drop <hash>
miner.mine <n> (via rpc call)
```

### Safe RPC Methods (User Role - Read-Only)

**Node & Health:**
- `node.ping`
- `node.getStatus`
- `node.syncStatus`

**Chain:**
- `chain.getHead`
- `chain.getParams`
- `chain.getChainId`
- `chain.getChainIdentity`
- `chain.getNetworkHashrate`
- `chain.getForks`
- `chain.getCheckpoints`
- `chain.getBlockByHeight`
- `block.getBlockByNumber`
- `block.getBlockByHash`

**Sync:**
- `sync.getStatus`

**P2P:**
- `p2p.getStatus`
- `p2p.getPeerStats`
- `p2p.debugStatus`
- `p2p.syncDebug`
- `net.peerCount`
- `net.peers`
- `net.getBootstrapSeeds`
- `p2p.getPeerInfo`
- `p2p.getBans`
- `p2p.getVerifierSeeds`

**Transaction:**
- `tx.getTransactionByHash`
- `tx.getTransaction`
- `tx.getTransactionStatus`
- `tx.getStatus`
- `tx.decodeRawTransaction`
- `tx.debugVerifyRawTransaction`

**State:**
- `state.getBalance`
- `state.getNonce`
- `state.getPendingNonce`
- `state.getNextNonce`
- `state.getAccount`
- `state.getRichList`
- `state.getTotalSupply`

**Mempool:**
- `mempool.getPending`
- `mempool.getStats`
- `mempool.getInfo`

**Mining:**
- `miner.getBlockTemplate`
- `mining.getTemplateStatus`
- `mining.getCredits`

### Operator RPC Methods (Requires Unlock)

**Sync:**
- `sync.force`
- `sync.trigger`
- `sync.start`
- `sync.pause`
- `sync.resume`
- `sync.setTarget`

**P2P:**
- `p2p.addPeer`
- `p2p.removePeer`
- `p2p.importPeers`
- `p2p.addPeers`
- `p2p.banPeer`
- `p2p.unbanPeer`

**Mempool:**
- `mempool.dropTransaction`

**Mining:**
- `miner.stop`

### Developer RPC Methods (Dev Builds Only)

**Bootstrap:**
- `bootstrap.getManifest` (with `allow_bootstrap_methods` flag)
- `bootstrap.getSeeds` (with `allow_bootstrap_methods` flag)
- `bootstrap.getSnapshotManifest` (with `allow_bootstrap_methods` flag)

**Mining:**
- `miner.mine` (devnet only)

**Transaction:**
- `tx.sendRawTransaction` (wallet already handles this, but exposed for debugging)

---

## Security Considerations

### Secret Redaction Patterns

The following patterns must be redacted in all console output and logs:

**Credential Patterns:**
- `rpcpassword=<value>`
- `admin_token=<value>`
- `ANIMICA_RPC_ADMIN_TOKEN=<value>`
- `X-Animica-Admin-Token: <value>`

**Key Patterns:**
- `private_key: <hex>`
- `privkey: <hex>`
- `secret: <hex>`
- Any 64+ hex character strings after keywords like "key", "secret", "priv"

**Seed Phrase Patterns:**
- 12 or 24 word sequences (common mnemonic patterns)

**Redaction Strategy:**
- Replace sensitive values with `***REDACTED***`
- Preserve key names for debugging
- Apply to both stdout and log exports

### Timeouts and Limits

**Per-Command Timeouts:**
- Default: 5 seconds
- Bootstrap/snapshot operations: 60 seconds
- Sync operations: 30 seconds
- Simple queries: 5 seconds

**Output Limits:**
- Maximum output size: 2 MB
- Maximum line count: 20,000 lines
- Truncation message: `[Output truncated: exceeded limit]`

**Rate Limiting:**
- Max 10 commands per second (prevent DoS)
- Max 100 commands per minute
- Enforced per-session

### Environment Variable Protection

**Never log these environment variables:**
- `ANIMICA_RPC_ADMIN_TOKEN`
- `ANIMICA_BOOTSTRAP_RPC_URL` (may contain auth)
- Any variable with `PASSWORD`, `SECRET`, `TOKEN`, `KEY` in name

---

## Recommended UI Workflow

### Console Execution Flow

```
User Input → Parse Command → Check Allowlist → Check Role
                                     ↓
                                 Allowed?
                                /         \
                              Yes         No
                               ↓           ↓
                    Check if RPC method   Reject with error
                        available
                          /    \
                        Yes    No
                         ↓      ↓
                   Call RPC   Execute CLI subprocess
                         \    /
                          ↓  ↓
                    Format Output
                         ↓
                   Apply Redaction
                         ↓
                    Display Result
```

### Node Status Polling

**Recommended:** Use `node.getStatus` RPC call every 5 seconds for complete dashboard update.

**Fallback:** If `node.getStatus` not available, poll individual methods:
- `chain.getHead` - chain info
- `sync.getStatus` - sync status
- `p2p.getStatus` - peer info
- `mempool.getStats` - mempool stats

### Log Streaming

**Recommended:** Capture node stdout/stderr via `QProcess` in `NodeManager`.

**Fallback:** Tail log file at `$DATA_DIR/logs/animica-node.log` if process capture unavailable.

**Ring Buffer:** Keep last 10,000 lines in memory, virtualized display for performance.

---

## Implementation Checklist

- [x] Document CLI commands with read-only vs write categorization
- [x] Document RPC methods with read-only vs write categorization
- [x] Document WebSocket endpoints and protocol
- [x] Define command allowlists for User/Operator/Developer roles
- [x] Define RPC method allowlists for User/Operator/Developer roles
- [x] Document secret redaction patterns
- [x] Document timeouts and output limits
- [x] Document security considerations
- [x] Provide recommended UI workflow diagrams
- [ ] Implement in Qt wallet (Parts B, C, D)

---

**Last Updated:** 2026-01-29
**Author:** Animica Qt Wallet Team
**Version:** 1.0
