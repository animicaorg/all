# Transaction Flow Documentation

This document describes the Animica transaction format, signing primitives, RPC endpoints, and send/receive flow for the Qt wallet with embedded node integration.

## Table of Contents

1. [Transaction Format](#transaction-format)
2. [Signing Primitives](#signing-primitives)
3. [Address Format](#address-format)
4. [RPC Endpoints](#rpc-endpoints)
5. [Transaction Lifecycle](#transaction-lifecycle)
6. [Signing and Broadcast Flow](#signing-and-broadcast-flow)
7. [Fee Estimation](#fee-estimation)
8. [Error Handling](#error-handling)

---

## 1. Transaction Format

Transactions use **canonical CBOR encoding** per RFC 8949 with deterministic field ordering.

### Transaction Structure

#### UnsignedTx

```python
@dataclass(frozen=True)
class UnsignedTx:
    version: int           # 1 (nonce-based) or 2 (time-window)
    chain_id: int          # Network identifier (1337=devnet, 1=mainnet, 2=testnet)
    fork_id: Optional[int] # Protocol upgrade marker
    
    # Version 1 (nonce-based):
    nonce: Optional[int]   # Sequential account nonce
    
    # Version 2 (time-window):
    valid_after: Optional[int]  # Unix timestamp (seconds)
    valid_until: Optional[int]  # Unix timestamp (seconds)
    salt: Optional[bytes]       # 16 or 32 bytes for uniqueness
    
    # Gas metering:
    gas_price: int         # Wei per gas unit
    gas_limit: int         # Maximum gas units
    
    # Sender and payload:
    sender: bytes          # 32-byte sender address (raw, not bech32m)
    kind: TxKind           # TRANSFER, DEPLOY, CALL, COINBASE
    payload: TxPayload     # Kind-specific payload
    
    # Advanced features:
    access_list: Tuple[AccessEntry, ...] = ()  # Preload accounts/storage
```

#### Transaction Kinds

```python
class TxKind(IntEnum):
    TRANSFER = 0  # Value transfer + optional data
    DEPLOY = 1    # Deploy Python-VM contract
    CALL = 2      # Call contract method
    COINBASE = 3  # Mining reward (protocol-generated only)
```

#### Payloads

**TxTransfer:**
```python
@dataclass(frozen=True)
class TxTransfer:
    to: bytes       # 32-byte recipient address
    amount: int     # Wei to transfer
    data: bytes     # Optional memo/data (default: b"")
```

**TxDeploy:**
```python
@dataclass(frozen=True)
class TxDeploy:
    code: bytes     # Python source or bytecode
    manifest: bytes # Canonical JSON manifest (ABI, capabilities, metadata)
```

**TxCall:**
```python
@dataclass(frozen=True)
class TxCall:
    to: bytes       # 32-byte contract address
    data: bytes     # ABI-encoded function selector + arguments
```

#### Signed Transaction

```python
@dataclass(frozen=True)
class Tx:
    tx: UnsignedTx           # Unsigned transaction
    sigs: List[PqSignature]  # Post-quantum signatures (usually 1)
```

#### PQ Signature

```python
class PqSignature:
    alg_id: int    # Algorithm ID (0x1001=Dilithium3, 0x1002=SPHINCS+)
    pubkey: bytes  # Public key (max 2048 bytes)
    sig: bytes     # Signature (max 8192 bytes)
```

### CBOR Encoding Rules

- **Canonical**: Keys sorted lexicographically, minimal integer encoding
- **Deterministic**: Same input always produces identical bytes
- **Domain-separated**: Sign-bytes use domain string "animica/tx.sign"
- **TxID**: SHA3-256 of signed CBOR (includes signature, prevents malleability)

### Version Differences

**Version 1 (nonce-based):**
- Uses sequential nonce per account
- Must increment by 1 for each tx
- Nonce gaps cause rejection
- Replay protection via nonce uniqueness

**Version 2 (time-window):**
- Uses time windows (valid_after, valid_until)
- Requires 16 or 32-byte salt for uniqueness
- No nonce sequencing required
- Replay protection via time + salt

---

## 2. Signing Primitives

### Post-Quantum Algorithms

The wallet supports NIST-standard post-quantum signature algorithms:

| Algorithm | Alg ID | Pubkey Size | Sig Size | Security Level |
|-----------|--------|-------------|----------|----------------|
| **Dilithium3** (default) | 0x1001 | ~1952B | ~3293B | NIST Level 3 |
| **SPHINCS+ SHAKE 128s** | 0x1002 | 32B | ~7856B | NIST Level 1 |

### Domain Separation

All signatures use **domain-separated signing** per `spec/domains.yaml`:

```python
sign_bytes = UnsignedTx.sign_bytes()
# Returns: SHA3-256(domain_string || CBOR(UnsignedTx))
# where domain_string = "animica/tx.sign"
```

This prevents signature reuse across different contexts (tx, blocks, headers, etc.).

### Signing Flow

```python
# 1. Build unsigned transaction
unsigned_tx = build_transfer(
    version=1,
    chain_id=1337,
    sender=sender_addr_bytes,
    to=recipient_addr_bytes,
    amount=1_000_000_000,  # 1 ANM in wei
    nonce=5,
    gas_price=1_000_000,
    gas_limit=21_000
)

# 2. Get sign bytes (domain-separated hash)
sign_bytes = unsigned_tx.sign_bytes()

# 3. Sign with PQ algorithm
from pq.py.sign import sign_detached

sig_env = sign_detached(
    msg=sign_bytes,
    alg="dilithium3",
    sk=secret_key_bytes,
    domain="animica/tx.sign",
    chain_id=1337,
    fork_id=None,
    prehash="sha3-512"  # Deterministic prehash
)

# 4. Build signed transaction
signed_tx = Tx(
    tx=unsigned_tx,
    sigs=[PqSignature(
        alg_id=sig_env["alg"],
        pubkey=sig_env["pubkey"],
        sig=sig_env["sig"]
    )]
)

# 5. Encode to CBOR
from core.encoding.cbor import cbor_dumps
raw_cbor = cbor_dumps(signed_tx.to_obj())

# 6. Compute TX ID
from core.utils.hash import sha3_256
tx_id = "0x" + sha3_256(raw_cbor).hex()
```

### Verification

```python
from pq.py.verify import verify_detached

# Extract from signed tx
tx_obj = cbor_loads(raw_cbor)
unsigned_obj = tx_obj["tx"]
sig_obj = tx_obj["sigs"][0]

# Reconstruct sign bytes
unsigned_tx = UnsignedTx.from_obj(unsigned_obj)
sign_bytes = unsigned_tx.sign_bytes()

# Verify
is_valid = verify_detached(
    msg=sign_bytes,
    sig_env=sig_obj,
    pubkey=sig_obj["pubkey"],
    chain_id=unsigned_tx.chain_id,
    fork_id=unsigned_tx.fork_id
)
```

---

## 3. Address Format

Animica uses **bech32m encoding** with HRP `anim` for addresses.

### Address Structure

```
anim1<bech32m-encoded-payload>

Payload = alg_id (2 bytes) || sha3_256(pubkey) (32 bytes)
Total: 34 bytes encoded to bech32m
```

### Encoding

```python
from pq.py.address import address_from_pubkey

# Generate address from public key
address = address_from_pubkey(
    pubkey=pubkey_bytes,
    alg_id=0x1001  # Dilithium3
)
# Returns: "anim1..."
```

### Decoding

```python
from pq.py.address import decode_address

addr_record = decode_address("anim1...")
# Returns: AddressRecord(alg_id=0x1001, digest=bytes(32))
```

### Validation

Qt wallet must validate:
- Prefix is "anim1"
- Bech32m checksum passes
- Payload decodes to exactly 34 bytes
- Algorithm ID is recognized (0x1001, 0x1002)

**Invalid examples:**
- Wrong HRP: `eth1...`, `anim...` (missing '1')
- Wrong checksum: bech32m validation fails
- Wrong length: payload != 34 bytes

---

## 4. RPC Endpoints

The wallet communicates with the embedded node via JSON-RPC 2.0 over HTTP.

### Base Configuration

- **HTTP Endpoint:** `http://127.0.0.1:<port>/rpc`
- **WebSocket:** `ws://127.0.0.1:<port>/ws`
- **Port:** Auto-incremented if conflict (default 8545)
- **Localhost-only:** No external exposure

### 4.1 Transaction Submission

#### `tx.sendRawTransaction(rawCborTx)`

Submit a signed transaction to the mempool.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tx.sendRawTransaction",
  "params": ["0xa264747882a8..."]
}
```

**Response (success):**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "0x1234567890abcdef..."
}
```

**Response (error):**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32011,
    "message": "ChainIdMismatch",
    "data": {
      "expected": 1337,
      "got": 1,
      "address": "anim1..."
    }
  }
}
```

**Error Codes:**
- `-32010`: InvalidTx (CBOR decode/validation failed)
- `-32011`: ChainIdMismatch
- `-32012`: SignatureInvalid (PQ verification failed)
- `-32013`: FeeTooLow (below minimum or mempool floor)
- `-32014`: NonceGap (nonce not sequential)
- `-32015`: Oversize (tx exceeds byte/gas limits)
- `-32016`: Duplicate (already in mempool)

### 4.2 Transaction Queries

#### `tx.getTransactionByHash(hash)`

Fetch transaction by hash (searches mempool + chain).

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tx.getTransactionByHash",
  "params": ["0x1234..."]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "hash": "0x1234...",
    "from": "anim1...",
    "to": "anim1...",
    "value": "1000000000",
    "gas": "21000",
    "gasPrice": "1000000",
    "nonce": 5,
    "status": "mined",
    "blockHash": "0xabcd...",
    "blockNumber": 12345
  }
}
```

**Status values:**
- `"pending"`: In mempool
- `"mined"`: Included in block

#### `tx.getTransactionReceipt(txHash)`

Get execution receipt (only for mined transactions).

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tx.getTransactionReceipt",
  "params": ["0x1234..."]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "txHash": "0x1234...",
    "status": "SUCCESS",
    "gasUsed": "21000",
    "logs": [],
    "blockHash": "0xabcd...",
    "blockNumber": 12345,
    "transactionIndex": 0
  }
}
```

**Receipt status:**
- `"SUCCESS"`: Execution succeeded
- `"REVERT"`: Contract reverted
- `"OOG"`: Out of gas

### 4.3 State Queries

#### `state.getBalance(address, blockTag?)`

Get account balance at a specific block.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "state.getBalance",
  "params": ["anim1...", "latest"]
}
```

**Block tags:**
- `"latest"`: Current head
- `"pending"`: Head + pending mempool
- `"safe"`: Safe finalized block
- `"finalized"`: Finalized block
- Number: Specific height

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": "1000000000000000000"
}
```

#### `state.getNonce(address, blockTag?)`

Get account nonce (includes pending transactions).

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "state.getNonce",
  "params": ["anim1...", "pending"]
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": 7
}
```

**Important:** Always query with `"pending"` tag to get correct next nonce including mempool transactions.

### 4.4 Chain Queries

#### `chain.getHead()`

Get current canonical head.

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "height": 12345,
    "hash": "0xabcd...",
    "time": "2026-01-29T01:42:46Z"
  }
}
```

#### `chain.getBlockByNumber(number, opts?)`

Get block at height.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "chain.getBlockByNumber",
  "params": [12345, {"txs": true, "receipts": false}]
}
```

