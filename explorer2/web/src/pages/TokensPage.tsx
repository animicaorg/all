import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import type { TokenInfo } from '@animica/explorer2-shared'
import { fetchTokens, searchTokens } from '../lib/api'
import { fetchAnmUsd } from '../lib/anmPrice'
import { shorten } from '../lib/format'
import {
  changeClass,
  formatAnmAmount,
  formatChangePct,
  formatCompact,
  formatUsdFromAnm,
  marketCapAnm,
  wholeSupply
} from '../lib/tokenFormat'
import ErrorDisplay from '../components/ErrorDisplay'
import Skeleton from '../components/Skeleton'

const POLL_INTERVAL_MS = 30_000

export default function TokensPage() {
  const [tokens, setTokens] = useState<TokenInfo[] | null>(null)
  const [anmUsd, setAnmUsd] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<TokenInfo[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let mounted = true
    const load = async () => {
      try {
        const [list, usd] = await Promise.all([fetchTokens(200), fetchAnmUsd()])
        if (!mounted) return
        setTokens(list)
        setAnmUsd(usd)
        setError(null)
      } catch (err) {
        if (!mounted) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (mounted) setLoading(false)
      }
    }
    load()
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, POLL_INTERVAL_MS)
    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [retryToken])

  // Debounced server-side search (name / symbol / address).
  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed) {
      setSearchResults(null)
      setSearching(false)
      return
    }
    setSearching(true)
    const timer = setTimeout(async () => {
      try {
        const results = await searchTokens(trimmed, 100)
        setSearchResults(results)
      } catch {
        setSearchResults([])
      } finally {
        setSearching(false)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [query])

  const promoted = useMemo(() => (tokens ?? []).filter((token) => token.promoted), [tokens])
  const displayed = searchResults ?? tokens ?? []

  if (error) {
    return (
      <ErrorDisplay
        error={error}
        onRetry={() => {
          setLoading(true)
          setTokens(null)
          setError(null)
          setRetryToken((value) => value + 1)
        }}
      />
    )
  }

  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-3xl border border-day-200 bg-gradient-to-br from-amber-50 via-white to-sky-100 p-6 shadow-sm dark:border-night-800 dark:bg-gradient-to-br dark:from-night-900 dark:via-night-900 dark:to-slate-900 sm:p-8">
        <div className="pointer-events-none absolute -right-24 -top-20 h-56 w-56 rounded-full bg-animica-500/20 blur-3xl" />
        <div className="relative">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-700 dark:text-amber-300">
            Token Tracker
          </p>
          <h1 className="mt-2 text-3xl font-bold text-gray-900 dark:text-slate-100 sm:text-4xl">ANM-20 Tokens</h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-gray-700 dark:text-slate-300">
            Every AnimicaTokenStandard launch, auto-indexed from chain data — metadata from init calls, promotions
            from on-chain ANMPROMO1 payments, and live pool prices once contract execution is enabled on this
            network.
          </p>
          <div className="mt-5 max-w-xl">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by name, symbol or contract address…"
              className="w-full rounded-xl border border-day-300 bg-white px-4 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-animica-500 focus:outline-none focus:ring-2 focus:ring-animica-500/30 dark:border-night-700 dark:bg-night-900 dark:text-slate-100 dark:placeholder:text-slate-500"
            />
          </div>
        </div>
      </section>

      {!searchResults && promoted.length > 0 && (
        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-700 dark:text-slate-300">
            Promoted
          </h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {promoted.map((token) => (
              <PromotedCard key={token.address} token={token} anmUsd={anmUsd} />
            ))}
          </div>
        </section>
      )}

      <section className="overflow-hidden rounded-2xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex items-center justify-between border-b border-day-200 bg-day-50 px-5 py-4 dark:border-night-800 dark:bg-night-800">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-700 dark:text-slate-300">
            {searchResults ? `Search Results (${displayed.length})` : `All Tokens (${displayed.length})`}
          </h2>
          {searching && <span className="text-xs text-gray-500 dark:text-slate-400">Searching…</span>}
        </div>

        {loading && !tokens ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-lg" />
            ))}
          </div>
        ) : displayed.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-gray-600 dark:text-slate-400">
            {searchResults
              ? 'No tokens match this search.'
              : 'No tokens indexed yet. New AnimicaTokenStandard launches appear here automatically.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-day-200 text-left text-xs uppercase tracking-wider text-gray-500 dark:border-night-800 dark:text-slate-400">
                  <th className="px-5 py-3 font-medium">Token</th>
                  <th className="px-4 py-3 font-medium">Price</th>
                  <th className="px-4 py-3 font-medium">24h</th>
                  <th className="px-4 py-3 font-medium">Market Cap</th>
                  <th className="px-4 py-3 font-medium">Liquidity</th>
                  <th className="px-4 py-3 font-medium">Supply</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-day-200 dark:divide-night-800">
                {displayed.map((token) => (
                  <TokenRow key={token.address} token={token} anmUsd={anmUsd} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="text-xs text-gray-500 dark:text-slate-500">
        Price, liquidity and supply require on-chain contract execution (state.call). Where the node cannot serve
        them yet they are shown as “—” rather than estimated.
      </p>
    </div>
  )
}

function TokenBadge({ token, size = 9 }: { token: TokenInfo; size?: number }) {
  const dimension = `${size * 0.25}rem`
  if (token.imageUrl) {
    return (
      <img
        src={token.imageUrl}
        alt=""
        style={{ width: dimension, height: dimension }}
        className="rounded-full border border-day-200 object-cover dark:border-night-700"
        onError={(event) => {
          ;(event.target as HTMLImageElement).style.display = 'none'
        }}
      />
    )
  }
  return (
    <div
      style={{ width: dimension, height: dimension }}
      className="flex items-center justify-center rounded-full bg-animica-100 text-sm font-bold text-animica-700 dark:bg-animica-900/40 dark:text-animica-300"
    >
      {(token.symbol || token.name || '?').slice(0, 2).toUpperCase()}
    </div>
  )
}

function TokenRow({ token, anmUsd }: { token: TokenInfo; anmUsd: number | null }) {
  const cap = marketCapAnm(token)
  const supply = wholeSupply(token.totalSupply, token.decimals)
  return (
    <tr className="transition-colors hover:bg-day-50 dark:hover:bg-night-800/60">
      <td className="px-5 py-3">
        <Link to={`/token/${token.address}`} className="flex items-center gap-3">
          <TokenBadge token={token} />
          <span>
            <span className="flex items-center gap-2">
              <span className="font-semibold text-gray-900 dark:text-slate-100">
                {token.symbol || shorten(token.address, 10, 6)}
              </span>
              {token.promoted && <PromotedPill daysLeft={token.promoDaysLeft} />}
            </span>
            <span className="block text-xs text-gray-500 dark:text-slate-400">
              {token.name || 'Unnamed token'}
            </span>
          </span>
        </Link>
      </td>
      <td className="px-4 py-3">
        <div className="font-mono text-gray-900 dark:text-slate-100">{formatAnmAmount(token.priceAnm)} {token.priceAnm !== null ? 'ANM' : ''}</div>
        <div className="text-xs text-gray-500 dark:text-slate-400">{formatUsdFromAnm(token.priceAnm, anmUsd)}</div>
      </td>
      <td className={`px-4 py-3 font-medium ${changeClass(token.change24h)}`}>{formatChangePct(token.change24h)}</td>
      <td className="px-4 py-3">
        <div className="text-gray-900 dark:text-slate-100">{cap !== null ? `${formatCompact(cap)} ANM` : '—'}</div>
        <div className="text-xs text-gray-500 dark:text-slate-400">{formatUsdFromAnm(cap, anmUsd)}</div>
      </td>
      <td className="px-4 py-3">
        <div className="text-gray-900 dark:text-slate-100">
          {token.liquidityAnm !== null ? `${formatCompact(token.liquidityAnm)} ANM` : '—'}
        </div>
        <div className="text-xs text-gray-500 dark:text-slate-400">{formatUsdFromAnm(token.liquidityAnm, anmUsd)}</div>
      </td>
      <td className="px-4 py-3 text-gray-900 dark:text-slate-100">{supply !== null ? formatCompact(supply) : '—'}</td>
    </tr>
  )
}

function PromotedCard({ token, anmUsd }: { token: TokenInfo; anmUsd: number | null }) {
  const cap = marketCapAnm(token)
  return (
    <Link
      to={`/token/${token.address}`}
      className="group rounded-2xl border border-amber-200/80 bg-gradient-to-br from-amber-50 to-white p-4 shadow-sm transition-shadow hover:shadow-md dark:border-amber-500/25 dark:from-amber-500/10 dark:to-night-900"
    >
      <div className="flex items-center gap-3">
        <TokenBadge token={token} size={11} />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-semibold text-gray-900 dark:text-slate-100">
              {token.symbol || shorten(token.address, 8, 6)}
            </span>
            <PromotedPill daysLeft={token.promoDaysLeft} />
          </div>
          <p className="truncate text-xs text-gray-600 dark:text-slate-400">{token.name || 'Unnamed token'}</p>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div>
          <p className="text-gray-500 dark:text-slate-500">Price</p>
          <p className="mt-0.5 font-mono font-medium text-gray-900 dark:text-slate-100">{formatAnmAmount(token.priceAnm)}</p>
        </div>
        <div>
          <p className="text-gray-500 dark:text-slate-500">24h</p>
          <p className={`mt-0.5 font-medium ${changeClass(token.change24h)}`}>{formatChangePct(token.change24h)}</p>
        </div>
        <div>
          <p className="text-gray-500 dark:text-slate-500">MCap</p>
          <p className="mt-0.5 font-medium text-gray-900 dark:text-slate-100">
            {cap !== null ? formatUsdFromAnm(cap, anmUsd) : '—'}
          </p>
        </div>
      </div>
    </Link>
  )
}

function PromotedPill({ daysLeft }: { daysLeft: number | null }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">
      ★ Promoted{typeof daysLeft === 'number' ? ` · ${daysLeft}d` : ''}
    </span>
  )
}
