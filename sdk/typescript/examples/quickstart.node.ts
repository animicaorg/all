/**
 * @file quickstart.node.ts
 * Minimal Node demo using the TypeScript SDK.
 *
 * What it does:
 *  1) Connects to the node over HTTP JSON-RPC and prints the current head.
 *  2) (Optional) Connects to WS and prints a few newHeads.
 *  3) (Optional) If MNEMONIC and TO_ADDR are set, builds & sends a tiny transfer,
 *     then waits for its receipt.
 *
 * Env:
 *   RPC_URL  (default: http://127.0.0.1:8545/rpc)
 *   WS_URL   (optional:  ws://127.0.0.1:8545/ws)
 *   MNEMONIC (optional: BIP-like words for a local signer)
 *   TO_ADDR  (optional: bech32m anim1... address to receive a transfer)
 *   AMOUNT   (optional: integer units, default 1234)
 */

import 'node:process'
import { setTimeout as delay } from 'node:timers/promises'

// ──────────────────────────────────────────────────────────────────────────────
// Helper: tolerant dynamic import + function pickers so this example stays
// compatible with minor API name differences within the SDK.
// ──────────────────────────────────────────────────────────────────────────────
function pick<T extends Function>(obj: any, names: string[]): T | undefined {
  for (const n of names) {
    const fn = obj?.[n] ?? obj?.default?.[n]
    if (typeof fn === 'function') return fn as unknown as T
  }
  return undefined
}
function pickClass<T>(obj: any, names: string[]): { new (...args: any[]): T } | undefined {
  for (const n of names) {
    const cls = obj?.[n] ?? obj?.default?.[n]
    if (typeof cls === 'function') return cls as any
  }
  return undefined
}
async function importRpcHttp() {
  const mod = await import('../src/rpc/http')
  const createHttpClient = pick<(opts: { url: string; timeoutMs?: number }) => any>(mod, ['createHttpClient', 'create'])
  const HttpClient = pickClass<any>(mod, ['HttpClient', 'JsonRpcHttp', 'Client'])
  const request = async (client: any, method: string, params?: any) => {
    if (typeof client?.request === 'function') return client.request(method, params)
    if (typeof client?.call === 'function') return client.call(method, params)
    if (typeof client?.send === 'function') return client.send(method, params)
    throw new Error('HTTP client has no request/call/send')
  }
  return { createHttpClient, HttpClient, request }
}
async function importRpcWs() {
  try {
    const mod = await import('../src/rpc/ws')
    const createWs = pick<(opts: { url: string }) => any>(mod, ['createWsClient', 'create'])
    return { createWs }
  } catch {
    return { createWs: undefined }
  }
}
async function importWallet() {
  try {
    const signMod = await import('../src/wallet/signer')
    const createSigner = pick<(opts: { alg: string; seed?: Uint8Array; mnemonic?: string }) => Promise<any>>(signMod, ['createSigner'])
    const mnemonicMod = await import('../src/wallet/mnemonic').catch(() => ({} as any))
    const toSeed = pick<(mnemo: string) => Promise<Uint8Array> | Uint8Array>(mnemonicMod, ['mnemonicToSeed', 'toSeed', 'mnemonicToBytes'])
    return { createSigner, toSeed }
  } catch {
    return { createSigner: undefined, toSeed: undefined }
  }
}
async function importTx() {
  const buildMod = await import('../src/tx/build')
  const encodeMod = await import('../src/tx/encode')
  const sendMod = await import('../src/tx/send').catch(() => ({} as any))

  const buildTransfer = pick<(a: any) => any>(buildMod, ['buildTransfer', 'transfer'])
  const estimateGas = pick<(a: any) => Promise<number> | number>(buildMod, ['estimateGas', 'estimate'])
  const encodeSignBytes = pick<(a: any) => Uint8Array>(encodeMod, ['encodeSignBytes', 'signBytes', 'encode'])
  const sendRawTx = pick<(client: any, raw: string | Uint8Array) => Promise<string>>(sendMod, ['sendRawTransaction', 'sendRawTx', 'broadcast'])
  const awaitReceipt = pick<(client: any, hash: string, opts?: any) => Promise<any>>(sendMod, ['awaitReceipt', 'waitForReceipt'])

  return { buildTransfer, estimateGas, encodeSignBytes, sendRawTx, awaitReceipt }
}

// Hex helpers
const toHex = (u8: Uint8Array): string => '0x' + Buffer.from(u8).toString('hex')
const fromHex = (hex: string): Uint8Array => new Uint8Array(Buffer.from(hex.replace(/^0x/, ''), 'hex'))

