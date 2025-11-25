import React, { useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { DappContext } from '../main'

// -----------------------------------------------------------------------------
// Minimal helpers
// -----------------------------------------------------------------------------

function short(s: string, n = 6) {
  if (!s) return ''
  if (s.length <= n * 2 + 2) return s
  return `${s.slice(0, n)}…${s.slice(-n)}`
}

async function jsonRpc<T = any>(url: string, method: string, params: any[] = []): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ id: Date.now(), jsonrpc: '2.0', method, params }),
  })
  if (!res.ok) throw new Error(`RPC ${method} HTTP ${res.status}`)
  const data = await res.json()
  if (data.error) throw new Error(`RPC ${method} error: ${data.error.message || data.error}`)
  return data.result as T
}

// Naive decimal → hex with 18 decimals (demo money math; do NOT use in prod)
function toHexWei(amount: string): string {
  const [intPart, fracPartRaw] = amount.split('.')
  const fracPart = (fracPartRaw ?? '').padEnd(18, '0').slice(0, 18)
  const big = BigInt(intPart || '0') * 10n ** 18n + BigInt(fracPart || '0')
  return `0x${big.toString(16)}`
}

type AbiParam = { name?: string; type?: string }
type AbiItem = {
  type?: string
  name?: string
  inputs?: AbiParam[]
  outputs?: AbiParam[]
  stateMutability?: 'view' | 'pure' | 'nonpayable' | 'payable' | string
}

// Try to parse ABI JSON. Returns only "function" entries with basic shape.
function parseAbi(abiText: string): AbiItem[] {
  try {
    const j = JSON.parse(abiText)
    const arr: any[] = Array.isArray(j) ? j : []
    return arr
      .filter((x) => x && (x.type === 'function' || x.type === undefined) && typeof x.name === 'string')
      .map((x) => ({
        type: 'function',
        name: x.name,
        inputs: Array.isArray(x.inputs) ? x.inputs : [],
        outputs: Array.isArray(x.outputs) ? x.outputs : [],
        stateMutability: x.stateMutability || 'nonpayable',
      }))
  } catch {
    return []
  }
}

// Rough arg coercion based on ABI type (best-effort demo only)
function coerceArg(type: string | undefined, raw: string): any {
  const t = (type || '').toLowerCase().trim()

  // Arrays: expect JSON input like ["0x..", 123]
  if (t.endsWith('[]') || t.includes('[')) {
    try {
      return JSON.parse(raw)
    } catch {
      return raw
    }
  }

  if (t.startsWith('uint') || t.startsWith('int')) {
    if (raw.startsWith('0x')) return raw
    try {
      const bi = BigInt(raw)
      return `0x${bi.toString(16)}`
    } catch {
      return raw
    }
  }

  if (t === 'bool' || t === 'boolean') {
    return /^true$/i.test(raw) || raw === '1'
  }

  // bytes / address: pass through (expect hex)
  if (t.startsWith('bytes') || t === 'address') return raw

  // default: string
  return raw
}

const SAMPLE_COUNTER_ABI = `[
  { "type": "function", "name": "get", "inputs": [], "outputs": [{"name":"value","type":"uint64"}], "stateMutability": "view" },
  { "type": "function", "name": "inc", "inputs": [{"name":"by","type":"uint64"}], "outputs": [], "stateMutability": "nonpayable" }
]`

// -----------------------------------------------------------------------------
// Page component
// -----------------------------------------------------------------------------

