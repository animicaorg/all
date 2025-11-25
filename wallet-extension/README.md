# Animica Wallet — Browser Extension (MV3)

A privacy-first, post-quantum (PQ) wallet for Animica networks. Ships an in-page provider (`window.animica`) for dapps, deterministic simulation, and a secure keyring backed by an encrypted vault. Built with TypeScript, React, and Vite as a Manifest V3 extension.

---

## ✨ Features

- **In-page provider (`window.animica`)**  
  AIP-1193-like API (inspired by EIP-1193) for `request({ method, params })`, events (`accountsChanged`, `chainChanged`, `newHeads`), and JSON-RPC passthrough.
- **Post-Quantum keys (default: Dilithium3; optional: SPHINCS+ Shake-128s)**  
  Deterministic derivation from a mnemonic; per-account algorithm selection; domain-separated signing (chain-bound).
- **Bech32m addresses (`anim1…`)**  
  Derive from a short address hash: `alg_id || sha3_256(pubkey)` ⇒ bech32m.
- **Encrypted vault (AES-GCM)**  
  Password/PIN protected, auto-lock with inactivity timer; zero-knowledge of secrets outside the extension.
- **Deterministic simulation**  
  Dry-run transfers/calls via a local static VM call (no side effects) prior to approval; gas/fee hints surfaced in UI.
- **Per-origin permissions**  
  Explicit connect approval per site; granular sign/tx approvals; origin allow/deny lists.
- **Network aware**  
  Preloaded network presets (main/test/dev); chainId binding in sign bytes; easy RPC override via `.env`.
- **Robust networking**  
  Fetch-based JSON-RPC with retries/backoff; WebSocket fan-out for `newHeads`/pending txs.
- **MV3 architecture**  
  Background **service worker** + content script injector + React UIs (Popup/Onboarding/Approval windows).
- **Developer-friendly**  
  Live reload in MV3, TypeScript types, unit + E2E tests, and a tiny demo dapp.

---

## 🧭 Architecture Overview

┌──────────────────────┐        ┌─────────────────────────┐
│  Dapp (web page)     │  RPC   │  Node RPC/WS (JSON-RPC) │
│  window.animica  ◀───┼────────┤  + DA/AICF/Beacon APIs  │
└─────────▲────────────┘        └───────────▲─────────────┘
│ in-page provider                 │ network
│ (content script bridge)         │
┌─────────┴─────────┐    messages    ┌──────┴─────────────────────┐
│ Content Script     ├────────────────► Background Service Worker  │
│ (isolated world)   │                │ (keyring, router, network)│
└─────────▲──────────┘                └──────┬─────────▲──────────┘
│ UI intents                           │      │
│                                      │      │ events
┌─────────┴─────────┐                      ┌─────┴───┐  │
│ Popup (React)     │                      │Approve  │◄─┘
│ Onboarding (React)│                      │Windows  │  (connect/sign/tx)
└───────────────────┘                      └─────────┘

**Key components**
- `src/background/*`: keyring, router, network, migrations, notifications
- `src/content/*`: provider injection and window ↔ content ↔ background bridge
- `src/provider/*`: AIP-1193-like API, errors, event streams
- `src/ui/*`: Popup, Onboarding, Approvals (React)
- `src/workers/*`: crypto & simulation workers (MV3-compatible)

---

## 🔐 Security Model

**Threat model (high level)**
- Secrets (mnemonic, private keys) never leave the extension process.  
- The vault is encrypted at rest with **AES-GCM** using a key derived from the user’s password/PIN (PBKDF2/HKDF-SHA3).  
- Keys are **PQ**: Dilithium3 (default) and SPHINCS+ Shake-128s (optional).  
- All signatures are domain separated and **chainId-bound**.  
- Every dapp action is **explicitly approved** (connect, sign, send tx).  
- Simulation runs deterministically off-chain before presenting the final approval.

**What this does _not_ protect against**
- Compromised browser or malicious extensions with broader privileges.
- Users approving malicious transactions. (We show human-readable summaries and simulation hints to reduce risk.)
- Supply-chain attacks on a browser profile. Always install from trusted builds.

**Storage & locking**
- Encrypted vault lives in extension storage.  
- Auto-lock on timeout or browser restart; background SW wakes only on events/alarms.  
- PIN/password is never persisted; a session key unlocks the vault in memory and is zeroized on lock.

**Permissions**
- Per-origin connection gate; you can revoke in **Settings → Connected Sites**.
- Host allow/deny lists (optional policy in `host_permissions.ts`).

---

## 🧪 Simulation & TX Pipeline

