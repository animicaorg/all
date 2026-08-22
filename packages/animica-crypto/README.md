# @animica/crypto

**Animica Custom Post-Quantum Cryptography Library**

This package provides browser-safe implementations of Animica's custom PQ cryptography, based on the repository's own pure-Python implementations. **This package NEVER uses liboqs or any third-party PQ libraries.**

## Features

- ✅ **Dilithium3** (ML-DSA-65) - CRYSTALS-Dilithium lattice-based signatures
- ✅ **SPHINCS+ SHAKE-128s** - Stateless hash-based signatures  
- ✅ **Bech32m address derivation** - Animica `anim1...` addresses
- ✅ **Domain-separated signing** - Prevents cross-domain signature reuse
- ✅ **Transaction signBytes** - Compatible with Animica node validation
- ✅ **Pure TypeScript** - No native dependencies, works in browsers and Node.js
- ✅ **Test vectors** - Validated against Python CLI outputs

## NO LIBOQS POLICY

This package is built from the ground up using Animica's custom PQ implementations. It does NOT depend on:
- ❌ liboqs
- ❌ oqs
- ❌ open-quantum-safe
- ❌ pqclean
- ❌ Any other third-party PQ library

All cryptographic primitives are implemented in pure TypeScript or compiled from Animica's repository code.

## Installation

```bash
pnpm add @animica/crypto
```

## Usage

### Generate Keypair

```typescript
import { generateKeypair, AlgorithmId } from '@animica/crypto';

const keypair = generateKeypair(AlgorithmId.DILITHIUM3);
console.log('Address:', keypair.address);
console.log('Public key:', Buffer.from(keypair.publicKey).toString('hex'));
```

### Sign Message

```typescript
import { sign } from '@animica/crypto';

const signature = sign({
  algorithm: AlgorithmId.DILITHIUM3,
  secretKey: keypair.secretKey,
  message: new TextEncoder().encode('hello world'),
  domain: 'test',
  chainId: 1337
});
```

### Verify Signature

```typescript
import { verify } from '@animica/crypto';

const isValid = verify({
  publicKey: keypair.publicKey,
  message: new TextEncoder().encode('hello world'),
  signature,
  domain: 'test',
  chainId: 1337
});
console.log('Valid:', isValid);
```

### Derive Address

```typescript
import { addressFromPubkey, AlgorithmId } from '@animica/crypto';

const address = addressFromPubkey(AlgorithmId.DILITHIUM3, keypair.publicKey);
console.log('Address:', address); // anim1qq...
```

### Build Transaction SignBytes

```typescript
import { txSignBytes } from '@animica/crypto';

const signBytes = txSignBytes({
  tx: {
    from: 'anim1qq...',
    to: 'anim1qq...',
    value: '1000000000000000000',
    nonce: 42,
    gasLimit: { amount: 21000 }
  },
  chainId: 1337,
  domain: 'animica.tx.v1'
});
```

## Algorithm IDs

| Algorithm | ID | Public Key | Secret Key | Signature |
|-----------|-----|-----------|------------|-----------|
| Dilithium3 | `0x1001` (4097) | 1952 bytes | 4000 bytes | 3293 bytes |
| SPHINCS+ SHAKE-128s | `0x1002` (4098) | 64 bytes | 64 bytes | 7856 bytes |

## API Reference

See inline documentation for full API details.

## Testing

```bash
# Run tests
pnpm test

# Run with coverage
pnpm test:coverage

# Watch mode
pnpm test:watch
```

## Test Vectors

Golden test vectors are generated from the Animica CLI and stored in `test-vectors/`. These ensure byte-for-byte compatibility with the Python node implementation.

## Security

- All signatures use domain separation to prevent cross-domain attacks
- SignBytes construction matches the node's verification exactly
- Secret keys are handled as Uint8Array and should be zeroed after use
- Address derivation uses SHA3-256 (Keccak-256)
- Signing uses SHA3-512 (Keccak-512) prehashing

## License

MIT

## Contributing

This package must maintain compatibility with Animica's Python PQ implementation. Any changes should be validated against test vectors generated from the CLI.

**NEVER add liboqs or third-party PQ dependencies.**

## HD derivation (BIP-39 / BIP-44 / SLIP-0010 → ML-DSA-65)

`src/hd.ts` derives the 32-byte ML-DSA-65 seed ξ for an account from a BIP-39 mnemonic along
`m/44'/4279885'/account'/0'/index'` (SLIP-0044 coin type `4279885` = ASCII "ANM"), using
SLIP-0010 ed25519-style hardened derivation. Feed ξ to `ml_dsa65.keygen(ξ)` (FIPS 204
`KeyGen_internal`) and `addressFromPubkey(AlgorithmId.ML_DSA_65, pk)`. Spec + test vectors:
[`docs/wallet/HD_DERIVATION.md`](../../docs/wallet/HD_DERIVATION.md).

Get ANM: mine at [pool.animica.org](https://pool.animica.org) or trade on
[NonKYC ANM/USDT](https://nonkyc.io/market/ANM_USDT).
