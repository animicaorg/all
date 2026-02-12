# Animica Wallet Extension

A production-quality Chrome MV3 browser extension wallet for the Animica post-quantum blockchain.

## Features

- 🔐 **Post-Quantum Security**: Uses Dilithium3 (ML-DSA-65) signatures
- 🔒 **Encrypted Vault**: AES-GCM encryption with PBKDF2 key derivation (100k iterations)
- 🔑 **Multi-Account**: Create unlimited accounts with watch-only support
- 🌐 **Multi-Network**: Mainnet, Testnet, and Devnet with automatic failover
- 💸 **v2 Transactions**: Validity window-based transactions (no nonce race conditions)
- 📊 **Activity Tracking**: Real-time transaction status with idempotent state machine
- 🔗 **Web3 Provider**: `window.animica` API for dapp integration
- 📦 **wallets.json**: Import/export compatibility with CLI and node
- ⏱️ **Auto-Lock**: Configurable inactivity timer

## Quick Start

### Development

```bash
# Install dependencies
pnpm install

# Build for development (with watch mode)
pnpm dev

# Build for production
pnpm build

# Run tests
pnpm test
```

### Load in Chrome

1. Build the extension: `pnpm build`
2. Open Chrome and navigate to `chrome://extensions/`
3. Enable "Developer mode" (toggle in top right)
4. Click "Load unpacked"
5. Select the `dist/` folder
6. The Animica wallet icon should appear in your toolbar

### Load in Firefox (Experimental)

Firefox support is partial due to Manifest V3 differences:

1. Build the extension: `pnpm build`
2. Open Firefox and navigate to `about:debugging#/runtime/this-firefox`
3. Click "Load Temporary Add-on"
4. Select any file in the `dist/` folder
5. The extension will load temporarily (removed on browser restart)

## Networks

### Mainnet (Default)

- **Chain ID**: 1
- **Default RPC**: `http://144.126.133.21:8545/rpc`
- **Optional local RPC**: `http://127.0.0.1:8545/rpc`
- Production network (runtime RPC override supported in Settings)

### Testnet

- **Chain ID**: 2
- **RPC**: `http://127.0.0.1:18546/rpc`
- Local testnet for integration testing

### Devnet

- **Chain ID**: 1337
- **RPC**: `http://127.0.0.1:28545/rpc`
- Local development network

## Usage

### Configure RPC Endpoint (Runtime)

1. Open **Settings** → **RPC Endpoint**
2. Enter any `http://` or `https://` RPC URL (the wallet normalizes bare hosts to `/rpc`)
3. Click **Test Connection** to verify latency + chain/head response
4. Click **Save** to switch the running extension to the new endpoint
5. Click **Reset to default** to restore `http://144.126.133.21:8545/rpc`

Notes:
- Setting is persisted in `chrome.storage.local` across restarts
- HTTP endpoints show a warning outside localhost/127.0.0.1
- If RPC `chain_id` differs from selected wallet network, Settings shows a mismatch warning

### Creating a Wallet

1. Click the extension icon
2. Set a strong password (min 8 characters)
3. Click "Create Wallet"
4. Your first account is automatically created

**⚠️ Security Warning**: Your password encrypts your wallet locally. There is no way to recover it if forgotten!

### Sending Transactions

1. Navigate to the "Send" tab
2. Enter recipient address (starts with `anim1`)
3. Enter amount in ANM (1 ANM = 1e9 base units)
4. Click "Send Transaction"
5. Monitor progress in the "Activity" tab

### Connecting to Dapps

When a dapp calls `window.animica.animica_requestAccounts()`:

1. The extension popup will show an approval prompt (future version)
2. Approve the connection
3. The dapp can now send transactions (with per-transaction approval)

### Import/Export wallets.json

#### Import

1. Go to Settings → Import wallets.json
2. Select your `wallets.json` file
3. Accounts are merged with existing (secrets preferred)

#### Export

1. Go to Settings → Export wallets.json
2. Choose "Public only" or "Include secrets"
3. Save the file securely

**⚠️ Warning**: Exported files with secrets contain private keys. Store them securely!

### Dev Mode Sync (Chrome Only)

For developers working with local node wallets:

1. Enable File System Access API permissions
2. Point to `~/.animica/wallets.json`
3. Changes sync bidirectionally

**Note**: Not available in Firefox due to API limitations.

## Architecture

