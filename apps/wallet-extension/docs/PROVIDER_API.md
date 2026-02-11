# Animica Wallet Provider API Specification

**Version:** 1.0  
**Date:** 2026-02-11  
**Status:** Draft

---

## Overview

The Animica browser wallet extension exposes a provider API at `window.animica` that allows web applications (like the Dapp IDE) to:
- Request account access
- Query account information
- Sign transactions with user approval
- Submit transactions to the network
- Sign arbitrary messages (off-chain)

**Security Model:** The wallet controls all secret keys. Web applications can only request operations with explicit user approval.

---

## API Surface

### Provider Object

```typescript
interface AnimicaProvider extends EventEmitter {
  // Properties
  isAnimica: boolean;              // Always true
  version: string;                 // Extension version
  chainId: string | null;          // Current chain ID (hex, e.g., "0x539")
  networkVersion: string | null;   // Chain ID as decimal string
  selectedAddress: string | null;  // Currently selected address (bech32m)
  
  // Methods
  request(args: RequestArguments): Promise<any>;
  
  // Events
  on(event: ProviderEvent, handler: EventHandler): void;
  off(event: ProviderEvent, handler: EventHandler): void;
  once(event: ProviderEvent, handler: EventHandler): void;
  removeAllListeners(event?: ProviderEvent): void;
}

interface RequestArguments {
  method: string;
  params?: any[] | Record<string, any>;
}

type ProviderEvent = 
  | 'connect'
  | 'disconnect'
  | 'accountsChanged'
  | 'chainChanged'
  | 'networkChanged'
  | 'message';
```

---

## Methods

### `animica_requestAccounts`

Request user permission to access accounts.

**Parameters:** `[]`

**Returns:** `string[]` - Array of bech32m addresses

**Behavior:**
- If not previously authorized, shows permission popup
- User can select which accounts to share
- Returns immediately if already authorized
- Throws if user denies permission

**Example:**
```typescript
const accounts = await window.animica.request({
  method: 'animica_requestAccounts'
});
// ["anim1qq9a7x3k4l...", "anim1qq5b8y2m3n..."]
```

**Errors:**
- `4001` - User rejected the request
- `-32603` - Internal error

---

### `animica_accounts`

Get currently accessible accounts (no permission prompt).

**Parameters:** `[]`

**Returns:** `string[]` - Array of bech32m addresses (empty if not authorized)

**Example:**
```typescript
const accounts = await window.animica.request({
  method: 'animica_accounts'
});
```

---

### `animica_chainId`

Get current chain ID.

**Parameters:** `[]`

**Returns:** `string` - Chain ID as hex string (e.g., `"0x539"` for 1337)

**Example:**
```typescript
const chainId = await window.animica.request({
  method: 'animica_chainId'
});
// "0x539"
```

---

### `animica_switchChain`

Request to switch to a different chain.

**Parameters:**
```typescript
[{
  chainId: string;  // Hex chain ID, e.g., "0x539"
}]
```

**Returns:** `null`

**Behavior:**
- If chain is not configured, may prompt user to add it
- Switches active network
- Emits `chainChanged` event

**Example:**
```typescript
await window.animica.request({
  method: 'animica_switchChain',
  params: [{ chainId: '0x539' }]
});
```

**Errors:**
- `4902` - Chain not added to wallet
- `4001` - User rejected the request

---

### `animica_addNetwork`

Add a custom network to the wallet.

**Parameters:**
```typescript
[{
  chainId: string;        // Hex chain ID
  chainName: string;      // Human-readable name
  rpcUrls: string[];      // RPC endpoints
  blockExplorerUrls?: string[];
  nativeCurrency?: {
    name: string;
    symbol: string;
    decimals: number;
  };
}]
```

**Returns:** `null`

**Example:**
```typescript
await window.animica.request({
  method: 'animica_addNetwork',
  params: [{
    chainId: '0x539',
    chainName: 'Animica Devnet',
    rpcUrls: ['http://127.0.0.1:8545/rpc'],
    nativeCurrency: {
      name: 'Animica',
      symbol: 'ANI',
      decimals: 18
    }
  }]
});
```

---

### `animica_signTx`

Sign a transaction with user approval (does NOT submit).

**Parameters:**
```typescript
[{
  from: string;           // Bech32m address
  to: string;             // Bech32m address
  value: string;          // Wei amount (hex or decimal string)
  nonce: number;          // Transaction nonce
  gasLimit?: any;         // Gas limit (can be dict or number)
  data?: string;          // Hex-encoded data (optional)
}]
```

**Returns:**
```typescript
{
  signature: {
    algId: number;        // Algorithm ID (0x1001 for Dilithium3)
    algName: string;      // "dilithium3"
    domain: string;       // "animica.tx.v1"
    prehash: string;      // "sha3-512"
    sig: string;          // Hex-encoded signature
  };
  txHash: string;         // SHA3-512 hash of SignBytes
  signedTx: any;          // Full signed transaction object
}
```

