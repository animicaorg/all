# @animica/sdk — TypeScript Starter Template

A batteries-included starter for building TypeScript apps and scripts on the **Animica** network using the **@animica/sdk**. It’s designed to get you from “empty folder” to “making real JSON-RPC calls, sending signed transactions, subscribing to new heads, and interacting with contracts” in minutes.

---

## What you get

- **Clean project scaffolding** (Node + TypeScript) with sensible defaults.
- **Typed RPC client** (HTTP + WebSocket) wired to your node endpoints.
- **Ready-to-run examples**:
  - `getHead.ts` — fetch the chain head and params.
  - `sendTransfer.ts` — build & send a simple transfer (when a signer is configured).
  - `subscribeNewHeads.ts` — live stream of `newHeads` via WS.
  - `contractCall.ts` — call a contract function from an ABI.
- **.env-driven config** for RPC URLs and Chain ID.
- **Dev UX**: fast `ts-node` style runner, build step, lint/format scripts.
- **Extensible layout** for adding SDK submodules (DA, AICF, Randomness, Light Client).

> This template is intentionally minimal—add web frameworks, CLIs, schedulers, or configuration managers as your app grows.

---

## Prerequisites

- **Node.js ≥ 18** (LTS recommended).
- **A package manager**: `pnpm` (default), `npm`, or `yarn`.
- **An Animica node** to talk to:
  - Local devnet (via this repo’s `tests/devnet` or `ops/docker`).
  - Or a remote RPC/WS endpoint.
- **Optional (for signing)**: PQ WASM artifacts available at runtime (the TS SDK can load PQ signers behind feature flags; see “Signing and PQ notes” below).

---

## Generating a project

You can render this template in two ways.

### 1) Using the template engine (recommended in monorepo)

From the repo root:

