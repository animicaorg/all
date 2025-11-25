# @animica/sdk

TypeScript SDK for the **Animica** stack — typed JSON-RPC, wallet utilities, transaction builders, contract clients, and helpers for **DA**, **AICF**, **Randomness**, and **light-client** verification. Designed for Node ≥ **18** and modern browsers.

- Tree-shakeable ESM build with a CommonJS fallback.
- Zero server-side signing: keys stay in the app or the user’s wallet.
- Works with the Animica **OpenRPC** surface and ABI schemas published in `spec/`.

> This README documents usage; actual APIs live under `src/` per the directory layout in your repo plan.

---

## Install

```bash
npm i @animica/sdk
# or
pnpm add @animica/sdk
# or
yarn add @animica/sdk

Peer requirements
	•	Node 18.17+ or evergreen browsers.
	•	TypeScript 5.3+ (types shipped in the package).

⸻

Quickstart (Node)

import { RpcHttp } from '@animica/sdk/rpc/http'
import { TxBuilder } from '@animica/sdk/tx/build'
import { TxSender } from '@animica/sdk/tx/send'
import { Wallet } from '@animica/sdk/wallet'
import { Address } from '@animica/sdk/address'

const rpc = new RpcHttp({ url: process.env.RPC_URL!, timeoutMs: 10_000 })

// Create a PQ signer from a mnemonic or seed (Dilithium3 by default)
const signer = await Wallet.fromMnemonic('abandon ...', { alg: 'dilithium3' })

const sender = Address.fromPublicKey(await signer.publicKey(), { alg: signer.alg }).toBech32()
const to = Address.random().toBech32()

const tx = TxBuilder.transfer({
  chainId: 1,
  sender,
  to,
  amount: 1_000_000n,
  gasPrice: 1n,
  gasLimit: 50_000n,
  nonce: 0n
})

// Domain-separated signing (tx domain)
const signed = await signer.sign(tx.signBytes(), { domain: 'tx' })
tx.attachSignature({ algId: signer.alg, signature: signed })

const raw = tx.encode() // CBOR bytes
const txHash = await TxSender.sendRawTransaction(rpc, raw)
const receipt = await TxSender.awaitReceipt(rpc, txHash, { timeoutMs: 60_000 })
console.log('status=', receipt.status)


⸻

Quickstart (Browser)

Use a wallet (e.g. Animica Wallet Extension) injected provider or your own signer running in a Web Worker. The SDK ships ESM and is bundler-friendly.

import { Contracts } from '@animica/sdk/contracts'
import { RpcHttp } from '@animica/sdk/rpc/http'

const rpc = new RpcHttp({ url: import.meta.env.VITE_RPC_URL })

// Create a contract client from an ABI
const counterAbi = /* fetch or import JSON */;
const counter = new Contracts.Client({
  rpc,
  address: 'anim1...',
  abi: counterAbi,
  chainId: 1
})

// Read (view)
const n = await counter.read('get')

// Write (nonpayable) — you must provide a signer externally
const tx = await counter.buildTx('inc', { gasLimit: 70_000n })
/* sign/encode/send with your signer + TxSender */


⸻

Modules & Capabilities

JSON-RPC
	•	@animica/sdk/rpc/http — HTTP client with retries and structured errors.
	•	@animica/sdk/rpc/ws — WebSocket subscriptions (newHeads, pendingTxs, contracts events).

import { RpcHttp } from '@animica/sdk/rpc/http'
import { RpcWs } from '@animica/sdk/rpc/ws'

const http = new RpcHttp({ url: 'http://localhost:8545' })
const head = await http.call('chain.getHead', [])
const ws = new RpcWs({ url: 'ws://localhost:8546/ws' })
const sub = await ws.subscribe('newHeads', (h) => console.log('head:', h))

Wallet & PQ signing
	•	@animica/sdk/wallet/mnemonic — BIP-39-like mnemonic → seed (SHA3-based PBKDF/HKDF).
	•	@animica/sdk/wallet/keystore — AES-GCM file/Storage keystore.
	•	@animica/sdk/wallet/signer — PQ signers (Dilithium3, SPHINCS+), domain separation.

Note: PQ implementations prefer WASM or native bindings when available; the SDK falls back to safe stubs for dev. Production builds should enable the WASM feature flags and host the binaries.

Addresses
	•	@animica/sdk/address — anim1… bech32m addresses: alg_id || sha3_256(pubkey).

import { Address } from '@animica/sdk/address'
const addr = Address.fromPublicKey(pubkeyBytes, { alg: 'dilithium3' }).toBech32()

Transactions
	•	@animica/sdk/tx/build — transfer / deploy / call builders with helpers for gas.
	•	@animica/sdk/tx/encode — sign-bytes & CBOR encoding (per spec/tx_format.cddl).
	•	@animica/sdk/tx/send — sendRawTransaction, awaitReceipt, polling/WS.

Contracts
	•	@animica/sdk/contracts/client — generic ABI client (view & write flows).
	•	@animica/sdk/contracts/deployer — on-chain package deploy (manifest+code).
	•	@animica/sdk/contracts/events — event filter/decoder (topic0 = sha3_256(signature)).
	•	@animica/sdk/contracts/codegen — ABI → typed client stubs.

import { Contracts } from '@animica/sdk/contracts'
const token = new Contracts.Client({ rpc, address: 'anim1...', abi, chainId: 1 })
const bal = await token.read('balanceOf', Address.parse('anim1...'))
const tx = await token.buildTx('transfer', Address.parse('anim1...'), 1000n)

Data Availability (DA)
	•	@animica/sdk/da/client — postBlob, getBlob, getProof.

import { DA } from '@animica/sdk/da/client'
const da = new DA(rpc)
const receipt = await da.postBlob({ namespace: 24, data: new Uint8Array([1,2,3]) })
const bytes = await da.getBlob(receipt.commitment)
const proof = await da.getProof(receipt.commitment)

AICF (AI / Quantum)
	•	@animica/sdk/aicf/client — enqueue AI/Quantum jobs; read results.

import { AICF } from '@animica/sdk/aicf/client'
const aicf = new AICF(rpc)
const job = await aicf.enqueueAI({ model: 'tiny', prompt: 'hello' })
const result = await aicf.getResult(job.taskId)

Randomness (Beacon)
	•	@animica/sdk/randomness/client — getParams, getRound, commit, reveal, getBeacon, getHistory.

Light Client
	•	@animica/sdk/light_client/verify — verify a header + DA light-proof bundle for UI/light nodes.

⸻

Error Handling

All RPC helpers throw typed errors:

import { RpcError, TxError } from '@animica/sdk/errors'

try {
  await sender.sendRawTransaction(...)
} catch (e) {
  if (e instanceof TxError) console.error(e.code, e.data)
  else if (e instanceof RpcError) console.error(e.method, e.message)
  else throw e
}


⸻

CommonJS Usage

const { RpcHttp } = require('@animica/sdk/rpc/http')
const { Contracts } = require('@animica/sdk/contracts')


⸻

Security Notes
	•	No server-side signing. Keep private keys in local keystores or browser wallets.
	•	Enable PQ WASM backends in production builds; test fallbacks are for development only.
	•	Enforce CORS/Origin policies in your RPC and studio-services deployments.

⸻

Development

# in sdk/typescript/
npm run build
npm run test
npm run lint

Artifacts land in dist/ and the package exposes:
	•	module: ESM dist/index.js
	•	main: CJS dist/index.cjs
	•	types: dist/index.d.ts

⸻

Version

import { version } from '@animica/sdk/version'
console.log(version)


⸻

License

Apache-2.0 © Animica