**Options:**
- `txs`: Include transaction hashes
- `receipts`: Include receipts
- `proofs`: Include PoIES proofs

#### `chain.getBlockByHash(hash, opts?)`

Get block by hash (same options as getBlockByNumber).

### 4.5 Mempool Queries

#### `mempool.stats()`

Get mempool statistics.

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "pending": 42,
    "queued": 5,
    "oldestTxAge": 120.5
  }
}
```

### 4.6 WebSocket Subscriptions

#### Subscribe to new heads

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "subscribe",
  "params": {"topic": "newHeads"}
}
```

**Ack:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {"subscriptionId": "sub-7b2a"}
}
```

**Push notifications:**
```json
{
  "jsonrpc": "2.0",
  "method": "newHeads",
  "params": {
    "subscriptionId": "sub-7b2a",
    "data": {
      "height": 12346,
      "hash": "0x...",
      "time": "2026-01-29T01:42:50Z"
    }
  }
}
```

#### Subscribe to pending transactions

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "subscribe",
  "params": {"topic": "pendingTxs"}
}
```

**Push notifications:**
```json
{
  "jsonrpc": "2.0",
  "method": "pendingTxs",
  "params": {
    "subscriptionId": "sub-abc123",
    "data": {
      "hash": "0x1234...",
      "from": "anim1..."
    }
  }
}
```

