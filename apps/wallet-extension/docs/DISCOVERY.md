# Animica Browser Wallet Extension - Phase 0 Discovery

This document summarizes the key findings from exploring the Animica repository to inform the browser wallet extension implementation.

## A) RPC Endpoints & Methods

The wallet will primarily interact with these RPC methods:

### Account & Balance
- `state.getBalance(address: str, tag: str = "latest") → hex_quantity`
  - Query confirmed balance from chain state
  - Address formats: `anim1...` (bech32m), `0x...` (hex), or `system:...`
  
- `state.getNonce(address: str, tag: str = "latest") → int`
  - Get account nonce for v1 transactions (deprecated in favor of v2)

### Transaction Submission & Status
- `tx.sendRawTransaction(rawTx: hex_bytes) → txid_hex`
  - Submit CBOR-encoded signed transaction to mempool
  - Returns transaction hash
  
- `tx.getTransaction(txid: hex_str) → tx_object`
  - Fetch full transaction data by hash
  
- `tx.getTransactionStatus(txid: hex_str) → {status, blockHeight, confirmations, included_height}`
  - Query transaction status:
    - `pending` - in mempool
    - `in_block_pending_confirm` - included but awaiting confirmations
    - `confirmed` - fully confirmed
    - `not_found` - not in chain or mempool
    - `reorged_out` - removed due to reorg
  
- `tx.getTransactionReceipt(txid: hex_str) → receipt_object`
  - Get execution receipt with logs and status

### Chain Info
- `chain.getHead() → block_header`
  - Get latest block header
  
- `chain.getChainId() → int`
  - Get network chain ID (1=mainnet, 2=testnet, 1337=devnet)
  
- `block.getBlock(blockId: str_or_int, full: bool) → block_object`
  - Fetch block by number or hash

### Mempool
- `tx2.getMempoolStats() → {count, totalBytes}`
  - Get mempool statistics
  
- `mempool.list() → txid_list`
  - List pending transaction IDs

### Network
- `net.getBootstrapSeeds() → peer_list`
  - Get bootstrap peer list for P2P discovery

## B) Transaction Schema

### UnsignedTx Structure

Animica supports both v1 (nonce-based) and v2 (validity window) transactions. **v2 is recommended** for new implementations.

```typescript
interface UnsignedTxV2 {
  v: 2;                          // Version
  chainId: number;               // Network chain ID
  from: Uint8Array;              // Sender address (32 bytes)
  gas: {
    price: number;               // Gas price per unit (integer)
    limit: number;               // Max gas units (must be > 0 except coinbase)
  };
  payload: {
    t: TxKind;                   // 0=TRANSFER, 1=DEPLOY, 2=CALL, 3=COINBASE
    v: TxPayload;                // Payload data (see below)
  };
  accessList?: AccessListEntry[]; // Optional storage optimization
  
  // v2-specific fields:
  validAfter: number;            // Tx valid from this block height
  validUntil: number;            // Tx valid until this block height (typically +120)
  salt: Uint8Array;              // 16 or 32 bytes for replay protection
  forkId?: number;               // Optional fork discriminator
}

interface UnsignedTxV1 {
  v: 1;
  // ... same fields as v2, except:
  nonce: number;                 // Account nonce (instead of validAfter/validUntil/salt)
}
```

### Payload Types

```typescript
// Transfer (t=0)
interface TxTransfer {
  to: Uint8Array;                // Recipient address (32 bytes)
  amount: number;                // Amount in base units (1 ANM = 1e9 base)
  data?: Uint8Array;             // Optional memo/calldata
}

// Deploy contract (t=1)
interface TxDeploy {
  code: Uint8Array;              // Contract bytecode
  manifest: Uint8Array;          // Canonical JSON ABI/manifest
}

// Call contract (t=2)
interface TxCall {
  to: Uint8Array;                // Contract address (32 bytes)
  data: Uint8Array;              // ABI-encoded calldata (non-empty)
}
```

### SignedTx Envelope

```typescript
interface SignedTx {
  tx: UnsignedTx;                // Unsigned transaction
  sigs: PqSignature[];           // At least 1 signature required
}

interface PqSignature {
  alg: number;                   // Algorithm ID (0x1001 for Dilithium3)
  pubkey: Uint8Array;            // Public key bytes (variable length)
  sig: Uint8Array;               // Signature bytes (2701 bytes for Dilithium3)
}
```

