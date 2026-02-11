# Animica Browser Wallet Extension - PQ Implementation Plan

**Status:** MOCK Implementation (Development Only)  
**Target:** Production-Ready PQ Backend  
**Policy:** NO LIBOQS (see `docs/PQ_POLICY.md`)

---

## Current State

The wallet extension has a **MOCK** PQ implementation in `src/core/crypto/pq.ts` that:
- ✅ Provides correct interface and key sizes
- ✅ Allows UI development and testing
- ❌ **NOT SECURE** - uses random bytes and mock signatures
- ❌ **NOT COMPATIBLE** with node verification
- ❌ **DO NOT USE IN PRODUCTION**

---

## Implementation Options

To make this production-ready, we must implement **real Dilithium3** signing and verification. Per `docs/PQ_POLICY.md`, we **CANNOT** use liboqs. Instead, choose one of these approaches:

### Option 1: TypeScript Port (Recommended)

**Pros:**
- Native TypeScript, no WASM overhead
- Easier debugging and maintenance
- Works in all browser environments

**Cons:**
- Large porting effort (~2000 lines of complex math)
- Performance may be slower than native/WASM

**Steps:**
1. Port `python/animica/_vendor/dilithium_py/dilithium3.py` to TypeScript
2. Port dependencies: NTT (Number-Theoretic Transform), polynomial arithmetic
3. Add comprehensive test vectors from Python CLI
4. Validate byte-for-byte compatibility

**Estimated Effort:** 2-3 weeks for a careful port with tests

### Option 2: WASM Compilation

**Pros:**
- Reuse existing Python implementation directly
- Good performance
- Deterministic (same code as node)

**Cons:**
- Requires build toolchain (Emscripten, Pyodide)
- Larger bundle size
- More complex build process

**Steps:**
1. Use Pyodide or compile Python to WASM with Emscripten
2. Create JS wrapper for `dilithium3.py` functions
3. Bundle WASM in extension
4. Test in extension environment (content security policy, etc.)

**Estimated Effort:** 1-2 weeks for integration + testing

### Option 3: Hybrid (Recommended for MVP)

**Pros:**
- Fast to implement
- Reuses trusted Python implementation
- Can be replaced with pure TS later

**Cons:**
- Bundle size increase (Pyodide is ~6MB)
- First-load latency

**Steps:**
1. Bundle Pyodide WASM runtime in extension
2. Load `python/animica/_vendor/dilithium_py/dilithium3.py` at runtime
3. Expose Python functions via JS bridge
4. Cache WASM for fast subsequent loads

**Estimated Effort:** 1 week

---

## Implementation Plan

### Phase 1: Prepare Python Bridge

1. **Extract Dilithium3 module** from `python/animica/_vendor/dilithium_py/`
2. **Create wrapper** that exposes:
   ```python
   def keygen(seed: bytes = None) -> dict:
       # Returns {"public_key": bytes, "secret_key": bytes}
   
   def sign(secret_key: bytes, message: bytes) -> bytes:
       # Returns signature bytes
   
   def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
       # Returns True/False
   ```
3. **Test wrapper** standalone in Python

### Phase 2: Integrate with Extension

1. **Bundle Pyodide** in `src/core/crypto/pyodide/`
2. **Create bridge** in `src/core/crypto/pq-bridge.ts`:
   ```typescript
   export async function initPQ(): Promise<void>;
   export async function dilithium3_keygen(): Promise<KeyPair>;
   export async function dilithium3_sign(sk: Uint8Array, msg: Uint8Array): Promise<Uint8Array>;
   export async function dilithium3_verify(pk: Uint8Array, msg: Uint8Array, sig: Uint8Array): Promise<boolean>;
   ```
3. **Replace mock** in `pq.ts` with bridge calls
4. **Add loading UI** for first initialization

### Phase 3: Testing

1. **Generate test vectors** from Python CLI:
   ```bash
   animica wallet create --label test-wallet-extension --alg dilithium3
   animica wallet export --label test-wallet-extension --output test-vector.json
   echo "test message" | animica wallet sign --label test-wallet-extension --output sig.json
   ```
2. **Load test vectors** in extension tests
3. **Verify** signing produces identical output
4. **Test** node verification of wallet signatures

### Phase 4: Optimization

1. **Cache compiled WASM** in extension storage
2. **Lazy load** PQ backend (only when needed)
3. **Add progress indicators** for slow operations
4. **Consider TS port** for better performance (future)

---

## File Structure