// ──────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────-
async function main() {
  const RPC_URL = process.env.RPC_URL || 'http://127.0.0.1:8545/rpc'
  const WS_URL = process.env.WS_URL // optional
  const MNEMONIC = process.env.MNEMONIC
  const TO_ADDR = process.env.TO_ADDR
  const AMOUNT = Number(process.env.AMOUNT || 1234)

  console.log('RPC_URL =', RPC_URL)

  // HTTP client
  const { createHttpClient, HttpClient, request } = await importRpcHttp()
  const http = createHttpClient ? createHttpClient({ url: RPC_URL }) : new HttpClient!({ url: RPC_URL })

  // Basic calls
  let chainId: number | string | undefined
  try {
    chainId = await request(http, 'chain.getChainId', [])
  } catch { /* older nodes may not expose it */ }
  const head = await request(http, 'chain.getHead', [])
  console.log('→ chainId:', chainId ?? '(unknown)')
  console.log('→ head:', head)

  // Optional WS subscription
  if (WS_URL) {
    const { createWs } = await importRpcWs()
    if (createWs) {
      console.log('Connecting WS:', WS_URL)
      const ws = createWs({ url: WS_URL })
      let seen = 0
      await new Promise<void>((resolve, reject) => {
        ws.on('open', () => {
          // Common subscription shapes; try a few
          const msgs = [
            { jsonrpc: '2.0', id: 1, method: 'subscribe', params: ['newHeads'] },
            { jsonrpc: '2.0', id: 2, method: 'ws.subscribe', params: ['newHeads'] }
          ]
          for (const m of msgs) try { ws.send(JSON.stringify(m)) } catch {}
        })
        ws.on('message', (buf: any) => {
          try {
            const msg = JSON.parse(buf.toString())
            if (msg?.method && /newHeads/i.test(String(msg.method))) {
              console.log('WS newHead:', msg.params?.result ?? msg.params)
              seen++
              if (seen >= 3) {
                try { ws.close() } catch {}
                resolve()
              }
            }
          } catch { /* ignore */ }
        })
        ws.on('error', (e: any) => {
          console.warn('WS error (continuing without):', e?.message ?? e)
          resolve()
        })
        ws.on('close', () => resolve())
      })
    } else {
      console.warn('WS module not available; skipping subscription demo.')
    }
  }

  // Optional: build & send a tiny transfer if enough pieces are available.
  if (MNEMONIC && TO_ADDR) {
    console.log('\nPreparing a tiny transfer demo…')
    const { createSigner, toSeed } = await importWallet()
    const { buildTransfer, estimateGas, encodeSignBytes, sendRawTx, awaitReceipt } = await importTx()

    if (!createSigner || !buildTransfer || !encodeSignBytes || !sendRawTx) {
      console.warn('SDK pieces missing (signer/build/encode/send). Skipping transfer demo.')
      return
    }

    // Seed → signer
    const seed = toSeed ? await toSeed(MNEMONIC) : new TextEncoder().encode(MNEMONIC).slice(0, 32)
    const signer = await createSigner({ alg: 'dilithium3', seed }) // prefer dilithium3 by default
    const getPub =
      signer.publicKey ??
      (typeof signer.getPublicKey === 'function' ? signer.getPublicKey() : undefined)
    if (!getPub || !(getPub instanceof Uint8Array)) {
      console.warn('Signer missing publicKey; cannot derive address. Skipping transfer demo.')
      return
    }

    // Derive address (best-effort — rely on SDK address module if present)
    let fromAddr = '(unknown)'
    try {
      const addrMod = await import('../src/address')
      const toBech32 =
        addrMod?.toBech32 || addrMod?.encodeAddress || addrMod?.default?.toBech32 || addrMod?.default?.encodeAddress
      const CHAIN_ID = Number(chainId || 1)
      fromAddr = typeof toBech32 === 'function'
        ? toBech32({ chainId: CHAIN_ID, algId: signer.alg || 'dilithium3', publicKey: getPub })
        : '0x' + Buffer.from(await (await import('../src/utils/hash')).sha3_256(getPub)).toString('hex')
    } catch {
      const { sha3_256 } = await import('../src/utils/hash')
      fromAddr = '0x' + Buffer.from(sha3_256(getPub)).toString('hex')
    }
    console.log('From:', fromAddr)
    console.log('To  :', TO_ADDR)

    // Fetch nonce & balance if available
    let nonce = 0
    try {
      nonce = await request(http, 'state.getNonce', [fromAddr])
    } catch { /* ok */ }

    // Build tx
    const draft = buildTransfer({
      from: fromAddr,
      to: TO_ADDR,
      value: AMOUNT,
      nonce,
      chainId: chainId ?? 1,
      gasPrice: 1,          // conservative defaults; adjust if your node enforces floors
      gasLimit: 50_000
    })

    // Estimate gas if helper exists
    try {
      const g = await estimateGas?.(draft)
      if (g && Number(g) > 0) (draft as any).gasLimit = Number(g)
    } catch { /* ignore */ }

    // SignBytes → signature → raw tx
    const signBytes = encodeSignBytes(draft)
    const sig = await signer.sign(signBytes)
    ;(draft as any).signature = typeof sig === 'string' ? sig : toHex(sig)

    // Broadcast
    const txHash = await sendRawTx(http, (draft as any).raw || (draft as any))
    console.log('→ sent tx:', txHash)

    // Await receipt (fallback to manual poll if helper absent)
    let receipt: any
    if (awaitReceipt) {
      receipt = await awaitReceipt(http, txHash, { timeoutMs: 30_000, pollIntervalMs: 1000 }).catch(() => null)
    }
    if (!receipt) {
      const start = Date.now()
      while (Date.now() - start < 30_000) {
        await delay(1000)
        try {
          receipt = await request(http, 'tx.getTransactionReceipt', [txHash])
          if (receipt) break
        } catch { /* keep polling */ }
      }
    }
    if (receipt) {
      console.log('✓ receipt:', receipt)
    } else {
      console.warn('Timed out waiting for receipt (tx may still land).')
    }
  } else {
    console.log('\nTip: set MNEMONIC and TO_ADDR to try a tiny transfer demo.')
  }
}

main().catch((err) => {
  console.error(err)
  process.exitCode = 1
})
