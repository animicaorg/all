# {{ project_name | default("Animica Dapp (React + TypeScript)") }}

A production-ready React + TypeScript starter for building Animica dapps. It includes a clean app shell, a minimal state layer, optional Tailwind CSS, first-class wallet support (Animica browser extension or SDK signer), typed RPC and WS clients, and ready-to-run unit + E2E tests.

> This project is generated from the **`templates/dapp-react-ts`** blueprint. All values like `{{ project_name }}` and `{{ chain_id }}` are filled in by the template engine at render time.

---

## ✨ What’s inside

- **React 18 + TypeScript** with Vite for fast HMR and optimized builds.
- **Animica TypeScript SDK** (`@animica/sdk`) for JSON-RPC, WS subscriptions, ABI calls, and utilities.
- **Wallet integration**
  - `window.animica` provider (MV3 extension) **or**
  - SDK signer stub (for local demos/dev boxes).
- **Example screens & hooks** (optional): current head, account balance, send-tx form.
- **Environment-driven config**: RPC, WS, chainId, services URL (deploy/verify/faucet).
- **Testing**
  - **Vitest** unit tests.
  - **Playwright** E2E (optional) to exercise connect → send → receipt flow.
- **Code quality**: ESLint + Prettier.  
- **Styling**: Tailwind CSS (optional) or bring-your-own styles.
- **Deploy**: Static assets can be served by any CDN or static host.

---

## 🧰 Requirements

- **Node.js** 18.17+ (recommended LTS) or 20.x
- Package manager: **pnpm** (recommended) or npm/yarn
- (Optional) **Animica Wallet Extension** (MV3) in your browser
- (Optional) Access to a running **Animica node** (devnet/mainnet) via RPC/WS

---

## 🚀 Quickstart