```
apps/wallet-extension/
├── src/
│   ├── core/
│   │   ├── crypto/
│   │   │   ├── pq.ts                 # Public API (currently MOCK)
│   │   │   ├── pq-bridge.ts          # Python/WASM bridge (TO ADD)
│   │   │   ├── pq-pyodide.ts         # Pyodide loader (TO ADD)
│   │   │   ├── address.ts            # Bech32m address derivation
│   │   │   └── vault.ts              # Wallet storage
│   │   └── wallets/
│   │       └── wallet-manager.ts     # Wallet CRUD
│   ├── provider/
│   │   └── animica-provider.ts       # window.animica API
│   ├── ui/
│   │   ├── popup/                    # Extension popup UI
│   │   └── pages/                    # Full-page UI
│   └── background/
│       └── service-worker.ts         # Background script
├── assets/
│   ├── pyodide/                      # Pyodide WASM runtime (TO ADD)
│   └── python/                       # Python modules (TO ADD)
│       └── dilithium3.py
├── docs/
│   ├── PQ_IMPLEMENTATION.md          # This file
│   └── PROVIDER_API.md               # window.animica API spec
└── tests/
    ├── pq.test.ts                    # PQ crypto tests
    └── test-vectors/                 # Generated from Python CLI
        ├── dilithium3-keygen.json
        ├── dilithium3-sign.json
        └── dilithium3-verify.json
```

---

## Provider API (`window.animica`)

The wallet extension must expose this API to Dapp IDE:

```typescript
interface AnimicaProvider {
  // Network
  chainId: string | null;
  networkVersion: string | null;
  
  // Methods
  request(args: RequestArguments): Promise<any>;
  
  // Events
  on(event: string, handler: (...args: any[]) => void): void;
  off(event: string, handler: (...args: any[]) => void): void;
}

interface RequestArguments {
  method: string;
  params?: any[];
}

// Supported methods:
// - animica_requestAccounts
// - animica_accounts
// - animica_chainId
// - animica_switchChain
// - animica_signTx
// - animica_sendTx
// - animica_signBytes

// Events:
// - accountsChanged
// - chainChanged
// - networkChanged
```

See `docs/PROVIDER_API.md` for full specification.

---

## Security Considerations

### Key Storage

- **Format:** wallets.json schema (see `apps/dapp-ide/docs/PQ_DISCOVERY.md`)
- **Encryption:** AES-256-GCM with password-derived key (PBKDF2, 100k iterations)
- **Storage:** Chrome extension storage API (encrypted at rest by OS)
- **Export:** Allow encrypted export/import of wallets.json
- **Permissions:** No external access to secret keys

### Transaction Approval

- **UI:** Full-screen approval dialog (not popup to prevent clickjacking)
- **Display:** Show all tx fields clearly (from, to, value, gas, data)
- **SignBytes:** Display hash of SignBytes for verification
- **Timeout:** Auto-reject after 5 minutes
- **Rate limiting:** Max 10 signatures per minute

### Network Configuration

- **Default mainnet RPC:** `http://144.126.133.21:8545/rpc`
- **Devnet RPC:** `http://127.0.0.1:8545/rpc`
- **Custom RPCs:** User can add, with warning about risks
- **Chain ID verification:** Always verify chain ID matches network

---

## Development Workflow

### 1. Setup

```bash
cd apps/wallet-extension
pnpm install
pnpm dev  # Watch mode
```

### 2. Load in Browser

1. Open Chrome/Firefox extension management
2. Enable developer mode
3. Load unpacked extension from `apps/wallet-extension/dist`
4. Open popup to test

### 3. Test with Dapp IDE

```bash
# In separate terminal
cd apps/dapp-ide
pnpm dev  # Starts on http://localhost:5173
```

Open Dapp IDE, click "Connect Wallet", select Animica extension.

### 4. Generate Test Vectors

```bash
# In repo root
source .venv/bin/activate
animica wallet create --label test-dilithium3 --alg dilithium3
animica wallet export --label test-dilithium3 --show-secret > apps/wallet-extension/tests/test-vectors/dilithium3.json
```

### 5. Run Tests

```bash
cd apps/wallet-extension
pnpm test
```

---

## Next Steps

1. **Choose implementation approach** (Option 1, 2, or 3)
2. **Set up Pyodide** if using Option 2 or 3
3. **Create Python wrapper** for Dilithium3
4. **Build bridge** in TypeScript
5. **Replace mock** with real implementation
6. **Generate test vectors** from CLI
7. **Validate** against Python outputs
8. **Test end-to-end** with node verification

---

## References

- **PQ Policy:** `docs/PQ_POLICY.md`
- **PQ Discovery:** `apps/dapp-ide/docs/PQ_DISCOVERY.md`
- **Python Implementation:** `python/animica/_vendor/dilithium_py/dilithium3.py`
- **Address Derivation:** `pq/py/address.py`
- **Signing Logic:** `pq/py/sign.py`
- **Wallet Format:** `python/animica/cli/wallet.py`

---

## Questions?

See `docs/PQ_POLICY.md` for the NO LIBOQS policy and rationale.

For implementation help, consult:
- Python CLI source code
- `@animica/crypto` package (TypeScript utilities)
- Test vectors from CLI