### Key Points
- **Gas is a dict**: `{price, limit}` NOT separate fields
- **No nonce in v2**: Use `validAfter`/`validUntil`/`salt` instead
- **Transaction hash**: `SHA3-256(CBOR(SignedTx))` - includes all signatures
- **Unsigned hash**: `SHA3-256(CBOR(UnsignedTx))` - for mempool deduplication
- **CBOR encoding**: Must be canonical (deterministic field order)

## C) Signing & Cryptography

### Signing Flow

1. **Build UnsignedTx** with all fields
2. **Get signing preimage**: `sign_bytes = canonical_sign_bytes(unsigned_tx, chain_id)`
   - Domain: `"animica/tx.v1"` for deterministic signing
3. **Sign with PQ algorithm**: `signature = sign_detached(sign_bytes, secret_key, alg_id)`
4. **Create SignedTx**: Wrap unsigned tx with signature(s)
5. **Encode to CBOR**: `cbor_bytes = CBOR.encode(signed_tx)` (canonical)
6. **Submit**: `tx.sendRawTransaction(hex(cbor_bytes))`

### Signature Algorithms

| Algorithm | ID | Public Key Size | Signature Size | Notes |
|-----------|-----|-----------------|----------------|-------|
| **Dilithium3** | `0x1001` (4097) | 1312 bytes | 2701 bytes | NIST PQC standard (ML-DSA-65) |
| **SPHINCS+** | `0x1002` (4098) | ~32 bytes | ~17K-41K bytes | Hash-based, stateful/stateless |

**Dilithium3 is the default and recommended** algorithm for new wallets.

### Address Format (Bech32m)

Animica uses bech32m encoding with `anim1` prefix:

```
Format: anim1<bech32m-encoded-data>

Decoded payload (34 bytes):
  alg_id (2 bytes, big-endian) || SHA3-256(pubkey) (32 bytes)
```

**Construction:**
```python
from pq.py.address import address_from_pubkey
address = address_from_pubkey(pubkey_bytes, alg_id=0x1001)
# Returns: "anim1qp5h..." (59 chars for Dilithium3)
```

**Decoding:**
```python
from pq.py.address import decode_address
rec = decode_address("anim1...")
# Returns: AddressRecord(hrp="anim", version=1, alg_id=0x1001, digest=<32-bytes>)
```

### Cryptographic Primitives
- **Hashing**: SHA3-256 for all internal hashes (txid, addresses, block hashing)
- **Signing domain**: `"animica/tx.v1"` for transaction signatures
- **Encoding**: Canonical CBOR (RFC 8949 deterministic encoding)

## D) wallets.json Schema

The node and CLI use `~/.animica/wallets.json` to store wallet keys.

### File Structure

```json
{
  "version": 1,
  "wallets": [
    {
      "label": "my-account",
      "address": "anim1qp5h...",
      "alg_id": 4097,
      "alg_name": "dilithium3",
      "public_key_hex": "0x...",
      "secret_key_hex": "0x...",
      "created_at": "2025-01-15T10:30:00Z"
    }
  ]
}
```

### WalletEntry Fields

| Field | Type | Description |
|-------|------|-------------|
| `label` | string | Human-readable wallet name |
| `address` | string | Bech32m address (anim1...) |
| `alg_id` | number | Signature algorithm ID (4097 for Dilithium3) |
| `alg_name` | string | Algorithm name (e.g., "dilithium3") |
| `public_key_hex` | string | Hex-encoded public key with 0x prefix |
| `secret_key_hex` | string | Hex-encoded secret key with 0x prefix |
| `created_at` | string | ISO 8601 timestamp |

### Implementation Details
- **File location**: `~/.animica/wallets.json` (default) or `$ANIMICA_WALLETS_FILE`
- **Permissions**: 0o600 (owner read/write only)
- **Secret storage**: Plain hex-encoded keys (encrypt at rest recommended for extension)
- **Loading code**: `python/animica/cli/wallet.py` (`_load_store()`, `_entry_from_dict()`)
- **Unknown fields**: Preserve top-level and per-wallet unknown keys for forward compatibility

### Extension Requirements
1. **Import**: Parse wallets.json, validate schema, merge with vault
2. **Export**: Write wallets.json in exact node format
3. **Round-trip safe**: Import → export without edits must preserve all fields
4. **Security**: Never export secrets without explicit user confirmation + password
5. **Dev sync**: Optional File System Access API for live sync (Chrome only)