#### Unsubscribe

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "unsubscribe",
  "params": {"subscriptionId": "sub-7b2a"}
}
```

---

## 5. Transaction Lifecycle

### State Machine

```
       sendRawTransaction
              ↓
    ┌─────────────────┐
    │  MEMPOOL        │ ← Validation passed
    │  (pending)      │   Broadcast to peers
    └─────────────────┘
         │       ↑
         │       │ (reorg)
         ↓       │
    ┌─────────────────┐
    │  MINED          │ ← Included in block
    │  (confirmations │   Receipt available
    │   = 1)          │
    └─────────────────┘
         │
         ↓
    ┌─────────────────┐
    │  CONFIRMED      │ ← N confirmations
    │  (finalized)    │   (default: 10)
    └─────────────────┘
         │
         ↓
    ┌─────────────────┐
    │  FINAL          │ ← Safe/finalized
    └─────────────────┘

    Alternative paths:
    
    MEMPOOL → DROPPED (evicted, replaced, invalid)
    MINED → REORGED → MEMPOOL or DROPPED
```

### Confirmation Logic

```python
def get_confirmations(tx_block_height: int, current_height: int) -> int:
    if tx_block_height is None:
        return 0  # Pending
    return max(0, current_height - tx_block_height + 1)
```

### Reorg Detection

```python
async def detect_reorg(tx):
    """Check if a previously confirmed tx is still in the canonical chain."""
    if tx.block_hash is None:
        return False
    
    # Query block at tx's height
    block = await rpc.getBlockByNumber(tx.block_height)
    
    # If hash differs, block was reorged out
    if block is None or block["hash"] != tx.block_hash:
        return True
    
    return False
