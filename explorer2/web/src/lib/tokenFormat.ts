import type { TokenInfo } from '@animica/explorer2-shared'

/** Whole-token supply from a base-unit decimal string (BigInt-safe). */
export function wholeSupply(baseUnits: string | null | undefined, decimals: number): number | null {
  if (!baseUnits) return null
  try {
    const value = BigInt(baseUnits)
    const scale = 10n ** BigInt(Math.max(0, decimals))
    const whole = value / scale
    const frac = scale > 1n ? Number(value % scale) / Number(scale) : 0
    return Number(whole) + frac
  } catch {
    return null
  }
}

/** Fully-diluted market cap in ANM (price × whole supply), or null. */
export function marketCapAnm(token: TokenInfo): number | null {
  if (token.priceAnm === null || token.priceAnm === undefined) return null
  const supply = wholeSupply(token.totalSupply, token.decimals)
  if (supply === null) return null
  return token.priceAnm * supply
}

/** Compact number: 1234567 → "1.23M". */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 1000) {
    return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(value)
  }
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value)
}

/** ANM amount with sensible precision for tiny prices. */
export function formatAnmAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (value === 0) return '0'
  const abs = Math.abs(value)
  if (abs >= 1000) return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value)
  if (abs >= 1) return value.toFixed(4).replace(/\.?0+$/, '')
  return value.toPrecision(4).replace(/\.?0+$/, '')
}

/** USD figure derived from an ANM amount and the ANM/USDT quote. */
export function formatUsdFromAnm(anmAmount: number | null | undefined, anmUsd: number | null): string {
  if (anmAmount === null || anmAmount === undefined || !Number.isFinite(anmAmount) || anmUsd === null) return '—'
  const usd = anmAmount * anmUsd
  if (usd === 0) return '$0'
  const abs = Math.abs(usd)
  if (abs >= 1000) return `$${new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(usd)}`
  if (abs >= 0.01) return `$${usd.toFixed(abs >= 1 ? 2 : 4)}`
  return `$${usd.toPrecision(3).replace(/\.?0+$/, '')}`
}

export function formatChangePct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

export function changeClass(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'text-gray-500 dark:text-slate-400'
  if (value > 0) return 'text-emerald-600 dark:text-emerald-400'
  if (value < 0) return 'text-rose-600 dark:text-rose-400'
  return 'text-gray-500 dark:text-slate-400'
}
