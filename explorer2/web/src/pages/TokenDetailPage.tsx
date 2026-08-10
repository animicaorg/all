import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { TokenInfo, TokenPricePoint } from '@animica/explorer2-shared'
import { fetchToken, fetchTokenHistory } from '../lib/api'
import { fetchAnmUsd } from '../lib/anmPrice'
import { formatNumber, formatTimestamp, shorten } from '../lib/format'
import {
  changeClass,
  formatAnmAmount,
  formatChangePct,
  formatCompact,
  formatUsdFromAnm,
  marketCapAnm,
  wholeSupply
} from '../lib/tokenFormat'
import CopyButton from '../components/CopyButton'
import ErrorDisplay from '../components/ErrorDisplay'
import Skeleton from '../components/Skeleton'
import StatCard from '../components/StatCard'

const RANGES = ['24h', '7d', '30d', 'all'] as const
type Range = (typeof RANGES)[number]

export default function TokenDetailPage() {
  const { address = '' } = useParams()
  const [token, setToken] = useState<TokenInfo | null>(null)
  const [history, setHistory] = useState<TokenPricePoint[]>([])
  const [anmUsd, setAnmUsd] = useState<number | null>(null)
  const [range, setRange] = useState<Range>('7d')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let mounted = true
    setLoading(true)
    const load = async () => {
      try {
        const [info, usd] = await Promise.all([fetchToken(address), fetchAnmUsd()])
        if (!mounted) return
        setToken(info)
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
    return () => {
      mounted = false
    }
  }, [address, retryToken])

  useEffect(() => {
    let mounted = true
    fetchTokenHistory(address, range)
      .then((points) => {
        if (mounted) setHistory(points)
      })
      .catch(() => {
        if (mounted) setHistory([])
      })
    return () => {
      mounted = false
    }
  }, [address, range, retryToken])

  if (error) {
    return (
      <ErrorDisplay
        error={error}
        onRetry={() => {
          setError(null)
          setRetryToken((value) => value + 1)
        }}
      />
    )
  }

  if (loading && !token) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-28 w-full rounded-2xl" />
        <Skeleton className="h-64 w-full rounded-2xl" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
      </div>
    )
  }

  if (!token) return null

  const cap = marketCapAnm(token)
  const supply = wholeSupply(token.totalSupply, token.decimals)
  const initialSupply = wholeSupply(token.initialSupply ?? null, token.decimals)
  const maxSupply = wholeSupply(token.maxSupply ?? null, token.decimals)
  const links = token.links ?? {}
  const hasLinks = Object.keys(links).length > 0

  return (
    <div className="space-y-6">
      {/* Header */}
      <section className="rounded-2xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-4">
            <TokenImage token={token} />
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">
                  {token.name || 'Unnamed token'}
                </h1>
                {token.symbol && (
                  <span className="rounded-full bg-animica-100 px-2.5 py-0.5 text-sm font-semibold text-animica-700 dark:bg-animica-900/40 dark:text-animica-300">
                    {token.symbol}
                  </span>
                )}
                {token.promoted && (
                  <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wide text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">
                    ★ Promoted{typeof token.promoDaysLeft === 'number' ? ` · ${token.promoDaysLeft}d left` : ''}
                  </span>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Link
                  to={`/address/${token.address}`}
                  className="font-mono text-sm text-animica-700 hover:underline dark:text-animica-300"
                >
                  {shorten(token.address, 18, 12)}
                </Link>
                <CopyButton value={token.address} />
              </div>
            </div>
          </div>
          <div className="text-right">
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Price</p>
            <p className="mt-1 font-mono text-2xl font-bold text-gray-900 dark:text-slate-100">
              {token.priceAnm !== null ? `${formatAnmAmount(token.priceAnm)} ANM` : '—'}
            </p>
            <p className="text-sm text-gray-500 dark:text-slate-400">{formatUsdFromAnm(token.priceAnm, anmUsd)}</p>
            <p className={`mt-1 text-sm font-medium ${changeClass(token.change24h)}`}>
              {formatChangePct(token.change24h)} (24h)
            </p>
          </div>
        </div>
        {token.description && (
          <p className="mt-4 max-w-3xl text-sm leading-relaxed text-gray-700 dark:text-slate-300">{token.description}</p>
        )}
        {hasLinks && (
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(links).map(([label, url]) => (
              <a
                key={label}
                href={url}
                target="_blank"
                rel="noreferrer"
                className="rounded-full border border-day-300 px-3 py-1 text-xs font-medium capitalize text-gray-700 transition-colors hover:border-animica-500 hover:text-animica-700 dark:border-night-700 dark:text-slate-300 dark:hover:border-animica-500 dark:hover:text-animica-300"
              >
                {label} ↗
              </a>
            ))}
          </div>
        )}
      </section>

      {/* Price chart */}
      <section className="rounded-2xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-slate-100">Price History (ANM)</h2>
          <div className="flex gap-1">
            {RANGES.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setRange(option)}
                className={`rounded-lg px-3 py-1 text-xs font-medium transition-colors ${
                  range === option
                    ? 'bg-animica-600 text-white'
                    : 'bg-day-100 text-gray-600 hover:bg-day-200 dark:bg-night-800 dark:text-slate-300 dark:hover:bg-night-700'
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
        <TokenPriceChart points={history} />
        {history.length === 0 && (
          <p className="mt-3 text-xs text-gray-500 dark:text-slate-500">
            No price samples yet — live pool prices require on-chain contract execution, which is not enabled on
            this network yet. History fills in automatically once it is.
          </p>
        )}
      </section>

      {/* Market stats */}
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-700 dark:text-slate-300">
          Market
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Price"
            value={<Dual anm={token.priceAnm !== null ? `${formatAnmAmount(token.priceAnm)} ANM` : null} usd={formatUsdFromAnm(token.priceAnm, anmUsd)} />}
          />
          <StatCard
            label="Market Cap (FDV)"
            value={<Dual anm={cap !== null ? `${formatCompact(cap)} ANM` : null} usd={formatUsdFromAnm(cap, anmUsd)} />}
          />
          <StatCard
            label="Liquidity"
            value={
              <Dual
                anm={token.liquidityAnm !== null ? `${formatCompact(token.liquidityAnm)} ANM` : null}
                usd={formatUsdFromAnm(token.liquidityAnm, anmUsd)}
              />
            }
          />
          <StatCard
            label="24h Change"
            value={<span className={changeClass(token.change24h)}>{formatChangePct(token.change24h)}</span>}
          />
        </div>
      </section>

      {/* Token stats */}
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-700 dark:text-slate-300">
          Token
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Total Supply" value={supply !== null ? formatCompact(supply) : <NotAvailable />} />
          <StatCard label="Initial Supply" value={initialSupply !== null ? formatCompact(initialSupply) : '—'} />
          <StatCard
            label="Max Supply"
            value={
              token.maxSupply === '0'
                ? 'Uncapped'
                : maxSupply !== null
                  ? formatCompact(maxSupply)
                  : '—'
            }
          />
          <StatCard label="Decimals" value={formatNumber(token.decimals)} />
          <StatCard label="Mintable" value={token.mintable === null || token.mintable === undefined ? '—' : token.mintable ? 'Yes' : 'No'} />
          <StatCard
            label="Swap Fee"
            value={token.feeBps !== null && token.feeBps !== undefined ? `${(token.feeBps / 100).toFixed(2)}%` : <NotAvailable />}
          />
          <StatCard label="Holders" value={token.holders !== null && token.holders !== undefined ? formatNumber(token.holders) : <NotAvailable />} />
          <StatCard
            label="ANM Pool"
            value={
              token.pairAddress ? (
                <Link className="font-mono text-sm text-animica-700 hover:underline dark:text-animica-300" to={`/address/${token.pairAddress}`}>
                  {shorten(token.pairAddress, 10, 8)}
                </Link>
              ) : (
                <NotAvailable />
              )
            }
          />
        </div>
      </section>

      {/* Creation */}
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-700 dark:text-slate-300">
          Creation
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Creator"
            value={
              token.creator ? (
                <Link className="font-mono text-sm text-animica-700 hover:underline dark:text-animica-300" to={`/address/${token.creator}`}>
                  {shorten(token.creator, 10, 8)}
                </Link>
              ) : (
                '—'
              )
            }
          />
          <StatCard
            label="Creation Block"
            value={
              token.creationHeight !== null && token.creationHeight !== undefined ? (
                <Link className="text-animica-700 hover:underline dark:text-animica-300" to={`/block/${token.creationHeight}`}>
                  #{formatNumber(token.creationHeight)}
                </Link>
              ) : (
                '—'
              )
            }
          />
          <StatCard
            label="Deploy Tx"
            value={
              token.creationTx ? (
                <Link className="font-mono text-sm text-animica-700 hover:underline dark:text-animica-300" to={`/tx/${token.creationTx}`}>
                  {shorten(token.creationTx, 10, 8)}
                </Link>
              ) : (
                '—'
              )
            }
          />
          <StatCard label="Created" value={token.creationTime ? formatTimestamp(token.creationTime) : '—'} />
        </div>
      </section>

      {token.metadataUri && (
        <p className="text-xs text-gray-500 dark:text-slate-500">
          Metadata URI: <span className="break-all font-mono">{token.metadataUri}</span>
        </p>
      )}
      <p className="text-xs text-gray-500 dark:text-slate-500">
        “Not available yet on this network” fields need on-chain contract execution (state.call) — the explorer
        reports only what the chain can prove, and these light up automatically once execution is enabled.
      </p>
    </div>
  )
}

function Dual({ anm, usd }: { anm: string | null; usd: string }) {
  return (
    <span>
      <span>{anm ?? '—'}</span>
      <span className="block text-xs font-normal text-gray-500 dark:text-slate-400">{usd}</span>
    </span>
  )
}

function NotAvailable() {
  return (
    <span className="text-sm font-normal text-gray-400 dark:text-slate-500" title="Requires on-chain contract execution (state.call), not enabled on this network yet">
      not available yet
    </span>
  )
}

function TokenImage({ token }: { token: TokenInfo }) {
  const [failed, setFailed] = useState(false)
  if (token.imageUrl && !failed) {
    return (
      <img
        src={token.imageUrl}
        alt={token.symbol || token.name || 'token'}
        className="h-16 w-16 rounded-2xl border border-day-200 object-cover dark:border-night-700"
        onError={() => setFailed(true)}
      />
    )
  }
  return (
    <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-animica-100 text-xl font-bold text-animica-700 dark:bg-animica-900/40 dark:text-animica-300">
      {(token.symbol || token.name || '?').slice(0, 2).toUpperCase()}
    </div>
  )
}

// ── Price chart (same visual language as ThetaMicroChart) ─────────────────────

const WIDTH = 760
const HEIGHT = 240
const PADDING = { top: 16, right: 16, bottom: 24, left: 64 }

function TokenPriceChart({ points }: { points: TokenPricePoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)

  const chart = useMemo(() => {
    const series = [...points].sort((a, b) => a.t - b.t).filter((point) => Number.isFinite(point.priceAnm) && point.priceAnm > 0)
    if (!series.length) return null
    const plotWidth = WIDTH - PADDING.left - PADDING.right
    const plotHeight = HEIGHT - PADDING.top - PADDING.bottom
    const values = series.map((point) => point.priceAnm)
    const rawMin = Math.min(...values)
    const rawMax = Math.max(...values)
    const span = rawMax - rawMin
    const min = rawMin - (span > 0 ? span * 0.1 : rawMin * 0.05 || 1)
    const max = rawMax + (span > 0 ? span * 0.1 : rawMax * 0.05 || 1)
    const safeSpan = Math.max(max - min, Number.EPSILON)
    const tMin = series[0].t
    const tMax = series[series.length - 1].t
    const tSpan = Math.max(tMax - tMin, 1)
    const plot = series.map((point) => ({
      ...point,
      x: PADDING.left + ((point.t - tMin) / tSpan) * plotWidth,
      y: PADDING.top + ((max - point.priceAnm) / safeSpan) * plotHeight
    }))
    const path = plot.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ')
    const area = [
      `M ${plot[0].x.toFixed(2)} ${(HEIGHT - PADDING.bottom).toFixed(2)}`,
      ...plot.map((p) => `L ${p.x.toFixed(2)} ${p.y.toFixed(2)}`),
      `L ${plot[plot.length - 1].x.toFixed(2)} ${(HEIGHT - PADDING.bottom).toFixed(2)}`,
      'Z'
    ].join(' ')
    const ticks = Array.from({ length: 4 }, (_, i) => {
      const ratio = i / 3
      return { value: max - (max - min) * ratio, y: PADDING.top + (HEIGHT - PADDING.top - PADDING.bottom) * ratio }
    })
    return { plot, path, area, ticks }
  }, [points])

  if (!chart) {
    return (
      <div className="flex h-56 items-center justify-center rounded-xl border border-dashed border-day-300 text-sm text-gray-500 dark:border-night-700 dark:text-slate-400">
        No price data
      </div>
    )
  }

  const hovered = hoverIndex !== null ? chart.plot[hoverIndex] : null

  const onMove = (clientX: number, rect: DOMRect) => {
    const x = ((clientX - rect.left) / rect.width) * WIDTH
    let best = 0
    for (let i = 1; i < chart.plot.length; i += 1) {
      if (Math.abs(chart.plot[i].x - x) < Math.abs(chart.plot[best].x - x)) best = i
    }
    setHoverIndex(best)
  }

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-60 w-full"
        role="img"
        aria-label="Token price in ANM over time"
        onMouseMove={(event) => onMove(event.clientX, event.currentTarget.getBoundingClientRect())}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <defs>
          <linearGradient id="tokenPriceGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(14 165 233)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="rgb(14 165 233)" stopOpacity="0.03" />
          </linearGradient>
        </defs>
        {chart.ticks.map((tick) => (
          <g key={tick.y}>
            <line
              x1={PADDING.left}
              y1={tick.y}
              x2={WIDTH - PADDING.right}
              y2={tick.y}
              className="stroke-gray-200 dark:stroke-night-700"
              strokeWidth="1"
            />
            <text x={PADDING.left - 8} y={tick.y + 4} textAnchor="end" className="fill-gray-500 text-[11px] dark:fill-slate-400">
              {formatAnmAmount(tick.value)}
            </text>
          </g>
        ))}
        <path d={chart.area} fill="url(#tokenPriceGradient)" />
        <path d={chart.path} fill="none" stroke="rgb(2 132 199)" strokeWidth="2.5" />
        {hovered && (
          <>
            <line x1={hovered.x} y1={PADDING.top} x2={hovered.x} y2={HEIGHT - PADDING.bottom} stroke="rgb(2 132 199 / 0.45)" strokeWidth="1" />
            <circle cx={hovered.x} cy={hovered.y} r={4} fill="rgb(2 132 199)" />
          </>
        )}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg border border-day-200 bg-white px-3 py-2 text-xs shadow-md dark:border-night-700 dark:bg-night-900"
          style={{
            left: `${Math.min(Math.max((hovered.x / WIDTH) * 100, 10), 90)}%`,
            top: '4%',
            transform: 'translateX(-50%)'
          }}
        >
          <div className="font-semibold text-gray-900 dark:text-slate-100">{formatAnmAmount(hovered.priceAnm)} ANM</div>
          <div className="mt-0.5 text-gray-600 dark:text-slate-400">{formatTimestamp(hovered.t)}</div>
        </div>
      )}
    </div>
  )
}