```

---

## 6. Signing and Broadcast Flow

### Wallet Decision: Node Keys vs Wallet Keys

**Preferred:** Wallet manages keys (already implemented)

The Qt wallet:
- ✅ Generates Dilithium3 keypairs via AccountManager
- ✅ Stores encrypted keys in AES-256 keystore
- ✅ Signs transactions in-memory when unlocked
- ✅ Uses node only for broadcast and state queries

**Not used:** Node-side signing (would require exposing keys to node process)

### Complete Send Flow

```cpp
// 1. User fills send form
QString from_account_id = ui->fromComboBox->currentData().toString();
QString to_address = ui->toAddressEdit->text();  // "anim1..."
qint64 amount_wei = ui->amountEdit->value() * 1e9;  // Convert ANM to wei
qint64 gas_limit = 21000;  // Standard transfer
qint64 gas_price = feeEstimator->getGasPrice(FeeTier::Normal);

// 2. Validate inputs
if (!validateAddress(to_address)) {
    showError("Invalid recipient address");
    return;
}

// 3. Get current nonce from node
qint64 nonce = m_rpcClient->getNonce(from_account->address, "pending");

// 4. Build unsigned transaction
QJsonObject unsigned_tx;
unsigned_tx["version"] = 1;
unsigned_tx["chain_id"] = m_chainId;
unsigned_tx["fork_id"] = QJsonValue::Null;
unsigned_tx["sender"] = addressToHex(from_account->address);
unsigned_tx["nonce"] = nonce;
unsigned_tx["gas_price"] = gas_price;
unsigned_tx["gas_limit"] = gas_limit;
unsigned_tx["kind"] = 0;  // TRANSFER
unsigned_tx["payload"] = QJsonObject{
    {"to", addressToHex(to_address)},
    {"amount", QString::number(amount_wei)},
    {"data", ""}
};

// 5. Sign transaction (delegates to WalletEngine)
QString signed_tx_hex = m_walletEngine->signTransaction(
    unsigned_tx,
    from_account_id
);

if (signed_tx_hex.isEmpty()) {
    showError("Signing failed");
    return;
}

// 6. Broadcast to node
QString tx_hash = m_rpcClient->sendRawTransaction(signed_tx_hex);

if (tx_hash.isEmpty()) {
    showError("Broadcast failed: " + m_rpcClient->lastError());
    return;
}

// 7. Show confirmation dialog
showSuccess("Transaction sent!\nTX: " + tx_hash);

// 8. Start monitoring (poll for receipt)
m_txMonitor->addPendingTx(tx_hash, from_account->address, to_address, amount_wei);
```

### WalletEngine::signTransaction Implementation

```cpp
QString WalletEngine::signTransaction(const QJsonObject& txJson, 
                                      const QString& fromAccountId)
{
    if (m_locked) {
        emit error("Wallet is locked");
        return QString();
    }
    
    // 1. Get account from keystore
    WalletAccount account = m_accountManager->getAccount(fromAccountId);
    if (!account.isValid()) {
        emit error("Account not found");
        return QString();
    }
    
    // 2. Build sign bytes via Python helper
    QByteArray signBytes = buildSignBytes(txJson);
    
    // 3. Sign with PQ algorithm (calls pq.py via subprocess)
    PqSignature sig = signDetached(
        signBytes,
        account.privateKey,
        account.algorithm,
        txJson["chain_id"].toInt()
    );
    
    // 4. Build signed transaction
    QJsonObject signed_tx;
    signed_tx["tx"] = txJson;
    signed_tx["sigs"] = QJsonArray{sig.toJson()};
    
    // 5. Encode to CBOR
    QByteArray cbor = encodeCBOR(signed_tx);
    
    // 6. Return as hex
    return "0x" + cbor.toHex();
}
```

---

## 7. Fee Estimation

### Strategy

The node does not expose a dedicated fee estimation endpoint, so the wallet implements heuristics:

```cpp
class FeeEstimator {
public:
    enum FeeTier { Slow, Normal, Fast };
    
