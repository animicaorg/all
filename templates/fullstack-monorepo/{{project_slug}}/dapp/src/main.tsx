import React, { StrictMode, useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

// ---- Types & globals --------------------------------------------------------

export type DappConfig = {
  rpcUrl: string
  chainId: number
  servicesUrl?: string
}

type AnimicaProvider = {
  request: (args: { method: string; params?: unknown[] | object }) => Promise<unknown>
  on?: (event: string, listener: (...args: any[]) => void) => void
  removeListener?: (event: string, listener: (...args: any[]) => void) => void
  // Add more surface as your extension/provider grows
}

declare global {
  interface Window {
    animica?: AnimicaProvider
  }
}

// ---- Env → runtime config ---------------------------------------------------

const config: DappConfig = {
  rpcUrl: (import.meta as any).env.VITE_RPC_URL ?? 'http://localhost:8545',
  chainId: Number((import.meta as any).env.VITE_CHAIN_ID ?? 1337),
  servicesUrl: (import.meta as any).env.VITE_SERVICES_URL,
}

// ---- Provider detection & readiness -----------------------------------------

/**
 * Detects an injected Animica provider (wallet extension) if present.
 * Returns null if not available. We also listen for a custom initialization
 * event to support late injection after page load (similar to EIP-1193 patterns).
 */
function detectProvider(): AnimicaProvider | null {
  if (typeof window === 'undefined') return null
  return window.animica ?? null
}

/**
 * Wait briefly for a provider to be injected. If none appears, resolve null.
 * This avoids a jarring "no wallet" flash for users whose extension initializes late.
 */
async function waitForProvider(timeoutMs = 1500): Promise<AnimicaProvider | null> {
  const existing = detectProvider()
  if (existing) return existing

  return new Promise((resolve) => {
    let settled = false
    const finish = (prov: AnimicaProvider | null) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(prov)
    }

    const onInit = () => finish(detectProvider())

    // Custom event hook (your extension can dispatch this once ready)
    window.addEventListener('animica#initialized', onInit as EventListener)

    const t = setTimeout(() => finish(null), timeoutMs)
    const cleanup = () => {
      clearTimeout(t)
      window.removeEventListener('animica#initialized', onInit as EventListener)
    }
  })
}

// ---- React context to share provider/config ---------------------------------

export const DappContext = React.createContext<{
  provider: AnimicaProvider | null
  config: DappConfig
}>({
  provider: null,
  config,
})

function DappProvider({ children }: { children: React.ReactNode }) {
  const [provider, setProvider] = useState<AnimicaProvider | null>(detectProvider())

  useEffect(() => {
    let mounted = true
    if (!provider) {
      waitForProvider().then((p) => {
        if (mounted) setProvider(p)
      })
    }

    // If the provider supports events, keep basic session state in sync
    const handleChainChanged = () => {
      // You might refetch balances, clear caches, etc.
      console.info('[animica] chain changed')
    }
    const handleAccountsChanged = () => {
      // You might refresh UI and clear pending requests
      console.info('[animica] accounts changed')
    }

    if (provider?.on) {
      provider.on('chainChanged', handleChainChanged)
      provider.on('accountsChanged', handleAccountsChanged)
    }

    return () => {
      mounted = false
      if (provider?.removeListener) {
        provider.removeListener('chainChanged', handleChainChanged)
        provider.removeListener('accountsChanged', handleAccountsChanged)
      }
    }
  }, [provider])

  const value = useMemo(() => ({ provider, config }), [provider])

  return <DappContext.Provider value={value}>{children}</DappContext.Provider>
}

// ---- Mount ------------------------------------------------------------------

const container = document.getElementById('root')
if (!container) {
  throw new Error('Missing #root element; ensure index.html contains <div id="root"></div>')
}

const root = createRoot(container)

console.info(
  '[animica] booting dapp',
  { rpcUrl: config.rpcUrl, chainId: config.chainId, servicesUrl: config.servicesUrl }
)

root.render(
  <StrictMode>
    <DappProvider>
      <App />
    </DappProvider>
  </StrictMode>
)

// Enable HMR in dev
if (import.meta.hot) {
  import.meta.hot.accept()
}