**Behavior:**
- Shows transaction approval UI with all details
- User can approve or reject
- Signs with active account's secret key
- Returns signature and signed transaction

**Example:**
```typescript
const result = await window.animica.request({
  method: 'animica_signTx',
  params: [{
    from: 'anim1qq9a7x3k4l...',
    to: 'anim1qq5b8y2m3n...',
    value: '1000000000000000000',  // 1 ANI
    nonce: 42
  }]
});
```

**Errors:**
- `4001` - User rejected the request
- `-32000` - Invalid transaction
- `-32602` - Invalid params

---

### `animica_sendTx`

Sign AND submit a transaction.

**Parameters:** Same as `animica_signTx`

**Returns:**
```typescript
{
  txHash: string;   // Transaction hash
}
```

**Behavior:**
- Signs transaction (with user approval)
- Submits to current network's RPC
- Returns transaction hash immediately (does not wait for confirmation)

**Example:**
```typescript
const result = await window.animica.request({
  method: 'animica_sendTx',
  params: [{
    from: 'anim1qq9a7x3k4l...',
    to: 'anim1qq5b8y2m3n...',
    value: '1000000000000000000',
    nonce: 42
  }]
});
// { txHash: "0xabcd1234..." }
```

---

### `animica_signBytes`

Sign arbitrary bytes (off-chain signing).

**Parameters:**
```typescript
[{
  address: string;    // Bech32m address to sign with
  message: string;    // Hex-encoded message
  domain?: string;    // Domain string (default: "animica.offchain.v1")
}]
```

**Returns:**
```typescript
{
  signature: {
    algId: number;
    algName: string;
    domain: string;
    prehash: string;
    sig: string;
  };
  signHash: string;   // SHA3-512 hash of SignBytes
}
```

**Behavior:**
- Shows signature request UI
- User can approve or reject
- Does NOT submit to blockchain (off-chain only)

**Example:**
```typescript
const result = await window.animica.request({
  method: 'animica_signBytes',
  params: [{
    address: 'anim1qq9a7x3k4l...',
    message: '0x48656c6c6f20416e696d696361',  // "Hello Animica"
    domain: 'myapp.com/login'
  }]
});
```

---

### `animica_getBalance`

Get account balance (convenience method).

**Parameters:**
```typescript
[address: string]  // Bech32m address
```

**Returns:** `string` - Balance in wei (decimal string)

**Example:**
```typescript
const balance = await window.animica.request({
  method: 'animica_getBalance',
  params: ['anim1qq9a7x3k4l...']
});
// "1000000000000000000"
```

---

### `animica_getNonce`

Get account nonce for next transaction.

**Parameters:**
```typescript
[address: string]  // Bech32m address
```

**Returns:** `number` - Next nonce

**Example:**
```typescript
const nonce = await window.animica.request({
  method: 'animica_getNonce',
  params: ['anim1qq9a7x3k4l...']
});
// 42
```

---

## Events

### `connect`

Emitted when provider connects to a network.

**Payload:**
```typescript
{
  chainId: string;  // Hex chain ID
}
```

**Example:**
```typescript
window.animica.on('connect', (info) => {
  console.log('Connected to chain:', info.chainId);
});
```

---

### `disconnect`

Emitted when provider disconnects.

**Payload:**
```typescript
{
  code: number;     // Error code
  message: string;  // Error message
}
```

---

### `accountsChanged`

Emitted when user changes selected account or grants/revokes access.

**Payload:** `string[]` - New array of accessible addresses

**Example:**
```typescript
window.animica.on('accountsChanged', (accounts) => {
  if (accounts.length === 0) {
    console.log('User disconnected wallet');
  } else {
    console.log('Active account:', accounts[0]);
  }
});
```

---

### `chainChanged`

Emitted when user switches networks.

**Payload:** `string` - New chain ID (hex)

**Example:**
```typescript
window.animica.on('chainChanged', (chainId) => {
  console.log('Switched to chain:', chainId);
  window.location.reload();  // Recommended to reload page
});
```

---

### `networkChanged`

Alias for `chainChanged` (for compatibility).

---

### `message`

Emitted for subscription messages (future use).

---

## Error Codes

| Code | Message | Description |
|------|---------|-------------|
| `4001` | User Rejected Request | User declined the request |
| `4100` | Unauthorized | Not authorized to access accounts |
| `4200` | Unsupported Method | Method not supported |
| `4900` | Disconnected | Provider disconnected from chain |
| `4901` | Chain Disconnected | Provider not connected to requested chain |
| `4902` | Unrecognized Chain ID | Chain not added to wallet |
| `-32700` | Parse Error | Invalid JSON |
| `-32600` | Invalid Request | Invalid method or params |
| `-32601` | Method Not Found | Method does not exist |
| `-32602` | Invalid Params | Invalid method parameters |
| `-32603` | Internal Error | Internal JSON-RPC error |
| `-32000` | Invalid Input | Invalid transaction or signature |
| `-32001` | Resource Not Found | Requested resource not found |
| `-32002` | Resource Unavailable | Resource temporarily unavailable |
| `-32003` | Transaction Rejected | Transaction failed validation |
| `-32004` | Method Not Supported | Method not supported |