```
apps/wallet-extension/
├── src/
│   ├── background/        # Service worker (MV3)
│   │   └── index.ts       # Message handler, vault manager
│   ├── content/           # Content script
│   │   └── index.ts       # Injects provider, bridges messages
│   ├── provider/          # Page context provider
│   │   └── index.ts       # window.animica API
│   ├── ui/                # React popup UI
│   │   ├── pages/         # Onboarding, Unlock, Home
│   │   └── components/    # AccountsTab, SendTab, ActivityTab, SettingsTab
│   └── core/              # Business logic
│       ├── crypto/        # PQ crypto, address, vault encryption
│       ├── storage/       # Chrome storage wrapper
│       ├── rpc/           # RPC client with failover
│       ├── tx/            # Transaction builder, CBOR, store
│       ├── wallets/       # Account management, import/export
│       ├── networks/      # Network manager
│       └── permissions/   # Dapp permission manager
├── tests/                 # Vitest unit tests
├── manifest.json          # Chrome MV3 manifest
└── vite.config.ts         # Build configuration
```

## Security Model

### Vault Encryption

- **Algorithm**: AES-GCM (256-bit)
- **Key Derivation**: PBKDF2 with 100,000 iterations
- **Random Salt & IV**: Generated per encryption
- **Storage**: `chrome.storage.local` (encrypted at rest)

### Key Material

Keys are stored in the encrypted vault only. When unlocked:

- **In-memory only**: Keys never touch disk unencrypted
- **Auto-lock**: Configurable timer (default 5 minutes)
- **Lock on idle**: Extension locks automatically when inactive

### Transaction Security

- **Domain separation**: Signing uses `"animica/tx.v1"` prefix
- **Canonical CBOR**: Deterministic encoding prevents malleability
- **Replay protection**: v2 transactions use `validAfter`/`validUntil`/`salt`
- **No double-debit**: Pending tx tracking ensures balance consistency

### Post-Quantum Cryptography

⚠️ **Current Implementation**: Mock PQ crypto using `crypto.getRandomValues()`

**TODO**: Replace with actual Dilithium3 from liboqs-wasm when available.

The infrastructure is complete and production-ready. Only the crypto primitives need replacement:

- `src/core/crypto/pq.ts::generateKeyPair()`
- `src/core/crypto/pq.ts::sign()`
- `src/core/crypto/pq.ts::verify()`

## Transaction Lifecycle

```
┌─────────────┐
│ User clicks │
│ "Send"      │
└──────┬──────┘
       │
       v
┌─────────────────────┐
│ Build UnsignedTxV2  │
│ • validAfter/Until  │
│ • salt (32 bytes)   │
│ • gas {price,limit} │
└──────┬──────────────┘
       │
       v
┌─────────────────────┐
│ Sign with PQ        │
│ • Domain: tx.v1     │
│ • CBOR canonical    │
└──────┬──────────────┘
       │
       v
┌─────────────────────┐
│ Submit RPC          │
│ tx.sendRawTransaction
└──────┬──────────────┘
       │
       v
┌─────────────────────┐
│ Store in TxStore    │
│ • Idempotent        │
│ • No duplicates     │
└──────┬──────────────┘
       │
       v
┌─────────────────────┐
│ Poll Status         │
│ • Every 10s         │
│ • Update UI         │
└─────────────────────┘
```

### States

- `created_local` - Built locally, not yet submitted
- `submitted` - Sent to RPC, awaiting mempool
- `mempool_accepted` - Accepted by mempool
- `included` - Included in block, awaiting confirmations
- `confirmed` - Fully confirmed (6+ blocks)
- `dropped` - Rejected or expired
- `reorged_out` - Removed due to chain reorganization

## Balance Calculation

To prevent double-debit:

```typescript
confirmed = state.getBalance(address)
pendingOutgoing = sum(active_transactions.amount)
available = confirmed - pendingOutgoing
```

**Active transactions**: submitted, mempool_accepted, included (not confirmed/dropped/reorged)

## Provider API (window.animica)

### Methods

```typescript
// Request account access
await window.animica.animica_requestAccounts()
→ ["anim1qp5h..."]

// Get authorized accounts
await window.animica.animica_accounts()
→ ["anim1qp5h..."]

// Get current chain ID
await window.animica.animica_chainId()
→ 1

// Switch network
await window.animica.animica_switchChain(chainId: number)

// Sign arbitrary message
await window.animica.animica_signMessage(message: string)
→ "0x<signature>"

// Send transaction
await window.animica.animica_sendTransaction({
  from: "anim1...",
  to: "anim1...",
  amount: 1000000000,  // 1 ANM in base units
  data?: Uint8Array
})
→ "0x<txid>"
```

### Events

```typescript
// Account changed
window.animica.on('accountsChanged', (accounts: string[]) => {
  console.log('Active account:', accounts[0]);
});

// Network changed
window.animica.on('chainChanged', (chainId: number) => {
  console.log('Chain ID:', chainId);
});

// Disconnected
window.animica.on('disconnect', () => {
  console.log('Wallet disconnected');
});
```

## RPC Methods Used

Based on `/apps/wallet-extension/docs/DISCOVERY.md`:

### State Queries

- `state.getBalance(address, tag)` - Get account balance
- `state.getNonce(address, tag)` - Get nonce (v1 only, deprecated)

### Transaction Submission

