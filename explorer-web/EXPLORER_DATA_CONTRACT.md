# Explorer Data Contract

This document describes the RPC methods and response schemas that the Animica Explorer expects from a node. It also documents how the explorer gracefully degrades when optional methods are unavailable.

---

## Required RPC Methods

These methods **MUST** be implemented for the explorer to function:

### `chain.getChainId` → `string | number`

Returns the chain ID.

**Fallback**: `eth_chainId` (Ethereum-compatible)

**Example Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "1"
}
```

---

### `chain.getHead` → `ChainHead`

Returns the current chain head.

**Schema** (`ChainHead`):
```typescript
{
  height: number;          // Block height
  hash: string;            // Block hash
  timeISO: string;         // ISO 8601 timestamp
  timestamp?: number;      // Optional Unix timestamp (seconds)
  timestamp_ms?: number;   // Optional Unix timestamp (milliseconds)
}
```

**Example Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "height": 12345,
    "hash": "0xabc123...",
    "timeISO": "2026-01-05T06:00:00Z",
    "timestamp": 1704438000
  }
}
```

---

### `chain.getBlockByHeight` → `BlockDetail`

Fetches a block by height.

**Parameters**: `[height: number, includeTxs: boolean, includeReceipts: boolean]`

**Schema** (`BlockDetail`):
```typescript
{
  height: number;
  hash: string;
  parentHash?: string;
  timeISO: string;
  timestamp?: number;
  timestamp_ms?: number;
  proposer?: string;         // Address of block proposer/miner
  miner?: string;            // Alias for proposer
  reward?: string | number;  // Block reward
  difficulty?: string | number;
  gasUsed?: string | number;
  gasLimit?: string | number;
  size?: number;
  weight?: number;
  stateRoot?: string;
  receiptsRoot?: string;
  daRoot?: string;
  txCount?: number;
  txs?: TxSummary[];         // Array of transaction summaries
  transactions?: TxSummary[]; // Alias for txs
  confirmations?: number;
  poies?: {                  // Optional PoIES-specific data
    gamma?: number;
    fairness?: number;
    mix?: number;
  };
}
```

**Fallback**: `chain.getBlockByNumber`

**Example Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "height": 12345,
    "hash": "0xabc123...",
    "parentHash": "0xdef456...",
    "timeISO": "2026-01-05T06:00:00Z",
    "proposer": "0x123abc...",
    "gasUsed": "1000000",
    "gasLimit": "10000000",
    "txCount": 42,
    "txs": [...]
  }
}
```

---

### `tx.getTransaction` → `TxDetail`

Fetches transaction details by hash.

**Parameters**: `[hash: string]`

**Schema** (`TxDetail`):
```typescript
{
  hash: string;
  from: string;
  to?: string | null;
  value: string | number;
  nonce: number;
  blockHeight?: number;
  blockHash?: string;
  transactionIndex?: number;
  status?: 'pending' | 'executed' | 'failed' | 'success';
  gas?: string | number;
  gasLimit?: string | number;
  gasPrice?: string | number;
  fee?: string | number;
  input?: string;
  receipt?: Receipt;         // Transaction receipt
  signatureMetadata?: {      // Signature information
    algorithm?: string;
    publicKey?: string;
    signature?: string;
  };
  timestamp?: number;
  timeISO?: string;
}
```

**Example Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "hash": "0x789xyz...",
    "from": "0x123abc...",
    "to": "0x456def...",
    "value": "1000000000",
    "nonce": 5,
    "blockHeight": 12345,
    "status": "executed",
    "gasUsed": "21000",
    "fee": "42000"
  }
}
```

---

### `state.getAccount` → `AddressDetail`

Fetches account/address information.

**Parameters**: `[address: string]`

**Schema** (`AddressDetail`):
```typescript
{
  address: string;
  balance: string | number;
  balancePending?: string | number;  // Optional pending balance
  nonce: number;
  codeHash?: string | null;          // If contract
  isContract?: boolean;
  txCount?: number;
  tokens?: Array<{                   // Optional token balances
    token: string;
    balance: string | number;
    symbol?: string;
    decimals?: number;
  }>;
}
```

**Example Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "address": "0x123abc...",
    "balance": "5000000000000000000",
    "nonce": 10,
    "isContract": false,
    "txCount": 25
  }
}
```

---

## Optional RPC Methods

These methods **MAY** be implemented. The explorer detects their availability on startup and degrades gracefully if unavailable.

### `mempool.getStatus` → `MempoolStatus`

Returns current mempool status.

**Schema** (`MempoolStatus`):
```typescript
{
  size: number;              // Number of pending transactions
  txs?: MempoolEntry[];      // Array of pending transactions
  bytes?: number;            // Total mempool size in bytes
}
```

**Degradation**: If unavailable, the Mempool page shows "Not available on this node".

---

### `network.getPeers` → `PeersStatus`

Returns network peer information.

**Schema** (`PeersStatus`):
```typescript
{
  total: number;
  inbound: number;
  outbound: number;
  peers?: Array<{
    id: string;
    address?: string;
    inbound: boolean;
    version?: string;
    latency?: number;
    height?: number;
  }>;
}
```

**Degradation**: If unavailable, the Peers page shows "Not available on this node".

---

### `node.getSyncStatus` → `SyncStatus`

Returns node synchronization status.

**Schema**:
```typescript
{
  phase: 'idle' | 'headers' | 'syncing' | 'fully-synced' | 'catching-up' | 'backfilling';
  progress?: number;  // 0.0 to 1.0
}
```

**Degradation**: If unavailable, assumes node is fully synced.

---

### `node.getInfo` → `NodeInfo`

Returns node information.

**Schema**:
```typescript
{
  version?: string;
  peers?: number;
}
```

**Degradation**: Node version and peer count not displayed if unavailable.

---

### `chain.getFeePolicy` → `FeePolicy`

Returns current fee policy.

**Schema** (`FeePolicy`):
```typescript
{
  baseFee?: string | number;
  minGasPrice?: string | number;
  maxGasPrice?: string | number;
  gasCeiling?: string | number;
}
```

**Degradation**: Fee policy information not displayed if unavailable.

---

## WebSocket Subscriptions (Optional)

### `newHeads` Subscription

The explorer can subscribe to new heads via WebSocket for live updates.

**Connection**: WebSocket URL derived from HTTP RPC URL (http → ws, https → wss)

**Subscription Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "subscribe",
  "params": ["newHeads"]
}
```