---

## Detection and Initialization

### Detecting the Provider

```typescript
function getProvider(): AnimicaProvider | undefined {
  if (typeof window.animica !== 'undefined') {
    return window.animica;
  }
  return undefined;
}

// Or wait for injection
window.addEventListener('animica#initialized', () => {
  const provider = window.animica;
  // Ready to use
});
```

### Check if Connected

```typescript
async function isConnected(): Promise<boolean> {
  try {
    const accounts = await window.animica.request({
      method: 'animica_accounts'
    });
    return accounts.length > 0;
  } catch {
    return false;
  }
}
```

### Connect to Wallet

```typescript
async function connectWallet(): Promise<string[]> {
  try {
    const accounts = await window.animica.request({
      method: 'animica_requestAccounts'
    });
    console.log('Connected:', accounts[0]);
    return accounts;
  } catch (error) {
    if (error.code === 4001) {
      console.log('User rejected connection');
    } else {
      console.error('Failed to connect:', error);
    }
    throw error;
  }
}
```

---

## Best Practices

### 1. Always Check for Provider

```typescript
if (!window.animica) {
  alert('Please install Animica Wallet extension');
  return;
}
```

### 2. Handle Account Changes

```typescript
window.animica.on('accountsChanged', (accounts) => {
  if (accounts.length === 0) {
    // User disconnected
    clearUserSession();
  } else {
    // Update UI with new account
    updateAccount(accounts[0]);
  }
});
```

### 3. Handle Chain Changes

```typescript
window.animica.on('chainChanged', (chainId) => {
  // Reload page or update state
  window.location.reload();
});
```

### 4. Request Permissions Early

```typescript
// Request accounts as soon as user clicks "Connect"
document.getElementById('connect-btn').onclick = async () => {
  await connectWallet();
};
```

### 5. Graceful Error Handling

```typescript
try {
  const result = await window.animica.request({
    method: 'animica_sendTx',
    params: [tx]
  });
} catch (error) {
  if (error.code === 4001) {
    // User rejected - show friendly message
    showToast('Transaction cancelled');
  } else {
    // Other error - show technical details
    showError(error.message);
  }
}
```

### 6. Verify Chain ID

```typescript
const expectedChainId = '0x539';  // Devnet
const currentChainId = await window.animica.request({
  method: 'animica_chainId'
});

if (currentChainId !== expectedChainId) {
  await window.animica.request({
    method: 'animica_switchChain',
    params: [{ chainId: expectedChainId }]
  });
}
```

---

## TypeScript Definitions

```typescript
// Add to your project's types:
declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

export interface AnimicaProvider extends EventEmitter {
  isAnimica: true;
  version: string;
  chainId: string | null;
  networkVersion: string | null;
  selectedAddress: string | null;
  
  request<T = any>(args: RequestArguments): Promise<T>;
}

export interface RequestArguments {
  method: string;
  params?: any[] | Record<string, any>;
}

// Method-specific types
export type AnimicaMethod =
  | 'animica_requestAccounts'
  | 'animica_accounts'
  | 'animica_chainId'
  | 'animica_switchChain'
  | 'animica_addNetwork'
  | 'animica_signTx'
  | 'animica_sendTx'
  | 'animica_signBytes'
  | 'animica_getBalance'
  | 'animica_getNonce';
```

---

## Security Considerations

### User Approval

All sensitive operations require explicit user approval:
- Account access (`animica_requestAccounts`)
- Transaction signing (`animica_signTx`, `animica_sendTx`)
- Message signing (`animica_signBytes`)

### Origin Restrictions

- Provider only injects into https:// and localhost pages
- Each origin has separate permission state
- User can revoke access per-origin in wallet settings

### Transaction Display

Transaction approval UI must show:
- From address (with account label)
- To address (with warning if not in address book)
- Value in both wei and human-readable format
- Gas limit and estimated fee
- Data (decoded if possible, hex otherwise)
- Network name and chain ID
- SignBytes hash (for verification)

### Rate Limiting

- Max 10 signature requests per minute per origin
- User can set global rate limits in wallet settings

---

## Future Extensions

Possible future methods:
- `animica_deployContract` - Contract deployment helper
- `animica_callContract` - Contract call helper
- `animica_addToken` - Add custom token to wallet
- `animica_watchAsset` - Watch custom asset
- `animica_personal_sign` - EIP-191 compatible personal message signing
- `animica_signTypedData` - EIP-712 compatible structured data signing

---

## References

- **Wallet Implementation:** `apps/wallet-extension/`
- **Provider Code:** `apps/wallet-extension/src/provider/`
- **PQ Discovery:** `apps/dapp-ide/docs/PQ_DISCOVERY.md`
- **PQ Policy:** `docs/PQ_POLICY.md`

---

**This specification is subject to change during development.**
