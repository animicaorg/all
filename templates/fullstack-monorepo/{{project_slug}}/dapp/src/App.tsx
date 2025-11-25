import React, { useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { DappContext } from './main'

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

function short(s: string, n = 6) {
  if (!s) return ''
  if (s.length <= n * 2 + 2) return s
  return `${s.slice(0, n)}…${s.slice(-n)}`
}

function toHexWei(amount: string): string {
  // naive decimal → hex with 18 decimals (like wei), for demo-only
  // accepts "0.1" → hex string; NOT for production money math
  const [intPart, fracPartRaw] = amount.split('.')
  const fracPart = (fracPartRaw ?? '').padEnd(18, '0').slice(0, 18)
  const big = BigInt(intPart || '0') * 10n ** 18n + BigInt(fracPart || '0')
  return `0x${big.toString(16)}`
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

// -----------------------------------------------------------------------------
// App
// -----------------------------------------------------------------------------

export default function App() {
  const { provider, config } = useContext(DappContext)

  // Wallet / session
  const [accounts, setAccounts] = useState<string[]>([])
  const [chainId, setChainId] = useState<string | number | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [connectErr, setConnectErr] = useState<string | null>(null)

  // Node / head
  const [head, setHead] = useState<any | null>(null)
  const [headErr, setHeadErr] = useState<string | null>(null)
  const [headLoading, setHeadLoading] = useState(false)

  // Simple transfer form
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('0.01')
  const [txHash, setTxHash] = useState<string | null>(null)
  const [txErr, setTxErr] = useState<string | null>(null)
  const [txSending, setTxSending] = useState(false)

  // Derived
  const account = useMemo(() => accounts[0] ?? null, [accounts])

  // Detect basic info on mount (if provider injected & already connected)
  useEffect(() => {
    let mounted = true

    async function hydrate() {
      try {
        // chain id via provider if possible, else via RPC
        let cid: any = null
        if (provider) {
          try {
            cid = await provider.request?.({ method: 'wallet_chainId' })
          } catch {
            try {
              cid = await provider.request?.({ method: 'animica_chainId' })
            } catch {
              /* ignore */
            }
          }
        }
        if (!cid) {
          // Try node RPC if wallet not available
          try {
            cid = await jsonRpc(config.rpcUrl, 'animica_chainId')
          } catch {
            // last-resort: use config
            cid = config.chainId
          }
        }
        if (mounted) setChainId(cid)

        // accounts if wallet previously authorized
        if (provider) {
          try {
            const accs = (await provider.request?.({ method: 'wallet_accounts' })) as string[]
            if (mounted && Array.isArray(accs) && accs.length > 0) setAccounts(accs)
          } catch {
            /* not authorized yet */
          }
        }
      } catch (e) {
        console.warn('[animica] init hydration error', e)
      }
    }

    hydrate()
    return () => {
      mounted = false
    }
  }, [provider, config.rpcUrl, config.chainId])

  const connect = useCallback(async () => {
    if (!provider) {
      setConnectErr('No wallet provider detected. Install/enable the Animica extension.')
      return
    }
    setConnecting(true)
    setConnectErr(null)
    try {
      const accs = (await provider.request?.({
        method: 'wallet_requestAccounts',
      })) as string[]
      if (!Array.isArray(accs) || accs.length === 0) throw new Error('No accounts returned')
      setAccounts(accs)
      // refresh chain id after connect (some wallets only resolve post-auth)
      try {
        const cid = await provider.request?.({ method: 'wallet_chainId' })
        if (cid) setChainId(cid as any)
      } catch {
        /* ignore */
      }
    } catch (e: any) {
      setConnectErr(e?.message ?? String(e))
    } finally {
      setConnecting(false)
    }
  }, [provider])

  const fetchHead = useCallback(async () => {
    setHeadLoading(true)
    setHeadErr(null)
    try {
      // Try Animica-specific method first
      let result: any = null
      try {
        result = await jsonRpc<any>(config.rpcUrl, 'animica_getHead', [])
      } catch {
        // Backups for different nodes:
        try {
          result = await jsonRpc<any>(config.rpcUrl, 'chain_getHead', [])
        } catch {
          result = await jsonRpc<any>(config.rpcUrl, 'animica_blockNumber', [])
        }
      }
      setHead(result)
    } catch (e: any) {
      setHeadErr(e?.message ?? String(e))
    } finally {
      setHeadLoading(false)
    }
  }, [config.rpcUrl])

  const sendTx = useCallback(
    async (evt?: React.FormEvent) => {
      evt?.preventDefault()
      setTxErr(null)
      setTxHash(null)
      if (!provider) {
        setTxErr('No wallet provider detected.')
        return
      }
      if (!account) {
        setTxErr('Connect a wallet first.')
        return
      }
      // Super minimal raw transfer shape for demo (will vary per wallet)
      const valueHex = toHexWei(amount || '0')
      const tx: any = {
        from: account,
        to,
        value: valueHex,
        // gas/gasPrice/nonce are left to the wallet / node to fill if supported
      }

      setTxSending(true)
      try {
        const hash = (await provider.request?.({
          method: 'animica_sendTransaction',
          params: [tx],
        })) as string
        setTxHash(hash)
      } catch (e: any) {
        // Try an EVM-ish fallback if wallet proxies it
        try {
          const hash = (await provider.request?.({
            method: 'eth_sendTransaction',
            params: [tx],
          })) as string
          setTxHash(hash)
        } catch (ee: any) {
          setTxErr(ee?.message ?? String(ee))
        }
      } finally {
        setTxSending(false)
      }
    },
    [provider, account, to, amount]
  )

  return (
    <div className="container">
      <header className="header">
        <h1>Animica Full-Stack Dapp</h1>
        <p className="muted">
          Connected to <code>{config.rpcUrl}</code> (chainId: <code>{String(chainId)}</code>)
        </p>
      </header>

      {/* Wallet / Connection -------------------------------------------------- */}
      <section className="card">
        <div className="card-header">
          <h2>Wallet</h2>
        </div>
        <div className="card-body">
          {!provider ? (
            <div className="warning">
              No wallet provider found. Open with the Animica extension enabled or inject{' '}
              <code>window.animica</code>.
            </div>
          ) : (
            <div className="row">
              <div>
                <div className="label">Status</div>
                <div>
                  {account ? (
                    <span className="ok">
                      Connected as <strong title={account}>{short(account)}</strong>
                    </span>
                  ) : (
                    <span className="muted">Not connected</span>
                  )}
                </div>
              </div>

              <div className="spacer" />

              <div>
                <button onClick={connect} disabled={connecting || !!account}>
                  {connecting ? 'Connecting…' : account ? 'Connected' : 'Connect Wallet'}
                </button>
              </div>
            </div>
          )}

          {connectErr && <div className="error">{connectErr}</div>}
        </div>
      </section>

      {/* Node / Head --------------------------------------------------------- */}
      <section className="card">
        <div className="card-header">
          <h2>Node Head</h2>
        </div>
        <div className="card-body">
          <div className="row">
            <button onClick={fetchHead} disabled={headLoading}>
              {headLoading ? 'Fetching…' : 'Fetch head'}
            </button>
          </div>

          {headErr && <div className="error">{headErr}</div>}

          {head && (
            <pre className="pre">
{JSON.stringify(head, null, 2)}
            </pre>
          )}

          {!head && !headErr && !headLoading && (
            <p className="muted">Click “Fetch head” to query the node via JSON-RPC.</p>
          )}
        </div>
      </section>

      {/* Simple Transfer ----------------------------------------------------- */}
      <section className="card">
        <div className="card-header">
          <h2>Send Transfer (demo)</h2>
        </div>
        <div className="card-body">
          <form onSubmit={sendTx} className="form">
            <label>
              To
              <input
                type="text"
                placeholder="0xRecipient…"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                spellCheck={false}
                autoComplete="off"
              />
            </label>

            <label>
              Amount
              <input
                type="text"
                placeholder="0.01"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                inputMode="decimal"
              />
            </label>

            <div className="row">
              <button type="submit" disabled={txSending}>
                {txSending ? 'Sending…' : 'Send'}
              </button>
            </div>
          </form>

          {txHash && (
            <div className="ok">
              Submitted tx: <code title={txHash}>{short(txHash, 10)}</code>
            </div>
          )}
          {txErr && <div className="error">{txErr}</div>}

          <p className="muted">
            This is a minimal example that asks the wallet to build/sign/broadcast a transfer. Exact
            fields and behavior depend on your wallet/provider. For production, use a typed SDK and
            proper units/validation.
          </p>
        </div>
      </section>

      <footer className="footer">
        <span className="muted">
          Tip: In dev, update <code>.env</code> with <code>VITE_RPC_URL</code> and{' '}
          <code>VITE_CHAIN_ID</code>, then restart the dev server.
        </span>
      </footer>
    </div>
  )
}