export default function Interact() {
  const { provider, config } = useContext(DappContext)

  // Session
  const [accounts, setAccounts] = useState<string[]>([])
  const account = useMemo(() => accounts[0] ?? '', [accounts])

  // Contract form state
  const [address, setAddress] = useState('')
  const [abiText, setAbiText] = useState(SAMPLE_COUNTER_ABI)
  const abiFns = useMemo(() => parseAbi(abiText), [abiText])

  const [fnIndex, setFnIndex] = useState(0)
  const selectedFn = abiFns[fnIndex]

  const [valueNative, setValueNative] = useState('0') // optional value to send (native tokens)
  const [argInputs, setArgInputs] = useState<string[]>([])

  useEffect(() => {
    // Initialize arg inputs when function changes
    setArgInputs((selectedFn?.inputs || []).map(() => ''))
  }, [fnIndex, abiText]) // eslint-disable-line

  // Results / status
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [txHash, setTxHash] = useState<string | null>(null)

  // On mount, try reading accounts (if already authorized)
  useEffect(() => {
    let mounted = true
    async function hydrate() {
      if (!provider) return
      try {
        const accs = (await provider.request?.({ method: 'wallet_accounts' })) as string[]
        if (mounted && Array.isArray(accs) && accs.length) setAccounts(accs)
      } catch {
        // not connected yet; ignored
      }
    }
    hydrate()
    return () => {
      mounted = false
    }
  }, [provider])

  const connect = useCallback(async () => {
    if (!provider) return
    try {
      const accs = (await provider.request?.({ method: 'wallet_requestAccounts' })) as string[]
      if (Array.isArray(accs)) setAccounts(accs)
    } catch (e) {
      console.warn('wallet connect failed', e)
    }
  }, [provider])

  const isView = (fn: AbiItem | undefined) => {
    const m = (fn?.stateMutability || '').toLowerCase()
    return m === 'view' || m === 'pure'
  }

  const buildArgs = () => {
    const ins = selectedFn?.inputs || []
    return ins.map((p, i) => coerceArg(p.type, argInputs[i] ?? ''))
  }

  // Read-only call via node RPC (preferred), with fallbacks
  const doCall = useCallback(async () => {
    if (!address || !selectedFn?.name) {
      setError('Provide contract address and select a function.')
      return
    }
    setBusy(true)
    setResult(null)
    setError(null)
    setTxHash(null)
    try {
      const args = buildArgs()
      // Preferred Animica RPC
      try {
        const out = await jsonRpc<any>(config.rpcUrl, 'animica_callContract', [
          {
            to: address,
            abi: parseAbi(abiText),
            method: selectedFn.name,
            args,
          },
        ])
        setResult(out)
        return
      } catch (e) {
        // Fallback #1: generic "animica_call"
        try {
          const out = await jsonRpc<any>(config.rpcUrl, 'animica_call', [
            { to: address, data: { method: selectedFn.name, args, abi: parseAbi(abiText) } },
            'latest',
          ])
          setResult(out)
          return
        } catch {
          // Fallback #2: pretend-eth "eth_call" (if node supports)
          const out = await jsonRpc<any>(config.rpcUrl, 'eth_call', [
            { to: address /* data: <encode yourself (not implemented here)> */ },
            'latest',
          ])
          setResult(out)
          return
        }
      }
    } catch (err: any) {
      setError(err?.message ?? String(err))
    } finally {
      setBusy(false)
    }
  }, [address, selectedFn, abiText, argInputs, config.rpcUrl])

  // State-changing send via wallet provider (preferred), with fallbacks
  const doSend = useCallback(async () => {
    if (!provider) {
      setError('No wallet provider detected.')
      return
    }
    if (!account) {
      setError('Connect a wallet first.')
      return
    }
    if (!address || !selectedFn?.name) {
      setError('Provide contract address and select a function.')
      return
    }

    setBusy(true)
    setResult(null)
    setError(null)
    setTxHash(null)
    try {
      const args = buildArgs()
      const value = valueNative && valueNative !== '0' ? toHexWei(valueNative) : undefined

      // Preferred: explicit contract send (Animica wallet-aware)
      try {
        const hash = (await provider.request?.({
          method: 'animica_sendContract',
          params: [
            {
              from: account,
              to: address,
              abi: parseAbi(abiText),
              method: selectedFn.name,
              args,
              value,
            },
          ],
        })) as string
        setTxHash(hash)
        return
      } catch (e) {
        // Fallback #1: generic sendTransaction with a contractCall payload the wallet may understand
        try {
          const hash = (await provider.request?.({
            method: 'animica_sendTransaction',
            params: [
              {
                from: account,
                to: address,
                value,
                // Many wallets ignore non-standard fields; this is just a hint.
                contractCall: { method: selectedFn.name, args, abi: parseAbi(abiText) },
              },
            ],
          })) as string
          setTxHash(hash)
          return
        } catch (ee) {
          // Fallback #2: EVM-ish eth_sendTransaction (requires ABI encoding you provide — not implemented)
          const hash = (await provider.request?.({
            method: 'eth_sendTransaction',
            params: [
              {
                from: account,
                to: address,
                value,
                // data: encodeFunctionData(...) // left as an exercise if using EVM ABI
              },
            ],
          })) as string
          setTxHash(hash)
          return
        }
      }
    } catch (err: any) {
      setError(err?.message ?? String(err))
    } finally {
      setBusy(false)
    }
  }, [provider, account, address, selectedFn, abiText, argInputs, valueNative])

  // ---------------------------------------------------------------------------

  return (
    <div className="container">
      <header className="header">
        <h1>Interact with a Contract</h1>
        <p className="muted">
          Paste an ABI, select a function, enter arguments, then run a read-only call or send a
          state-changing transaction.
        </p>
      </header>

      {/* Wallet status ------------------------------------------------------ */}
      <section className="card">
        <div className="card-header">
          <h2>Wallet</h2>
        </div>
        <div className="card-body">
          <div className="row">
            <div>
              <div className="label">Account</div>
              <div>
                {account ? (
                  <span className="ok">
                    <strong title={account}>{short(account)}</strong>
                  </span>
                ) : (
                  <span className="muted">Not connected</span>
                )}
              </div>
            </div>
            <div className="spacer" />
            <div>
              <button onClick={connect} disabled={!!account}>
                {account ? 'Connected' : 'Connect Wallet'}
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Contract target ---------------------------------------------------- */}
      <section className="card">
        <div className="card-header">
          <h2>Target Contract</h2>
        </div>
        <div className="card-body">
          <label>
            Contract Address
            <input
              type="text"
              placeholder="0xContractAddress..."
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              spellCheck={false}
              autoComplete="off"
            />
          </label>

          <label>
            ABI JSON
            <textarea
              value={abiText}
              onChange={(e) => setAbiText(e.target.value)}
              rows={10}
              spellCheck={false}
            />
          </label>

          <div className="muted" style={{ marginTop: 6 }}>
            Hint: A simple Counter ABI is prefilled (functions <code>get()</code> and{' '}
            <code>inc(uint64)</code>).
          </div>
        </div>
      </section>

      {/* Function + Args ---------------------------------------------------- */}
      <section className="card">
        <div className="card-header">
          <h2>Function &amp; Arguments</h2>
        </div>
        <div className="card-body">
          <label>
            Function
            <select
              value={fnIndex}
              onChange={(e) => setFnIndex(Number(e.target.value))}
              disabled={abiFns.length === 0}
            >
              {abiFns.length === 0 ? (
                <option value={0}>No functions detected</option>
              ) : (
                abiFns.map((f, i) => (
                  <option key={`${f.name}-${i}`} value={i}>
                    {f.name}({(f.inputs || [])
                      .map((p) => (p.type ? `${p.type}${p.name ? ' ' + p.name : ''}` : ''))
                      .join(', ')})
                    {f.stateMutability ? ` — ${f.stateMutability}` : ''}
                  </option>
                ))
              )}
            </select>
          </label>

          {selectedFn && selectedFn.inputs && selectedFn.inputs.length > 0 && (
            <div className="form-grid">
              {selectedFn.inputs.map((p, i) => (
                <label key={`${p.name || 'arg'}-${i}`}>
                  {p.name || `arg${i}`} <span className="muted">{p.type || 'any'}</span>
                  <input
                    type="text"
                    value={argInputs[i] || ''}
                    onChange={(e) =>
                      setArgInputs((prev) => {
                        const next = [...prev]
                        next[i] = e.target.value
                        return next
                      })
                    }
                    placeholder={
                      (p.type || '').includes('[]')
                        ? 'e.g. ["0x01","0x02"]'
                        : p.type?.startsWith('uint')
                        ? 'e.g. 42'
                        : p.type === 'bool'
                        ? 'true/false'
                        : 'value'
                    }
                    spellCheck={false}
                    autoComplete="off"
                  />
                </label>
              ))}
            </div>
          )}

          {!isView(selectedFn) && (
            <label style={{ marginTop: 12 }}>
              Send Value (native)
              <input
                type="text"
                value={valueNative}
                onChange={(e) => setValueNative(e.target.value)}
                inputMode="decimal"
                placeholder="0"
              />
              <span className="muted"> (decimal, converted to 18-decimal base units)</span>
            </label>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            {isView(selectedFn) ? (
              <button onClick={doCall} disabled={busy || !selectedFn}>
                {busy ? 'Calling…' : 'Call (read)'}
              </button>
            ) : (
              <>
                <button onClick={doCall} disabled={busy || !selectedFn}>
                  {busy ? 'Calling…' : 'Simulate (read)'}
                </button>
                <div className="spacer" />
                <button onClick={doSend} disabled={busy || !selectedFn}>
                  {busy ? 'Sending…' : 'Send (write)'}
                </button>
              </>
            )}
          </div>

          {txHash && (
            <div className="ok" style={{ marginTop: 10 }}>
              Submitted tx: <code title={txHash}>{short(txHash, 10)}</code>
            </div>
          )}
          {error && (
            <div className="error" style={{ marginTop: 10 }}>
              {error}
            </div>
          )}
          {result !== null && (
            <>
              <div className="label" style={{ marginTop: 12 }}>
                Result
              </div>
              <pre className="pre">
{JSON.stringify(result, null, 2)}
              </pre>
            </>
          )}

          {!selectedFn && (
            <p className="muted" style={{ marginTop: 8 }}>
              Paste a valid ABI JSON and pick a function.
            </p>
          )}
        </div>
      </section>

      {/* Notes -------------------------------------------------------------- */}
      <section className="card">
        <div className="card-header">
          <h2>Notes</h2>
        </div>
        <div className="card-body">
          <ul>
            <li>
              This page uses best-effort RPCs: <code>animica_callContract</code> /{' '}
              <code>animica_sendContract</code> where available, with fallbacks to generic calls.
              Nodes and wallets may implement different methods — consult your node/wallet docs.
            </li>
            <li>
              Argument coercion is intentionally simple. For arrays, input JSON (e.g.{' '}
              <code>["0x01","0x02"]</code>). For integers, decimals are converted to hex.
            </li>
            <li>
              For production apps, prefer a typed SDK and strict ABI encoding/decoding. This page is
              designed for quick experiments on devnets.
            </li>
          </ul>
        </div>
      </section>
    </div>
  )
}