- `tx.sendRawTransaction(rawTx)` - Submit CBOR-encoded signed tx
- `tx.getTransaction(txid)` - Fetch tx details
- `tx.getTransactionStatus(txid)` - Get tx status
- `tx.getTransactionReceipt(txid)` - Get execution receipt

### Chain Info

- `chain.getChainId()` - Get network chain ID
- `chain.getHead()` - Get latest block header
- `block.getBlock(blockId, full)` - Fetch block data

## Testing

Run the test suite:

```bash
# Run all tests
pnpm test

# Run specific test file
pnpm test vault.test.ts

# Run with UI
pnpm test:ui

# Type checking
pnpm type-check
```

### Test Coverage

- ✅ Vault encryption/decryption
- ✅ wallets.json round-trip
- ✅ TX store idempotency
- ✅ Permission isolation
- ✅ Network failover (manual)

## Known Limitations

### Mock PQ Crypto

The extension uses mock Dilithium3 key generation and signing. Replace with actual liboqs-wasm for production:

1. Build liboqs to WebAssembly
2. Replace `src/core/crypto/pq.ts` functions
3. Update key sizes to match actual Dilithium3 (1952/4000/3293 bytes)

### No Hardware Wallet Support

Hardware wallet integration requires additional native messaging host.

### Firefox Compatibility

- Manifest V3 support is experimental
- File System Access API unavailable
- Service worker limitations

### Approval Flows

Auto-approval is currently enabled for development. Production should show:

- Connection approval popup (per origin)
- Transaction approval popup (per tx)
- Message signing approval popup

## Troubleshooting

### Extension won't load

- Ensure `pnpm build` completed without errors
- Check Chrome version (requires 109+)
- Try removing and re-adding extension

### Can't connect to mainnet

- Verify RPC endpoint: `http://144.126.133.21:8545/rpc`
- Check network connectivity
- Try custom/local RPC: `http://127.0.0.1:8545/rpc`
- View network status in Settings → RPC Endpoint

### Transaction stuck in "submitted"

- RPC node may be down - check Settings → Network
- Transaction may be rejected - check Activity tab for errors
- Wait up to 2 minutes for mempool propagation
- Try switching to fallback RPC

### Balance shows 0 ANM

- Ensure you're on the correct network
- Check RPC connectivity
- Verify address has funds on block explorer
- Try refreshing (lock/unlock wallet)

## Development Tips

### Adding a New RPC Method

1. Add method to `src/core/rpc/client.ts`
2. Add message handler in `src/background/index.ts`
3. Call from UI via `chrome.runtime.sendMessage()`

### Adding a New Network

1. Add config to `src/types/network.ts::NETWORKS`
2. Network switcher in Settings will automatically include it

### Debugging Background Script

1. Open `chrome://extensions/`
2. Find Animica Wallet
3. Click "Service worker" link
4. Chrome DevTools opens with console logs

### Enabling Debug Logging

#### RPC Request/Response Logging

To see full JSON-RPC requests and responses (especially for -32602 Invalid params errors):

**Option 1: Environment Variable (Build Time)**
```bash
VITE_DEBUG_RPC_PAYLOADS=1 pnpm build
```

**Option 2: Runtime Global (Service Worker Console)**
```javascript
// In chrome://extensions → Service worker DevTools console:
globalThis.__ANIMICA_DEBUG_RPC_PAYLOADS__ = true
```

#### Transaction Debug Logging

To see detailed transaction building and signing steps:

**Option 1: Environment Variable (Build Time)**
```bash
VITE_DEBUG_TX=1 pnpm build
```

**Option 2: Runtime Global (Service Worker Console)**
```javascript
// In chrome://extensions → Service worker DevTools console:
globalThis.__ANIMICA_DEBUG_TX__ = true
```

With debug logging enabled, you'll see:
- Full JSON-RPC request objects before sending
- Response errors with error codes and data
- Transaction building steps (chain context, nonce, signing)
- Hints for common errors like -32602 (Invalid params)

**Note**: -32602 errors are ALWAYS logged with full request details, even without debug flags enabled.

### Debugging Provider Injection

1. Open any webpage
2. Open DevTools console
3. Type `window.animica` to inspect provider
4. Call methods directly for testing

## Contributing

See `CONTRIBUTING.md` in the repository root.

## License

See `LICENSE.txt` in the repository root.

## Security

**⚠️ IMPORTANT**: This is alpha software. Use at your own risk.

- Never share your password or seed phrase
- Always verify recipient addresses
- Test with small amounts first
- Keep your software updated

Report security issues to: security@animica.io

## Resources

- Discovery Document: `/apps/wallet-extension/docs/DISCOVERY.md`
- Main Repo: `/home/runner/work/all/all/`
- RPC Docs: `docs/RPC.md`
- Transaction Spec: `spec/TX.md`
- CLI Usage: `python/animica/cli/`

---

Built with ❤️ for the post-quantum future.
