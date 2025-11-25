# Wallet Extension — Provider API (`window.animica`)

This document defines the **Animica Provider** interface that dapps use in the
browser. It follows the spirit of EIP-1193 but is tailored to Animica’s stack:
**bech32m addresses**, **PQ signatures (Dilithium3 / SPHINCS+)**, **CBOR
transactions**, and a **minimal JSON-RPC** to the node.

> Short name: **AIP-1193** (Animica Interface Proposal)

---

## TL;DR (Quick Start)

```ts
// 1) Detect
if (!('animica' in window)) throw new Error('Animica provider not found');
const provider = (window as any).animica as AnimicaProvider;

// 2) Connect (prompts the user)
const [address] = await provider.request({ method: 'animica_requestAccounts' });

// 3) Chain info
const chainId: number = await provider.request({ method: 'animica_chainId' });

// 4) Sign a human-readable message (domain-separated)
const sig = await provider.request({
  method: 'animica_signMessage',
  params: [{ address, message: 'Hello Animica', encoding: 'utf8' }],
});

// 5) Build & send a transfer (wallet encodes → CBOR, signs PQ, submits)
const txHash = await provider.request({
  method: 'animica_sendTransaction',
  params: [{
    to: 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv3c9xv',
    amount: '1000000',            // integer string (lowest unit)
    gasPrice: '1200',
    gasLimit: '120000',
    memo: 'hi',
  }],
});

// 6) Subscribe to heads (proxied via the extension’s WS client)
provider.on('newHeads', (head) => {
  console.log('height', head.height, 'hash', head.hash);
});


⸻

Detection & Shape

interface AnimicaProvider {
  // Marker flags
  readonly isAnimica: true;
  readonly version: string;          // wallet extension version (semver)
  readonly platform?: 'chrome' | 'firefox' | 'edge' | 'safari';

  // Session state (may be undefined until connected)
  chainId?: number;                  // CAIP-2 numeric chain id (e.g., 1)
  selectedAddress?: string;          // bech32m (hrp "anim" or test HRP)

  // Core request method
  request<T = unknown>(args: { method: string; params?: any }): Promise<T>;

  // Event API (AIP-1193 style)
  on(event: ProviderEvent, handler: (...args: any[]) => void): this;
  removeListener(event: ProviderEvent, handler: (...args: any[]) => void): this;
}

type ProviderEvent =
  | 'connect'          // { chainId: number }
  | 'disconnect'       // { code: number; message: string }
  | 'accountsChanged'  // string[] (bech32m)
  | 'chainChanged'     // number (chainId)
  | 'newHeads';        // { height: number; hash: string; time: number }

	•	Window Injection: The extension injects window.animica in an isolated
world and bridges messages to the background service worker.
	•	Identity: isAnimica === true. Avoid duck-typing on request alone.

⸻

Permissions & Connect Flow
	•	Dapps must request access via:

const accounts = await provider.request({ method: 'animica_requestAccounts' });

The wallet presents an approval UI listing the requesting origin, the
selected account(s), and the target network.

	•	On first approval, the provider emits:

provider.on('connect', ({ chainId }) => { ... });
provider.on('accountsChanged', (addrs) => { ... });


	•	Revoking permissions (from wallet UI) may emit accountsChanged: [] and
disconnect.

Principle: No server-side signing. Keys never leave the extension.

⸻

Addressing & Algorithms
	•	Addresses: bech32m with HRP anim (main/test/dev may vary), derived as:
payload = alg_id || sha3_256(pubkey); address = bech32m(hrp, payload).
	•	Algorithms: Determined by the account (e.g., Dilithium3 default;
SPHINCS+ SHAKE-128s optional). Algorithm choice appears in signature
metadata returned by signing methods.

⸻

Methods

1) Session & Accounts
	•	animica_requestAccounts() -> string[]
	•	Prompts the user to connect; resolves to a list of bech32m addresses
(usually a single selected account).
	•	animica_accounts() -> string[]
	•	Returns the currently authorized addresses for this origin (no prompt).
	•	animica_chainId() -> number
	•	Returns the numeric chain ID (e.g., 1 for Animica mainnet).

2) Signing (Domain-Separated)

All signing is domain-separated and non-ambiguous:
	•	animica_signMessage(params: { address: string; message: string | Uint8Array; encoding?: 'utf8' | 'hex' | 'base64' }) -> { algId: string; signature: string; publicKey?: string }
	•	For human-readable “login” / “proof of address” prompts.
	•	The wallet shows a clear-text review screen with the origin and hash preview.
	•	animica_signBytes(params: { address: string; bytes: string /* 0x… or base64 */; label?: string }) -> { algId: string; signature: string }
	•	Expert API for protocol builders. Avoid for casual dapps.

Note: Transaction signing happens implicitly via animica_sendTransaction
(the wallet constructs SignBytes per spec, encodes CBOR, and signs).

3) Transactions
	•	animica_sendTransaction(tx: SendTx) -> string /* txHash */

type SendTx = {
  // required
  to?: string;                 // bech32m or omitted for deploy
  amount?: string;             // integer string (lowest unit); default "0"
  gasPrice?: string;           // integer string
  gasLimit?: string;           // integer string

  // optional
  data?: string;               // hex/base64 for contract call/deploy payload
  accessList?: any[];          // reserved; used by scheduler/optimistic mode
  memo?: string;               // short note, displayed in wallet
  chainId?: number;            // overrides current (UI warns if mismatch)
  nonce?: string | number;     // if omitted, wallet fetches from RPC
};

	•	The wallet performs:
	1.	stateless checks (sizes, HRP, chain match)
	2.	build canonical CBOR transaction
	3.	PQ sign (Dilithium3/SPHINCS+), including domain separation
	4.	submit via node RPC (tx.sendRawTransaction)
	5.	return the tx hash (hex)
	•	animica_simulateTransaction(tx: Partial<SendTx> & { from?: string }) -> SimResult
	•	Dry-run via RPC (no state write). Useful for gas estimates & previews.

4) Node RPC Bridge (Safe Subset)
	•	animica_rpc(method: string, params?: unknown[]) -> unknown

Allowed methods (forwarded with rate limits):
	•	chain.getHead / chain.getBlockByNumber|Hash
	•	state.getBalance / state.getNonce
	•	tx.getTransactionByHash / tx.getTransactionReceipt
	•	DA/Randomness/AICF read-only calls (when enabled)

Use the official SDKs for rich types; animica_rpc is a low-level escape hatch.

5) Subscriptions
	•	newHeads: Emitted when the head advances. Payload:

type NewHead = { height: number; hash: string; time: number; parentHash: string };
provider.on('newHeads', (head: NewHead) => { /* … */ });

	•	Additional streams may be added (e.g., pendingTxs) with conservative defaults.

⸻

Error Model

Errors follow EIP-1193 style with Animica codes:

Code	Name	Meaning
4001	UserRejectedRequest	User rejected in an approval/sign modal
4100	Unauthorized	Origin not permitted (call requires permission)
4200	UnsupportedMethod	Method not implemented by the provider
4900	Disconnected	Provider not connected (no session/transport)
4901	ChainDisconnected	Target chain unavailable
5000	InternalError	Unexpected provider failure

Shape:

class AnimicaProviderError extends Error {
  code: number;
  data?: unknown;
}


⸻

Security & Privacy
	•	Consent-first: sites must request accounts; addresses are not exposed until
approved. Each origin has explicit, revokable permissions.
	•	No raw keys: private keys never touch page JS; all signing occurs in the
background service worker and secure contexts.
	•	Chain binding: chainId is always checked in SignBytes to prevent replay
across networks.
	•	PQ signatures: the signing UI shows the algorithm in use (e.g., Dilithium3),
and the signature blob encodes algId.
	•	Phishing & spoofing: the approval window shows the requesting origin and a
recognizable address format (bech32m). The extension enforces a host allowlist
if configured.
	•	Rate limits: RPC bridging is throttled per origin. Abuse is blocked.

⸻

Best Practices for Dapps
	•	Initialize early; render late. Detect provider and await connect before
reading selectedAddress/chainId.
	•	Handle events. Update UI on accountsChanged and chainChanged. Don’t
assume a single account forever.
	•	Never ask for seeds or private keys. You will never need them.
	•	Bound user inputs. Validate amounts, data sizes; let the wallet estimate gas.
	•	Prefer SDKs over ad-hoc RPC. They handle CBOR, ABIs, and receipts.

⸻

Example: Minimal Connector

export async function connectAnimica() {
  const provider = (window as any).animica as AnimicaProvider;
  if (!provider?.isAnimica) throw new Error('Animica provider missing');

  const [address] = await provider.request({ method: 'animica_requestAccounts' });
  const chainId = await provider.request({ method: 'animica_chainId' });

  provider.on('accountsChanged', (a: string[]) => console.log('accounts', a));
  provider.on('chainChanged',   (id: number)   => console.log('chainId', id));
  provider.on('newHeads',       (h: any)       => console.log('head', h));

  return { provider, address, chainId };
}


⸻

Compatibility Notes
	•	The provider is AIP-1193-like. EVM/EIP-1193 shims can be added at app
level, but Animica transactions are CBOR and addresses are bech32m.
	•	window.ethereum is not provided. Use window.animica.

⸻

FAQ

Q: Can I switch chains programmatically?
A: Use animica_chainId to read. Chain switching is a user action from
the wallet UI for safety.

Q: How do I deploy a contract?
A: Provide a data payload (manifest+code) in animica_sendTransaction. The
wallet shows a deploy review screen. Prefer the SDK for building manifests.

Q: Is message signing recoverable to an address?
A: Verification requires the PQ algorithm context. The result includes
algId; use the SDK or pq/py/verify.py helpers to verify.

⸻

Reference
	•	Wallet extension code: wallet-extension/src/provider/*
	•	Node RPC surface: spec/openrpc.json
	•	Addressing: pq/py/address.py (bech32m), docs/spec/ADDRESSES.md
	•	Transaction encoding: core/encoding/*, docs/spec/TX_FORMAT.md