```bash
python -m templates.engine.cli render \
  --template templates/sdk-ts-starter \
  --out ./my-ts-app \
  --vars project_name="My TS App" \
         project_slug="my-ts-app" \
         description="Animica SDK TypeScript starter" \
         package_manager="pnpm" \
         license="MIT" \
         sdk_ts_version="^0.1.0" \
         rpc_url="http://localhost:8545" \
         ws_url="ws://localhost:8546" \
         chain_id="1337" \
         init_git=true \
         include_examples=true \
         use_ws_examples=true

This uses the variables defined in templates/sdk-ts-starter/variables.json. The engine will slugify names and validate inputs.

2) Manual copy (quick prototype)

Create a new directory and copy files from this folder, then adjust the package.json and paths. You’ll still need to create a .env file (see below).

⸻

First run

Inside your generated project (e.g., my-ts-app):

# install deps
pnpm install
# or: npm install / yarn install

# copy the env template and edit values
cp .env.example .env

.env fields:

# HTTP JSON-RPC
RPC_URL=http://localhost:8545

# WebSockets JSON-RPC (for subscriptions)
WS_URL=ws://localhost:8546

# CAIP-2 chain id number (e.g., 1 mainnet, 2 testnet, 1337 devnet)
CHAIN_ID=1337

Now try a read-only call:

pnpm ts-node src/examples/getHead.ts
# or: pnpm dev src/examples/getHead.ts

If configured correctly, you’ll see a printed head height/hash and the active chain params.

⸻

Project layout (generated)

<project_slug>/
  ├─ src/
  │  ├─ config.ts             # loads RPC_URL, WS_URL, CHAIN_ID from env
  │  ├─ sdk.ts                # thin wrappers around @animica/sdk HTTP/WS clients
  │  └─ examples/
  │     ├─ getHead.ts         # read-only: chain.getHead + getParams
  │     ├─ sendTransfer.ts    # build & submit a transfer (needs signer)
  │     ├─ subscribeNewHeads.ts
  │     └─ contractCall.ts    # call a contract function via ABI
  ├─ .env.example             # template for environment variables
  ├─ .env                     # your local config (not committed)
  ├─ tsconfig.json            # TS settings
  ├─ package.json             # scripts & deps
  ├─ README.md                # this doc adapted to your project
  └─ (eslint/prettier configs as applicable)


⸻

Scripts

Typical package.json scripts you’ll see (package manager specific commands vary):

{
  "scripts": {
    "dev": "tsx",                           // fast runner for TS files
    "build": "tsc -p tsconfig.json",        // compile to ./dist
    "start": "node dist/index.js",          // adjust if you add an entrypoint
    "lint": "eslint . --ext .ts",
    "format": "prettier --write .",
    "typecheck": "tsc --noEmit",
    "example:head": "tsx src/examples/getHead.ts",
    "example:sub": "tsx src/examples/subscribeNewHeads.ts",
    "example:send": "tsx src/examples/sendTransfer.ts",
    "example:call": "tsx src/examples/contractCall.ts"
  }
}

If you select npm/yarn during template rendering, the commands will be adapted.

⸻

Using the SDK

1) Read chain params and head

// src/examples/getHead.ts
import { HttpClient } from "@animica/sdk/rpc/http";
import { loadConfig } from "../config";

async function main() {
  const { rpcUrl } = loadConfig();
  const http = new HttpClient({ url: rpcUrl });

  const params = await http.request("chain.getParams", []);
  const head = await http.request("chain.getHead", []);

  console.log("Chain Params:", params);
  console.log("Head:", head);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

2) Subscribe to new heads (WebSocket)

// src/examples/subscribeNewHeads.ts
import { WsClient } from "@animica/sdk/rpc/ws";
import { loadConfig } from "../config";

async function main() {
  const { wsUrl } = loadConfig();
  const ws = new WsClient({ url: wsUrl });

  await ws.connect();

  const sub = await ws.subscribe("newHeads", [], (evt) => {
    console.log("newHead:", evt);
  });

  console.log("Subscribed. Press Ctrl+C to exit.");
  process.on("SIGINT", async () => {
    await ws.unsubscribe(sub);
    process.exit(0);
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

3) Send a simple transfer

Requires a signer. If you’re using the TS SDK’s PQ signers, ensure the WASM artifacts are loadable in your environment. In Node, dynamic import with ESM and --experimental-wasm* is typically fine; in browsers, serve the artifacts and allow fetch. Alternatively, plug in a remote signer (e.g., wallet-extension) via a bridge.

// src/examples/sendTransfer.ts
import { HttpClient } from "@animica/sdk/rpc/http";
import { buildTransferTx, encodeSignBytes } from "@animica/sdk/tx/build";
import { sendTransaction } from "@animica/sdk/tx/send";
import { DilithiumSigner } from "@animica/sdk/wallet/signer"; // feature-gated WASM
import { loadConfig } from "../config";

// Example only: never hardcode mnemonics in real code.
const MNEMONIC = process.env.MNEMONIC || "abandon ... (dev only)";

async function main() {
  const { rpcUrl, chainId } = loadConfig();
  const http = new HttpClient({ url: rpcUrl });

  // Resolve nonce/balance off-chain as needed…
  const from = "anim1..."; // derived from your signer public key
  const to = "anim1recipient...";
  const amount = "1000";    // smallest unit
  const gasPrice = "1";
  const gasLimit = 200000;

  // 1) Build an unsigned tx
  const unsignedTx = buildTransferTx({
    chainId: Number(chainId),
    from,
    to,
    amount,
    gasPrice,
    gasLimit
  });

  // 2) Produce sign-bytes (domain-separated)
  const signBytes = encodeSignBytes(unsignedTx);

  // 3) Sign (PQ signer)
  const signer = await DilithiumSigner.fromMnemonic(MNEMONIC);
  const signature = await signer.sign(signBytes);

  // 4) Attach signature and send
  const txHash = await sendTransaction(http, { ...unsignedTx, signature });

  console.log("Submitted tx:", txHash);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

4) Call a contract function (ABI-based)

// src/examples/contractCall.ts
import { HttpClient } from "@animica/sdk/rpc/http";
import { ContractClient } from "@animica/sdk/contracts/client";
import counterAbi from "./abis/counter.json"; // include your ABI JSON
import { loadConfig } from "../config";

async function main() {
  const { rpcUrl } = loadConfig();
  const http = new HttpClient({ url: rpcUrl });

  const address = "anim1contract...";
  const counter = new ContractClient(http, { address, abi: counterAbi });

  const value = await counter.call("get", []); // read-only call
  console.log("Counter value:", value);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});


⸻

Configuration

This starter reads from .env via a tiny helper:

// src/config.ts (simplified)
import * as dotenv from "dotenv";
dotenv.config();

export function loadConfig() {
  const rpcUrl = process.env.RPC_URL || "http://localhost:8545";
  const wsUrl = process.env.WS_URL || "ws://localhost:8546";
  const chainId = process.env.CHAIN_ID || "1337";
  return { rpcUrl, wsUrl, chainId };
}

Keep your .env out of version control. Use .env.example to document required settings.

⸻

Signing and PQ notes
	•	The TS SDK’s PQ signers (e.g., Dilithium3, SPHINCS+) are feature-gated and rely on WASM.
	•	In Node:
	•	Use ESM ("type": "module") or enable dynamic import in your runner (e.g., tsx handles this well).
	•	Ensure the WASM files are discoverable (via relative imports or copying to dist/ on build).
	•	In Browser:
	•	Serve WASM files and allow the SDK’s loader to fetch them.
	•	For real dapps, prefer wallet-extension to hold keys client-side and sign via the provider.
	•	For CI or headless environments, you may mock signing or use a test-only local signer with deterministic RNG (never in production).

⸻

Extending beyond basics
	•	DA client: @animica/sdk/da/client for blob post/get/proof.
	•	AICF: @animica/sdk/aicf/client to enqueue jobs and read results.
	•	Randomness: @animica/sdk/randomness/client for commit/reveal/beacon.
	•	Light client: @animica/sdk/light_client/verify to validate headers + DA light-proofs.

Each module follows the same pattern as RPC—construct the client with your HTTP URL and call typed methods.

⸻

Common errors & troubleshooting
	•	ECONNREFUSED / fetch failed
The node isn’t up or the URL is wrong. Check RPC_URL and WS_URL, and confirm ports.
	•	CORS or WS 403 (browser)
Your node/ingress may block origins. Update CORS allowlists in RPC config.
	•	ChainIdMismatch on submit
Your CHAIN_ID in .env doesn’t match the node’s chain id. Call chain.getChainId to verify.
	•	InvalidTx / signature errors
Ensure domain-separated SignBytes are used and the correct PQ signer & alg_id are configured.
	•	WASM load errors (PQ signing)
Make sure the WASM artifacts are present and importable; check bundler configs and MIME types.

⸻

Recommended dev flow
	1.	Start/attach to a devnet (local docker compose or k8s overlay in this repo).
	2.	Render this template and configure .env.
	3.	Run examples (example:head, then example:sub).
	4.	Wire a signer (local dev key / wallet-extension) and try example:send.
	5.	Add contract ABIs and call into them (example:call).
	6.	Evolve into a service (cron/scheduler, API server, or dapp UI) as needed.

⸻

License

This starter is published under the selected license during template rendering (default MIT). See your generated LICENSE or package.json’s license field.

⸻

Resources
	•	SDK docs live under sdk/typescript/ in this monorepo.
	•	Node RPC surface: spec/openrpc.json.
	•	Contract ABI schema: spec/abi.schema.json.
	•	Example contracts and end-to-end flows: contracts/, tests/integration/.

Happy building! ⚡