```bash
# 1) Install dependencies
pnpm install        # or: npm install / yarn

# 2) Configure environment (see .env section below)
cp .env.example .env.local && $EDITOR .env.local

# 3) Start the dev server
pnpm dev            # Vite on http://localhost:5173 (by default)

# 4) Build for production
pnpm build          # emits dist/

# 5) Preview local production build
pnpm preview

If you selected Playwright, first run:

pnpm dlx playwright install --with-deps


⸻

⚙️ Configuration

Set the following in .env.local (Vite reads import.meta.env.*):

# HTTP JSON-RPC endpoint for the node
VITE_RPC_URL={{ rpc_url | default("http://localhost:8545") }}

# WebSocket endpoint for subscriptions
VITE_WS_URL={{ ws_url | default("ws://localhost:8546") }}

# Numeric chain id the app expects
VITE_CHAIN_ID={{ chain_id | default(1337) }}

# Optional Studio Services backend (deploy / verify / faucet proxy)
VITE_SERVICES_URL={{ services_url | default("http://localhost:8080") }}

# "extension" to use window.animica provider, "sdk" to use a local signer stub
VITE_WALLET_PROVIDER={{ wallet_provider | default("extension") }}

Tip: In production, deploy the built dist/ to your static host and configure the same variables during build time.

⸻

🧩 Project layout

/src
  /app/                   # App shell, providers, routing
  /components/            # Reusable UI
  /pages/
    Home.tsx              # Example dashboard (head, balance, send tx)
  /services/
    rpc.ts                # Typed RPC client using @animica/sdk
    ws.ts                 # WS subscribe helpers
    wallet.ts             # Abstraction over window.animica or SDK signer
  /hooks/
    useHead.ts            # Poll or subscribe head
    useBalance.ts         # Read balance for selected account
  /utils/                 # CBOR, bytes, formatting helpers
  index.css               # Tailwind base (if enabled) or minimal CSS
  main.tsx                # App entry

If Tailwind was selected, you’ll also have tailwind.config.cjs and postcss.config.cjs.

⸻

🔐 Wallets & Signing

Using the Animica Extension (window.animica)

When VITE_WALLET_PROVIDER=extension, the dapp uses the in-page provider injected by the extension:

import { assertProvider } from './services/wallet';

const provider = await assertProvider(); // prompts connect if needed
const accounts = await provider.request({ method: 'wallet_getAccounts' });
// accounts = [{ address: 'anim1...' }, ...]

The provider implements an AIP-1193-like interface and supports:
	•	wallet_getAccounts, wallet_requestPermissions
	•	animica_sendTransaction (CBOR-signed), animica_signMessage
	•	animica_chainId, animica_subscribe (proxied via extension)

Security: The extension never shares private keys. All signing happens in the service worker with user approval.

Using SDK Signer (local demo)

When VITE_WALLET_PROVIDER=sdk, the app wires a lightweight signer from @animica/sdk for local testing. This mode is not for production—just convenience while developing without a browser extension.

⸻

🧪 Testing

Unit tests (Vitest)

pnpm test
pnpm test:watch

E2E tests (Playwright, optional)

Runs a headless browser against the dev server. If wallet_provider=extension, the E2E suite uses a mocked extension bridge.

pnpm e2e


⸻

📡 RPC & WS Usage

The template ships a thin wrapper over @animica/sdk with sensible defaults.

// src/services/rpc.ts
import { createHttpClient } from '@animica/sdk/rpc/http';
import { hexToBytes } from '@animica/sdk/utils/bytes';

const rpcUrl = import.meta.env.VITE_RPC_URL;
export const rpc = createHttpClient({ url: rpcUrl, timeoutMs: 12_000 });

// Example: get head
export async function getHead() {
  return rpc.request('chain.getHead', []);
}

WebSocket subscriptions:

// src/services/ws.ts
import { createWsClient } from '@animica/sdk/rpc/ws';

const wsUrl = import.meta.env.VITE_WS_URL;

export async function subscribeNewHeads(onHead: (head: any) => void) {
  const ws = await createWsClient(wsUrl);
  const subId = await ws.subscribe('newHeads', onHead);
  return () => ws.unsubscribe(subId);
}


⸻

🧱 Common flows (examples)

1) Read head & balance (hook snippet)

// src/hooks/useHead.ts
import { useEffect, useState } from 'react';
import { getHead } from '../services/rpc';
import { subscribeNewHeads } from '../services/ws';

export function useHead() {
  const [head, setHead] = useState<any | null>(null);

  useEffect(() => {
    let off = () => {};
    (async () => {
      setHead(await getHead());
      off = await subscribeNewHeads(setHead);
    })();
    return () => off();
  }, []);
  return head;
}

// src/hooks/useBalance.ts
import { rpc } from '../services/rpc';

export async function getBalance(address: string) {
  const hex = await rpc.request('state.getBalance', [address]);
  return BigInt(hex); // hex-encoded integer ↦ BigInt
}

2) Build and send a transaction

// src/services/wallet.ts
import { createTxSignBytes, encodeTxCBOR } from '@animica/sdk/tx/encode';
import { buildTransfer } from '@animica/sdk/tx/build';
import { rpc } from './rpc';

const CHAIN_ID = Number(import.meta.env.VITE_CHAIN_ID);

export async function sendTransfer(from: string, to: string, amount: bigint) {
  // 1. Build Tx (transfer kind). In a real app, include nonce, gas, tip.
  const tx = await buildTransfer({
    from,
    to,
    amount,
    chainId: CHAIN_ID,
  });

  // 2. Ask wallet to sign
  const provider = await assertProvider();
  const signBytes = createTxSignBytes(tx);
  const signature = await provider.request({
    method: 'animica_signMessage',
    params: [{ domain: 'tx', bytes: signBytes }],
  });

  // 3. Attach signature, encode CBOR, and send
  const raw = encodeTxCBOR({ ...tx, signature });
  const hash = await rpc.request('tx.sendRawTransaction', [raw]);
  return hash as `0x${string}`;
}

3) Subscribe to pendingTxs

import { createWsClient } from '@animica/sdk/rpc/ws';
const ws = await createWsClient(import.meta.env.VITE_WS_URL);
const unsub = await ws.subscribe('pendingTxs', (tx) => {
  console.log('Pending tx', tx.hash);
});

4) Call a contract via ABI client

import { Contract } from '@animica/sdk/contracts/client';
import counterAbi from '../abi/counter.json';

const counter = new Contract({
  abi: counterAbi,
  address: 'anim1abc...xyz', // deployed address
  rpcUrl: import.meta.env.VITE_RPC_URL,
});

// read method (no sign)
const value = await counter.call('get', []);

// write method (sign)
const txHash = await counter.send('inc', [], { from: 'anim1...', chainId: Number(import.meta.env.VITE_CHAIN_ID) });


⸻

🧯 Troubleshooting
	•	ChainIdMismatch or rejections when sending
Ensure VITE_CHAIN_ID matches the node’s chain id. Check chain.getChainId via curl or the SDK.
	•	CORS / Mixed content
When hosting over https://, your RPC/WS must also be https:///wss://.
	•	WebSocket blocked / corporate proxy
Switch to polling mode for head updates or use a proxy that allows WS.
	•	Extension not detected
Verify the Animica wallet is installed and allowed on the site origin. In dev, use http://localhost:*.
	•	403 or rate-limit from services
The Studio Services proxy may enforce API keys or quotas—configure VITE_SERVICES_URL and headers accordingly.

⸻

🛡️ Security model & best practices
	•	Never embed private keys or mnemonics in the frontend. Use the extension.
	•	Validate chainId in signing domains to prevent replay.
	•	Use content security policies (CSP) where possible.
	•	Treat user input as untrusted. Validate ABI arguments before send.
	•	Consider feature flags for dangerous actions (e.g., bypass simulation) disabled in production builds.

⸻

📦 Scripts

pnpm dev        # Start dev server with HMR
pnpm build      # Production build to dist/
pnpm preview    # Preview dist/ locally
pnpm lint       # ESLint + typecheck (if configured)
pnpm test       # Vitest unit tests
pnpm e2e        # Playwright E2E (if included)

If Tailwind is enabled, the build pipeline automatically processes styles. You can customize tailwind.config.cjs.

⸻

🗺️ Deployment
	1.	Build the project: pnpm build
	2.	Upload dist/ to your static host (Netlify, Vercel, S3+CloudFront, Nginx, …).
	3.	Configure environment at build time (or via static file replacement) to point to the intended network’s RPC/WS/services.
	4.	Verify CORS and TLS policies match your hosting.

⸻

❓ FAQ

Q: Can I use another wallet?
A: Yes, as long as it implements the Animica provider interface or you write a small adapter in services/wallet.ts.

Q: How do I add a new network?
A: Create a .env.[name] with alternate RPC/WS/chainId and start vite with --mode [name].

Q: Where should I place contract ABIs?
A: Create src/abi/*.json and import them into your contract client(s).

⸻

📄 License

This template is provided under the repository’s default license. See LICENSE at the repo root. Some dependencies are licensed separately; consult their respective notices.

⸻

📝 Changelog

Significant template updates should be recorded in the parent repo’s templates/CHANGELOG.md.

⸻

Happy hacking! 🛠️