    qint64 getGasPrice(FeeTier tier) {
        // Base fee from chain params
        qint64 base_fee = getBaseFee();
        
        switch (tier) {
        case Slow:
            return base_fee;  // Minimum
        case Normal:
            return base_fee * 2;  // 2x for faster inclusion
        case Fast:
            return base_fee * 5;  // 5x for priority
        }
    }
    
private:
    qint64 getBaseFee() {
        // Query chain params or use conservative default
        QJsonObject params = m_rpcClient->call("chain.getParams");
        return params["min_gas_price"].toVariant().toLongLong();
    }
};
```

### Gas Limits

Standard values:
- **Transfer:** 21,000 gas
- **Contract call:** Estimate from dry-run or use user override
- **Contract deploy:** 2,000,000+ gas (depends on code size)

### Fee Display

```cpp
QString formatFee(qint64 gas_price, qint64 gas_limit) {
    qint64 fee_wei = gas_price * gas_limit;
    double fee_anm = fee_wei / 1e9;
    return QString("%1 ANM").arg(fee_anm, 0, 'f', 6);
}
```

---

## 8. Error Handling

### Error Surface Categories

#### 1. Pre-submission validation

Check locally before calling RPC:
- Invalid recipient address (checksum, length)
- Insufficient balance (query balance first)
- Zero amount
- Wallet locked
- Node not synced

#### 2. RPC submission errors

Map error codes to user-friendly messages:

| Code | Name | User Message | Recovery |
|------|------|--------------|----------|
| -32010 | InvalidTx | "Transaction format is invalid" | Check inputs |
| -32011 | ChainIdMismatch | "Wrong network selected" | Switch network |
| -32012 | SignatureInvalid | "Signature verification failed" | Retry signing |
| -32013 | FeeTooLow | "Gas price too low" | Increase fee tier |
| -32014 | NonceGap | "Nonce conflict detected" | Refresh nonce |
| -32015 | Oversize | "Transaction too large" | Reduce data size |
| -32016 | Duplicate | "Transaction already submitted" | Check mempool |

#### 3. Broadcast verification

After successful submission:
- Poll for tx in mempool (max 30s)
- If not found, offer "Rebroadcast" button
- Track peer propagation (if node exposes gossip stats)

#### 4. Confirmation failures

- Tx stuck pending > 10 minutes: Offer rebroadcast or replace-by-fee (if supported)
- Tx dropped from mempool: Show reason (evicted, invalid, conflict)
- Reorg detected: Update confirmations, may return to pending

### Example Error Handler

```cpp
void SendWidget::handleSendError(const QString& error_msg, int error_code) {
    QString user_msg;
    QString recovery_hint;
    
    switch (error_code) {
    case -32011:
        user_msg = "Wrong network! Expected devnet (chain ID 1337).";
        recovery_hint = "Switch to devnet in settings.";
        break;
    case -32013:
        user_msg = "Gas price too low. Transaction may not be included.";
        recovery_hint = "Try 'Fast' fee tier.";
        break;
    case -32014:
        user_msg = "Nonce conflict. Another transaction is pending.";
        recovery_hint = "Wait for pending transaction to confirm.";
        break;
    default:
        user_msg = error_msg;
        recovery_hint = "Check node logs for details.";
    }
    
    QMessageBox::warning(
        this,
        "Transaction Failed",
        user_msg + "\n\n" + recovery_hint
    );
}
```

---

## Summary

This document provides a complete reference for transaction handling in the Animica Qt wallet:

- ✅ **Transaction format:** CBOR-encoded UnsignedTx/Tx with PQ signatures
- ✅ **Signing:** Dilithium3 domain-separated signing in wallet, not node
- ✅ **Addresses:** Bech32m "anim1..." with algorithm ID + pubkey digest
- ✅ **RPC:** tx.sendRawTransaction, state.getNonce, getBalance, getReceipt
- ✅ **Lifecycle:** Pending → Mined → Confirmed → Final (with reorg handling)
- ✅ **Fees:** Heuristic tiers (Slow/Normal/Fast) based on chain params
- ✅ **Errors:** Comprehensive mapping of RPC error codes to user actions

**Next steps:** Implement UI dialogs (SendWidget, ReceiveWidget), TransactionMonitor class, and wallet database for tx journal/ledger.