## E) Network Configuration

### Chain IDs & Default RPC URLs

| Network | Chain ID | Default RPC URL | RPC Port | Bootstrap Seeds |
|---------|----------|-----------------|----------|-----------------|
| **mainnet** | `1` | `http://127.0.0.1:8545/rpc` | 8545 | Yes (DNS seeds) |
| **testnet** | `2` | `http://127.0.0.1:18546/rpc` | 18546 | Yes |
| **devnet** | `1337` | `http://127.0.0.1:28545/rpc` | 28545 | Local |
| **local** | `1337` | `http://127.0.0.1:38545/rpc` | 38545 | Local |

### Special Requirement: 144.126.133.21

Per issue requirements, **mainnet** default RPC must include:
- Primary: `http://144.126.133.21:8545/rpc`
- Fallback: `http://127.0.0.1:8545/rpc`

The extension should:
1. Try primary RPC first
2. Fall back to localhost on failure
3. Allow user to add custom RPC URLs
4. Implement health checks and automatic failover

### Environment Variables (Node/CLI)

```bash
ANIMICA_NETWORK=devnet              # Network selection
ANIMICA_RPC_URL=http://...          # Override RPC endpoint
ANIMICA_CHAIN_ID=1337               # Override chain ID
ANIMICA_WALLETS_FILE=...            # Custom wallets.json path
ANIMICA_DATA_DIR=~/.animica         # Root data directory
```

### Extension Network Model

```typescript
interface NetworkConfig {
  id: string;                    // "mainnet" | "testnet" | "devnet" | "local"
  name: string;                  // Display name
  chainId: number;               // Numeric chain ID
  rpcUrls: string[];             // Ordered list (primary + fallbacks)
  blockExplorer?: string;        // Optional explorer URL
  nativeCurrency: {
    name: string;                // "Animica"
    symbol: string;              // "ANM"
    decimals: number;            // 9 (1 ANM = 1e9 base units)
  };
}
```

## Implementation Summary

### Phase 1-12 Checklist

1. **[Phase 1] Scaffold**: MV3 extension structure with TypeScript + React + Vite
2. **[Phase 2] Vault**: AES-GCM encrypted storage with PBKDF2 key derivation
3. **[Phase 3] Accounts**: Multi-account with Dilithium3 keygen, import, delete
4. **[Phase 4] Networks**: Mainnet (144.126.133.21) + devnet + local with switcher
5. **[Phase 5] Provider**: `window.animica` AIP-1193 provider injection
6. **[Phase 6] Transactions**: v2 tx building, PQ signing, CBOR encoding, RPC submission
7. **[Phase 7] Activity**: Balance tracking with no double-debit (idempotent tx state)
8. **[Phase 8] wallets.json**: Full import/export/dev-sync compatibility
9. **[Phase 9] UI**: Onboarding/unlock/send/receive/activity with logo.png
10. **[Phase 10] RPC**: Default to 144.126.133.21 with health checks
11. **[Phase 11] Tests**: Crypto/wallets.json/tx/permissions unit tests
12. **[Phase 12] Docs**: README with dev/build/usage instructions

### Critical Gotchas

1. **Gas is a dict** `{price, limit}` - NOT separate fields
2. **Use v2 transactions** (validAfter/validUntil/salt) by default
3. **Addresses are 32 bytes** - must decode bech32m before using in tx
4. **Signature size**: 2701 bytes for Dilithium3 - much larger than ECDSA
5. **CBOR encoding**: Must be canonical for reproducible hashing
6. **Balance calculation**: `available = confirmed - pending_outgoing` (no double-debit)
7. **Transaction status**: Poll `tx.getTransactionStatus()` until `confirmed`
8. **wallets.json secrets**: Plain hex - extension must encrypt in vault

### Key Dependencies (for Extension)

- **TypeScript SDK**: Port or adapt from `sdk/python/omni_sdk/` and `sdk/typescript/`
- **PQ Crypto**: WASM build of liboqs (Dilithium3) or use remote signing
- **CBOR**: Use `cbor-x` or `cbor2` (canonical encoding required)
- **Bech32m**: Port or use `bech32` npm package with modified params
- **SHA3**: Use `js-sha3` or `@noble/hashes`

---

**End of Discovery Phase**

Next: Implement extension scaffold with MV3 structure.