**Notification Format**:
```json
{
  "jsonrpc": "2.0",
  "method": "subscription",
  "params": {
    "subscription": "0x123",
    "result": {
      "height": 12346,
      "hash": "0x...",
      "timeISO": "2026-01-05T06:01:00Z"
    }
  }
}
```

**Degradation**: If WebSocket unavailable, falls back to HTTP polling every 4 seconds.

---

## Feature Detection

On startup, the explorer probes each optional method to detect availability:

```typescript
// Probes a method by calling it and checking for -32601 (method not found)
async function detectFeatures() {
  const features = {
    hasMempool: await probe('mempool.getStatus'),
    hasPeers: await probe('network.getPeers'),
    hasSyncStatus: await probe('node.getSyncStatus'),
    hasNodeInfo: await probe('node.getInfo'),
    hasFeePolicy: await probe('chain.getFeePolicy'),
  };
  return features;
}
```

---

## Error Handling

### Method Not Found (`-32601`)

Treated as feature unavailable. Explorer shows "Not available on this node" for that feature.

### Server Error (`-32000` to `-32099`)

Retried with exponential backoff (150ms → 300ms → 600ms, max 2.5s).

### Network Errors / Timeouts

Retried with exponential backoff. If CORS error detected, shows clear error message.

### Invalid Response Schema

Validation error logged to console with full context. Page shows error state with troubleshooting guidance.

---

## CORS Requirements

The explorer is a **static SPA** that runs in the browser. The RPC server **MUST** allow the explorer's origin via CORS headers:

```
Access-Control-Allow-Origin: https://explorer.animica.org
Access-Control-Allow-Methods: POST, OPTIONS
Access-Control-Allow-Headers: Content-Type
```

For development:
```
Access-Control-Allow-Origin: http://localhost:3001
```

**Alternative**: Use a reverse proxy to serve the explorer and RPC from the same origin.

---

## Consistency Requirements

1. **Reorg Safety**: If a block hash changes at the same height, the explorer invalidates recent blocks and refetches.
2. **Sync Awareness**: If `syncPhase != 'fully-synced'`, a prominent banner warns users that data may be incomplete.
3. **Schema Validation**: All RPC responses are validated with Zod schemas. Invalid responses are logged and trigger error states.
4. **Request Deduplication**: Identical concurrent requests (within 100ms) are deduplicated to reduce load.

---

## Performance Considerations

- **Caching**: Uses TanStack Query with 5-second stale time for chain data, 60-second for immutable data (confirmed txs).
- **Batching**: Where possible, the explorer uses batch RPC calls to reduce round trips.
- **IndexedDB**: Blocks, transactions, and addresses are cached locally in IndexedDB for offline access.
- **Polling**: Only used when WebSocket unavailable. Default interval: 4 seconds.

---

## Testing Your RPC Implementation

### Quick Test Script

```bash
# Test chain ID
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'

# Test get head
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"chain.getHead","params":[]}'

# Test get block
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"chain.getBlockByHeight","params":[1, false, false]}'
```

### Using Explorer's Test Connection

The explorer includes a **Settings** page (`/settings`) with a "Test Connection" button that:
1. Tests each required method
2. Probes optional methods
3. Shows which features are available
4. Displays latency and error details

---

## Summary

| Method | Required | Fallback | Degradation |
|--------|----------|----------|-------------|
| `chain.getChainId` | ✅ Yes | `eth_chainId` | — |
| `chain.getHead` | ✅ Yes | — | — |
| `chain.getBlockByHeight` | ✅ Yes | `chain.getBlockByNumber` | — |
| `tx.getTransaction` | ✅ Yes | — | — |
| `state.getAccount` | ✅ Yes | — | — |
| `mempool.getStatus` | ⚪ Optional | — | "Not available" message |
| `network.getPeers` | ⚪ Optional | — | "Not available" message |
| `node.getSyncStatus` | ⚪ Optional | — | Assume fully synced |
| `node.getInfo` | ⚪ Optional | — | Node info hidden |
| `chain.getFeePolicy` | ⚪ Optional | — | Fee policy hidden |
| WebSocket `newHeads` | ⚪ Optional | HTTP polling | Polling every 4s |

---

For questions or issues, please file a GitHub issue at https://github.com/animicaorg/all/issues
