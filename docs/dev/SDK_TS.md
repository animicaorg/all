# TypeScript SDK (@animica/sdk) — Usage Patterns

This guide shows pragmatic patterns for building web and Node apps with the Animica **TypeScript SDK**. You’ll connect to RPC/WS, sign & send transactions, deploy/call contracts, read events, and use extra services (DA, AICF, Randomness, Light Client). Examples are minimal and production-ready.

> See also:
> - SDK layout: `sdk/typescript/src/*`
> - Python & Rust SDKs: `sdk/python`, `sdk/rust`
> - OpenRPC & ABI schemas: `spec/openrpc.json`, `spec/abi.schema.json`

---

## 1) Install

```bash
# app project
npm i @animica/sdk
# or
pnpm add @animica/sdk

Optional polyfills (Node < 20): undici (fetch), ws (WebSocket).

⸻

2) Imports & environment targets

The SDK ships as ESM with tree-shakable modules. Import only what you use.

// RPC + types
import { HttpClient } from '@animica/sdk/rpc/http'
import { WsClient }   from '@animica/sdk/rpc/ws'
import type { Head }  from '@animica/sdk/types/core'

// Wallet & signing (PQ via WASM feature gate)
import { Keystore } from '@animica/sdk/wallet/keystore'
import { Signer }   from '@animica/sdk/wallet/signer'

// Tx helpers
import { buildTransfer } from '@animica/sdk/tx/build'
import { sendTx }        from '@animica/sdk/tx/send'

// Contracts
import { ContractClient } from '@animica/sdk/contracts/client'

// Utilities
import { bech32m } from '@animica/sdk/utils/bech32'
import { sha3_256 } from '@animica/sdk/utils/hash'

// Extras
import { DAClient }           from '@animica/sdk/da/client'
import { AICFClient }         from '@animica/sdk/aicf/client'
import { RandomnessClient }   from '@animica/sdk/randomness/client'
import { verifyLightClient }  from '@animica/sdk/light_client/verify'

Targets
	•	Browser: uses fetch, WebCrypto; PQ signers load a small WASM on demand.
	•	Node 20+: globalThis.fetch is native. For WS subscribe, the SDK uses WebSocket if available; otherwise pass a WS factory (see §6).

⸻

3) Configure RPC clients

const RPC_URL = import.meta.env.PUBLIC_RPC_URL ?? 'https://rpc.animica.dev'
const CHAIN_ID = Number(import.meta.env.PUBLIC_CHAIN_ID ?? 1)

const rpc = new HttpClient({ url: RPC_URL, timeoutMs: 10_000 })
const ws  = new WsClient({ url: RPC_URL.replace(/^http/, 'ws') + '/ws' })

	•	timeoutMs: request deadline (AbortController under the hood)
	•	Retries: built-in jitter/backoff for transient network failures

⸻

4) Wallets & signers (mnemonic, keystore; optional extension)

a) In-app keystore (AES-GCM) + mnemonic

import { Keystore } from '@animica/sdk/wallet/keystore'
import { Signer }   from '@animica/sdk/wallet/signer'
import { mnemonicToSeed } from '@animica/sdk/wallet/mnemonic'

const mnemonic = '... 24 words ...'
const seed = await mnemonicToSeed(mnemonic)  // PBKDF + HKDF-SHA3
const ks = await Keystore.create({ password: 'strong-passphrase' })
await ks.importSeed(seed)

const signer = await Signer.fromKeystore(ks, { alg: 'dilithium3' }) // or 'sphincs_shake_128s'
const address = await signer.getAddress({ hrp: 'anim' })             // bech32m anim1...
console.log('Address', address)

PQ signers load WASM only on first use; cache persists across sessions.

b) Using the browser extension (if present)

If the user installed Animica Wallet (MV3), an AIP-1193-like provider is injected at window.animica. You may:
	1.	request permission to connect,
	2.	read the selected account & chain, and
	3.	delegate signing to the wallet (see wallet extension docs for methods).

const provider = (globalThis as any).animica
if (provider) {
  const accounts: string[] = await provider.request({ method: 'requestAccounts' })
  const address = accounts[0] // bech32m anim1...
  // For sending transactions via the wallet:
  // await provider.request({ method: 'tx_send', params: { cbor: '0x...' } })
}


⸻

5) Build & send a simple transfer

import { buildTransfer } from '@animica/sdk/tx/build'
import { sendTx }        from '@animica/sdk/tx/send'

// 1) Construct an unsigned transfer
const tx = await buildTransfer({
  chainId: CHAIN_ID,
  from: await signer.getAddress({ hrp: 'anim' }),
  to:   'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv3c9xv', // recipient
  amount: '123450000',       // integer string (minimal units)
  nonce:  await rpc.call<number>('state.getNonce', [await signer.getAddress({ hrp: 'anim' })]),
  gasPrice: '1000',
  gasLimit: '500000',
})

// 2) Sign (domain-separated, PQ)
const signed = await signer.signTx(tx)

// 3) Submit & await receipt (poll or WS)
const { txHash, receipt } = await sendTx(rpc, signed, { awaitReceiptMs: 30_000 })
console.log('txHash', txHash, 'status', receipt?.status)

Error handling (see §11): sendTx throws rich TxError for mempool/exec failures.

⸻

6) Subscribe to new heads (WebSocket)

await ws.connect()
const sub = await ws.subscribe<Head>('newHeads', (head) => {
  console.log('height:', head.height, 'hash:', head.hash)
})

// later:
await sub.unsubscribe()
await ws.close()

Node WS: If your runtime lacks WebSocket, pass a factory:

import WebSocket from 'ws'
const ws = new WsClient({
  url: RPC_URL.replace(/^http/,'ws') + '/ws',
  wsFactory: (url) => new WebSocket(url)
})


⸻

7) Contracts: call, deploy, events

a) Direct ABI client

import counterAbi from './counter_abi.json' assert { type: 'json' }
const counter = new ContractClient({ rpc, abi: counterAbi, address: 'anim1xyz...' })

// Read (free)
const current = await counter.call('get', [])

// Write (paid): build → sign → send
const callTx = await counter.buildTx('inc', [1], {
  from: await signer.getAddress({ hrp: 'anim' }),
  gasPrice: '1000',
  gasLimit: '100000',
})
const signed = await signer.signTx(callTx)
await sendTx(rpc, signed, { awaitReceiptMs: 20_000 })

b) Codegen (strongly typed stubs)

# using the shared codegen CLI
npx animica-codegen --lang ts --abi ./counter_abi.json --out ./src/contracts

import { Counter } from './contracts/Counter' // generated
const counter = new Counter({ rpc, address: 'anim1...' })
await counter.inc(signer, 1)               // typed
const value = await counter.get()          // typed return

c) Events

import { decodeEvents } from '@animica/sdk/contracts/events'
const receipt = await rpc.call('tx.getTransactionReceipt', [txHash])
const decoded = decodeEvents(receipt.logs, counterAbi)
decoded.forEach(e => console.log(e.name, e.args))


⸻

8) Data Availability (DA) client

const da = new DAClient({ baseUrl: RPC_URL }) // mounted by node/studio-services
const put = await da.putBlob({
  namespace: 24,
  data: new Uint8Array([1,2,3])
})
console.log('commitment', put.commitment)

const got = await da.getBlob(put.commitment)
console.log('size', got.data.byteLength)

const proof = await da.getProof(put.commitment)
const ok = await da.verifyProof(proof) // light verify against header root (if supplied)


⸻

9) AI/Quantum via AICF (enqueue → result)

const aicf = new AICFClient({ baseUrl: RPC_URL })

// Enqueue tiny AI job (dev/demo)
const job = await aicf.enqueueAI({
  model: 'tiny-demo',
  prompt: 'hello world',
  feeLimit: '500000',
  from: await signer.getAddress({ hrp: 'anim' }),
})

// Later: read result (usually next block)
const res = await aicf.getResult(job.taskId)
if (res?.status === 'completed') {
  console.log(res.output)
}


⸻

10) Randomness beacon (commit → reveal → read)

const rand = new RandomnessClient({ rpc })

const round = await rand.getRound()
const salt = crypto.getRandomValues(new Uint8Array(32))
const payload = sha3_256(new TextEncoder().encode('my-entropy')).hex()

await rand.commit({ salt, payload }, signer)  // tx
// ... after reveal window opens:
await rand.reveal({ salt, payload }, signer)  // tx

const beacon = await rand.getBeacon()
console.log('beacon', beacon.output)


⸻

11) Light client verification (headers + DA samples)

import { verifyLightClient } from '@animica/sdk/light_client/verify'

const header = await rpc.call('chain.getBlockByNumber', [12345, false])
const samples = await fetch('/samples.json').then(r => r.json()) // example
const ok = await verifyLightClient({ header, samples })
console.log('light verify', ok)


⸻

12) Errors, retries, and cancellation

The SDK throws typed errors (subset shown):
	•	RpcError — network/HTTP/JSON-RPC failures
	•	TxError — mempool admission, exec failures (e.g., FeeTooLow, NonceGap)
	•	AbiError, VerifyError — ABI mismatch, proof/verify failures

Pattern:

try {
  const controller = new AbortController()
  const p = rpc.call('state.getBalance', ['anim1...'], { signal: controller.signal })
  const res = await p
} catch (e: any) {
  if (e.name === 'AbortError') { /* cancelled */ }
  else if (e.code === 'FeeTooLow') { /* bump fee */ }
  else { console.error(e) }
}

	•	All HTTP calls support signal for cancellation.
	•	Retries: idempotent GET-like RPCs retry automatically (exponential jitter); tx submits do not retry blindly.

⸻

13) React examples (hooks-lite)

import { useEffect, useState } from 'react'
import { HttpClient } from '@animica/sdk/rpc/http'

export function UseHead({ rpcUrl }: { rpcUrl: string }) {
  const [height, setHeight] = useState<number | null>(null)
  useEffect(() => {
    const rpc = new HttpClient({ url: rpcUrl })
    let cancel = false
    rpc.call<{ height: number }>('chain.getHead', [])
      .then(h => { if (!cancel) setHeight(h.height) })
      .catch(console.error)
    return () => { cancel = true }
  }, [rpcUrl])
  return <span>Height: {height ?? '…'}</span>
}


⸻

14) Testing (Vitest / Jest)

Mock HTTP with MSW or a tiny handler:

import { HttpClient } from '@animica/sdk/rpc/http'

test('get head', async () => {
  const rpc = new HttpClient({
    url: 'http://test/',
    fetchImpl: async (input, init) => new Response(JSON.stringify({
      jsonrpc: '2.0', id: 1, result: { height: 42, hash: '0x..' }
    }), { status: 200 })
  })
  const head = await rpc.call('chain.getHead', [])
  expect(head.height).toBe(42)
})


⸻

15) Performance & bundle tips
	•	Import specific modules to keep bundles small.
	•	PQ WASM loads lazily at first Signer use; cache the signer instance.
	•	Prefer WS subscriptions over polling for heads/events.
	•	Use AbortController to cancel inflight requests on route changes.

⸻

16) Security notes
	•	Never embed secrets; keys stay client-side (or in the wallet extension).
	•	Enforce chainId on all sign/submit paths.
	•	Validate ABI & user inputs (zod/yup) before building txs.
	•	For dapps: use a strict CSP; avoid inline scripts; pin RPC origin.

⸻

17) Troubleshooting

Issue	Cause	Fix
Unsupported PQ	WASM failed to load	Serve WASM from same origin; ensure Content-Type: application/wasm
FeeTooLow	Fee market floor rose	Rebuild with higher gasPrice or use SDK estimator
NonceGap	Missing sequence	Fetch latest nonce; rebuild tx
WS close 1006	Proxy closes idle	Enable WS keepalive or reconnect with backoff
ABI mismatch	ABI vs contract code out of sync	Re-generate codegen, check address


⸻

18) Minimal end-to-end snippet

import { HttpClient } from '@animica/sdk/rpc/http'
import { buildTransfer } from '@animica/sdk/tx/build'
import { sendTx } from '@animica/sdk/tx/send'
import { Keystore } from '@animica/sdk/wallet/keystore'
import { Signer } from '@animica/sdk/wallet/signer'

const rpc = new HttpClient({ url: 'https://rpc.animica.dev' })
const ks = await Keystore.create({ password: 'pw' })
await ks.importSeed(await (await import('@animica/sdk/wallet/mnemonic')).mnemonicToSeed('...mnemonic...'))
const signer = await Signer.fromKeystore(ks, { alg: 'dilithium3' })

const tx = await buildTransfer({
  chainId: 1,
  from: await signer.getAddress({ hrp: 'anim' }),
  to:   'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv3c9xv',
  amount: '1000000',
  nonce:  await rpc.call('state.getNonce', [await signer.getAddress({ hrp: 'anim' })]),
  gasPrice: '1200',
  gasLimit: '100000',
})
const signed = await signer.signTx(tx)
const { txHash, receipt } = await sendTx(rpc, signed, { awaitReceiptMs: 20_000 })
console.log({ txHash, status: receipt?.status })

You’re set! For deeper dives, explore contracts/codegen.ts, DA/AICF/Randomness clients, and the light-client verifier.
