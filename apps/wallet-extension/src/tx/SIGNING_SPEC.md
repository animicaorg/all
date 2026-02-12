# Transaction Signing Specification

This document defines the canonical transaction signing process used by Animica nodes.
The wallet extension MUST follow these exact rules to produce valid signatures.

## Overview

Transaction signatures in Animica use post-quantum (PQ) cryptography with:
- **Dilithium3** (scheme_id=1, alg_id=4097/0x1001)
- **SPHINCS+ SHAKE-128s** (scheme_id=2, alg_id=4098/0x1002)

## Canonical Signing Process

### Step 1: Build Transaction Body

The transaction body MUST contain these fields in canonical form:

```typescript
{
  version: number,        // Always 1 for current version
  chain_id: number,       // Network chain ID
  nonce: number,          // Transaction sequence number
  from_addr: bytes,       // Sender address (raw bytes, not bech32)
  to_addr: bytes,         // Recipient address (raw bytes, not bech32)
  value: number,          // Amount to transfer (in smallest unit)
  fee: number,            // Transaction fee
  gas_limit: number,      // Maximum gas allowed
  data: bytes,            // Additional data (empty bytes if none)
  memo: string,           // Human-readable memo (empty string if none)
  timestamp: number,      // Unix timestamp in seconds
  kind: number,           // Transaction kind (0 = transfer, 1 = contract call, etc.)
}
```

**Important**: All numeric fields MUST be encoded as CBOR integers. Address fields MUST be raw bytes.

### Step 2: Build Signing Preimage

The signing preimage wraps the tx body with domain separation and chain context:

```typescript
preimage = CBOR({
  1: "animica.tx.v1",      // domain (string) - note the DOT not slash
  2: chain_id,             // chain ID (integer)
  3: genesis_hash,         // genesis block hash (bytes, 32 bytes)
  4: network,              // network name (string, e.g., "mainnet", "testnet")
  5: "tx",                 // message type (string)
  6: version,              // tx version (integer, typically 1)
  7: body                  // tx body (map as defined above)
})
```

**Critical Points**:
- Domain MUST be `"animica.tx.v1"` (with dot `.` not slash `/`)
- Fields are keyed by integers 1-7, not strings
- genesis_hash is REQUIRED (32-byte hash of genesis block)
- network is REQUIRED (string identifier)
- CBOR encoding MUST be canonical (deterministic key ordering)

### Step 3: Compute Sign Hash

```typescript
sign_hash = SHA3-512(preimage)
```

The sign hash is a 64-byte digest used as input to PQ signing.

**Critical Point**: Use SHA3-512, not SHA3-256.

### Step 4: Sign

```typescript
signature = PQ_SIGN(sign_hash, secret_key, scheme_id)
```

The signature algorithm depends on scheme_id:
- **Dilithium3** (scheme_id=1): Signs the 64-byte sign_hash directly
- **SPHINCS+** (scheme_id=2): Signs the 64-byte sign_hash directly

**Never** sign:
- Hex strings (e.g., "0xabc...")
- UTF-8 encoded strings
- JSON representations

Always sign the **raw 64-byte hash**.

### Step 5: Build Transaction Envelope

The final envelope format:

```typescript
{
  body: {
    // all fields from Step 1
  },
  auth: {
    scheme_id: number,      // 1=Dilithium3, 2=SPHINCS+
    pubkey_bytes: bytes,    // Public key (raw bytes)
    signature_bytes: bytes, // Signature (raw bytes)
    prehash_id: number,     // 2 (for SHA3-512)
  },
  txid: bytes              // SHA3-256 of canonical CBOR(envelope)
}
```

The envelope is encoded as canonical CBOR for transmission.

## Key Lengths

| Scheme | Public Key | Signature |
|--------|-----------|-----------|
| Dilithium3 (1) | 1952 bytes | 3293 bytes |
| SPHINCS+ (2) | 32 bytes | 7856 bytes |

## Address Binding

Before signing, the wallet MUST verify:

```
derived_address = address_from_pubkey(public_key, scheme_id)
if derived_address != from_addr:
  throw Error("Address/pubkey mismatch")
```

This prevents signing with the wrong key.

## Chain Context Retrieval

The wallet must fetch chain context from the RPC:

```typescript
const identity = await rpc.call('chain.getChainIdentity', []);
const context = {
  chain_id: identity.chainId,
  genesis_hash: hexToBytes(identity.genesisHash),
  network: identity.network,
  fork_id: identity.forkId || null,
};
```

## Example Flow

```typescript
// 1. Fetch chain context
const identity = await rpc.call('chain.getChainIdentity', []);

// 2. Build tx body
const body = {
  version: 1,
  chain_id: identity.chainId,
  nonce: await rpc.call('state.getNonce', [from_addr]),
  from_addr: addressToBytes(from_addr),
  to_addr: addressToBytes(to_addr),
  value: amount,
  fee: gasPrice,
  gas_limit: gasLimit,
  data: new Uint8Array(),
  memo: "",
  timestamp: Math.floor(Date.now() / 1000),
  kind: 0,
};

// 3. Build preimage
const preimage = encodeCBOR({
  1: "animica.tx.v1",
  2: identity.chainId,
  3: hexToBytes(identity.genesisHash),
  4: identity.network,
  5: "tx",
  6: 1,
  7: body,
});

// 4. Compute sign hash
const sign_hash = sha3_512(preimage);

// 5. Sign
const signature = await pq_sign(sign_hash, secret_key, scheme_id);

// 6. Build envelope
const auth = {
  scheme_id,
  pubkey_bytes: public_key,
  signature_bytes: signature,
  prehash_id: 2, // SHA3-512
};
const envelope = { body, auth, txid: new Uint8Array(32) };

// 7. Compute txid
const txid = sha3_256(encodeCBOR(envelope));
envelope.txid = txid;

// 8. Encode and send
const rawTx = "0x" + bytesToHex(encodeCBOR(envelope));
const result = await rpc.call('tx.sendRawTransaction', [rawTx]);
```

## Verification

The node verifies signatures by:

1. Extracting `body`, `auth` from envelope
2. Recomputing preimage from body + chain context
3. Computing sign_hash = SHA3-512(preimage)
4. Verifying `PQ_VERIFY(sign_hash, signature_bytes, pubkey_bytes, scheme_id)`
5. Checking derived address matches `body.from_addr`

If ANY step fails, the tx is rejected with error code -32012.

## Common Mistakes to Avoid

1. ❌ Using domain `"animica/tx.v1"` instead of `"animica.tx.v1"`
2. ❌ Concatenating domain + tx bytes instead of wrapping in CBOR structure
3. ❌ Missing genesis_hash or network in preimage
4. ❌ Using SHA3-256 for sign_hash instead of SHA3-512
5. ❌ Signing hex strings instead of raw bytes
6. ❌ Not validating address/pubkey binding before signing
7. ❌ Using wrong scheme_id for wallet's key type