1. **Build** — The background constructs canonical sign bytes (CBOR), applies intrinsic gas rules.
2. **Simulate** — Off-thread call to simulation worker (static VM call) for gas/return/logs preview.
3. **Approve** — UI shows summary, gas, and method arguments; user approves/rejects.
4. **Sign** — PQ signature produced in worker (WASM fast-path where available).
5. **Submit** — JSON-RPC `sendRawTransaction`; watcher polls/WS to display receipt.

---

## 🧰 Prerequisites

- Node.js **≥18** (LTS recommended)  
- pnpm **≥8** (recommended) or npm/yarn  
- A running Animica RPC endpoint (devnet or public testnet)

Copy `.env.example` → `.env` if you want to override:

RPC_URL=https://localhost:8545
CHAIN_ID=31337
CORS_ORIGINS=*

---

## ▶️ Development (MV3 live reload)

```bash
pnpm install
pnpm dev

This runs the MV3 dev server with live reload. Load the unpacked extension:

Chrome / Chromium
	1.	Open chrome://extensions
	2.	Enable Developer mode
	3.	Load unpacked → select the dist/chrome directory

Firefox (MV3 polyfilled)
	1.	Open about:debugging#/runtime/this-firefox
	2.	Load Temporary Add-on → select dist/firefox/manifest.json

The build scripts generate per-browser manifests and copy public assets. During pnpm dev, the service worker auto-reloads on changes.

⸻

📦 Production Builds

# Chromium bundle (zip) & dist/chrome
pnpm build:chrome

# Firefox bundle (zip) & dist/firefox
pnpm build:firefox

Outputs:
	•	dist/chrome/** + dist-manifests/manifest.chrome.json
	•	dist/firefox/** + dist-manifests/manifest.firefox.json

You can side-load the folders above or distribute the generated archives.

⸻

🧩 Using the Provider in a Dapp

// in app code
const provider = (window as any).animica;

await provider.request({ method: 'animica_requestAccounts' });
const [account] = await provider.request({ method: 'animica_accounts' });

// example transfer
const txHash = await provider.request({
  method: 'animica_sendTransaction',
  params: [{
    from: account,
    to: 'anim1qxy...xyz',
    value: '0x16345785d8a0000', // 0.1 ANM (hex wei-like)
    data: '0x',
  }]
});

// subscribe to heads
provider.events.newHeads.on((head) => console.log('new head', head.height));

Supported request methods mirror the RPC & wallet feature set (see src/provider/types.ts).

⸻

🔑 Key Management
	•	Create/Import mnemonic (BIP-39-like; PBKDF/HKDF-SHA3 derivation).
	•	Derive subkeys for Dilithium3 and SPHINCS+ deterministically (per-account).
	•	Export mnemonic (explicit confirmation + re-auth).
	•	Addresses: bech32m anim1… derived from alg_id || sha3_256(pubkey).

Recommendation: prefer Dilithium3 for general signing; use SPHINCS+ where deterministic stateless signatures are required.

⸻

🧷 Privacy
	•	No telemetry.
	•	Network requests go only to configured RPC/WS and CDN for extension assets.
	•	Minimal structured logs in background (disabled in production builds).

⸻

🧪 Tests
	•	Unit tests (Vitest): pnpm test
	•	E2E (Playwright + demo dapp): pnpm e2e

A tiny demo dapp is included under test/e2e/dapp/ and is exercised by the E2E spec.

⸻

🛠️ Troubleshooting
	•	Extension doesn’t load / blank popup: ensure pnpm dev or a fresh pnpm build:* ran after dependency changes.
	•	Service worker not updating: toggle the extension off/on or click “Update” in chrome://extensions.
	•	RPC errors: verify RPC_URL, CORS, and that chainId matches your target network.
	•	WASM PQ libs fail to load: the wallet uses safe fallbacks; ensure crossOriginIsolated is not required by your browser profile or disable conflicting extensions.

⸻

🧾 Manifest & Permissions

The MV3 manifest (generated) requests minimal permissions:
	•	storage (encrypted vault + settings)
	•	alarms (auto-lock & background maintenance)
	•	scripting (content script injection for provider)
	•	activeTab (optional; approval windows)
	•	host permissions for the configured RPC origin (if required)

See manifest.base.json and scripts/build.ts for details.

⸻

🔒 Supply-Chain Notes
	•	Reproducible builds: the build script emits dist-manifests/manifest.*.json with content hashes of bundles.
	•	Pin your package manager lockfile and verify WASM module checksums when integrating custom PQ backends.

⸻

📄 License & Credits

This extension includes third-party fonts/icons as noted in public/ and uses PQ crypto via WASM wrappers where available.

