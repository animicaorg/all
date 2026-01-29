import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { RichListEntry, RichListResponse, RichListSummary } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import ErrorDisplay from '../components/ErrorDisplay'
import Skeleton from '../components/Skeleton'

interface RichListState {
  data: RichListResponse | null
  summary: RichListSummary | null
  loading: boolean
  error: string | null
  retryTrigger: number
}

export function RichListPage() {
  const [state, setState] = useState<RichListState>({
    data: null,
    summary: null,
    loading: true,
    error: null,
    retryTrigger: 0
  })
  const [limit] = useState(100)
  const [offset, setOffset] = useState(0)

  useEffect(() => {
    let cancelled = false

    const fetchData = async () => {
      try {
        setState(prev => ({ ...prev, loading: true, error: null }))

        // Fetch rich list and summary in parallel
        const [listData, summaryData] = await Promise.all([
          api.getRichList(limit, offset),
          offset === 0 ? api.getRichListSummary() : Promise.resolve(null)
        ])

        if (cancelled) return

        setState(prev => ({
          data: listData,
          summary: summaryData ?? prev.summary,
          loading: false,
          error: null,
          retryTrigger: prev.retryTrigger
        }))
      } catch (err) {
        if (cancelled) return
        const errorMessage = err instanceof Error ? err.message : 'Unknown error occurred'
        setState(prev => ({
          ...prev,
          loading: false,
          error: errorMessage
        }))
      }
    }

    fetchData()

    return () => {
      cancelled = true
    }
  }, [limit, offset, state.retryTrigger])

  const formatBalance = (hexBalance: string): string => {
    try {
      const balance = BigInt(hexBalance)
      // Convert from nANM (10^-9 ANM) to ANM
      const anm = Number(balance) / 1e9
      return anm.toLocaleString('en-US', { 
        minimumFractionDigits: 2,
        maximumFractionDigits: 9 
      })
    } catch {
      return '0.00'
    }
  }

  const formatSupply = (hexSupply: string): string => {
    try {
      const supply = BigInt(hexSupply)
      const anm = Number(supply) / 1e9
      return anm.toLocaleString('en-US', { 
        minimumFractionDigits: 0,
        maximumFractionDigits: 0 
      })
    } catch {
      return '0'
    }
  }

  const handlePrevPage = () => {
    if (offset > 0) {
      setOffset(Math.max(0, offset - limit))
    }
  }

  const handleNextPage = () => {
    if (state.data && state.data.nextOffset !== undefined) {
      setOffset(state.data.nextOffset)
    }
  }

  const handleRetry = () => {
    // Reset to first page and trigger a refetch
    setOffset(0)
    setState(prev => ({ 
      ...prev, 
      error: null, 
      loading: true,
      retryTrigger: prev.retryTrigger + 1 
    }))
  }

  if (state.error) {
    return (
      <div className="container mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold mb-6">Rich List</h1>
        <ErrorDisplay error={state.error} onRetry={handleRetry} />
      </div>
    )
  }

  const currentPage = Math.floor(offset / limit) + 1
  const hasNextPage = state.data?.nextOffset !== undefined
  const hasPrevPage = offset > 0

  return (
    <div className="container mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">Rich List</h1>
        {state.data && (
          <div className="text-sm text-gray-600 dark:text-gray-400">
            Height: {state.data.height.toLocaleString()}
          </div>
        )}
      </div>

      {/* Summary Cards */}
      {state.summary && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <div className="bg-gradient-to-br from-blue-50 to-blue-100 dark:from-blue-900/30 dark:to-blue-800/30 rounded-lg p-6 border border-blue-200 dark:border-blue-700 shadow-sm">
            <div className="flex items-center justify-between mb-2">
              <div className="text-sm font-medium text-blue-600 dark:text-blue-400">Total Supply</div>
              <svg className="w-5 h-5 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div className="text-2xl font-bold text-blue-900 dark:text-blue-100">{formatSupply(state.summary.totalSupply)}</div>
            <div className="text-xs text-blue-700 dark:text-blue-300 mt-1">ANM</div>
          </div>
          <div className="bg-gradient-to-br from-green-50 to-green-100 dark:from-green-900/30 dark:to-green-800/30 rounded-lg p-6 border border-green-200 dark:border-green-700 shadow-sm">
            <div className="flex items-center justify-between mb-2">
              <div className="text-sm font-medium text-green-600 dark:text-green-400">Total Addresses</div>
              <svg className="w-5 h-5 text-green-600 dark:text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
            </div>
            <div className="text-2xl font-bold text-green-900 dark:text-green-100">{state.summary.addressCount.toLocaleString()}</div>
            <div className="text-xs text-green-700 dark:text-green-300 mt-1">Holders</div>
          </div>
          {state.summary.top10Pct !== undefined && (
            <div className="bg-gradient-to-br from-purple-50 to-purple-100 dark:from-purple-900/30 dark:to-purple-800/30 rounded-lg p-6 border border-purple-200 dark:border-purple-700 shadow-sm">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm font-medium text-purple-600 dark:text-purple-400">Top 10 Hold</div>
                <svg className="w-5 h-5 text-purple-600 dark:text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
              </div>
              <div className="text-2xl font-bold text-purple-900 dark:text-purple-100">{state.summary.top10Pct.toFixed(2)}%</div>
              <div className="text-xs text-purple-700 dark:text-purple-300 mt-1">of supply</div>
            </div>
          )}
          {state.summary.top100Pct !== undefined && (
            <div className="bg-gradient-to-br from-amber-50 to-amber-100 dark:from-amber-900/30 dark:to-amber-800/30 rounded-lg p-6 border border-amber-200 dark:border-amber-700 shadow-sm">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm font-medium text-amber-600 dark:text-amber-400">Top 100 Hold</div>
                <svg className="w-5 h-5 text-amber-600 dark:text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                </svg>
              </div>
              <div className="text-2xl font-bold text-amber-900 dark:text-amber-100">{state.summary.top100Pct.toFixed(2)}%</div>
              <div className="text-xs text-amber-700 dark:text-amber-300 mt-1">of supply</div>
            </div>
          )}
        </div>
      )}

      {/* Rich List Table */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-lg">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gradient-to-r from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-800 border-b-2 border-gray-200 dark:border-gray-700">
              <tr>
                <th className="px-6 py-4 text-left text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                  Rank
                </th>
                <th className="px-6 py-4 text-left text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                  Address
                </th>
                <th className="px-6 py-4 text-right text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                  Balance (ANM)
                </th>
                <th className="px-6 py-4 text-right text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                  % of Supply
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {state.loading ? (
                Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i}>
                    <td className="px-6 py-4"><Skeleton className="h-5 w-8" /></td>
                    <td className="px-6 py-4"><Skeleton className="h-5 w-64" /></td>
                    <td className="px-6 py-4 text-right"><Skeleton className="h-5 w-32" /></td>
                    <td className="px-6 py-4 text-right"><Skeleton className="h-5 w-16" /></td>
                  </tr>
                ))
              ) : state.data && state.data.items.length > 0 ? (
                state.data.items.map((entry: RichListEntry) => (
                  <tr 
                    key={entry.rank} 
                    className="hover:bg-gray-50 dark:hover:bg-gray-900/50 transition-colors"
                  >
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        {entry.rank <= 3 ? (
                          <span className={`inline-flex items-center justify-center w-7 h-7 rounded-full text-sm font-bold ${
                            entry.rank === 1 ? 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400' :
                            entry.rank === 2 ? 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300' :
                            'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400'
                          }`}>
                            {entry.rank}
                          </span>
                        ) : (
                          <span className="text-sm font-medium text-gray-900 dark:text-gray-100">#{entry.rank}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Link 
                        to={`/address/${entry.address}`}
                        className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 hover:underline font-mono text-sm font-medium"
                      >
                        {entry.address.slice(0, 12)}...{entry.address.slice(-8)}
                      </Link>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <span className="text-sm font-semibold text-gray-900 dark:text-gray-100 font-mono">
                        {formatBalance(entry.balance)}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300">
                        {entry.pctSupply !== undefined ? entry.pctSupply.toFixed(4) : '0.00'}%
                      </span>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="px-6 py-12 text-center">
                    <div className="flex flex-col items-center gap-3">
                      <svg className="w-12 h-12 text-gray-400 dark:text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
                      </svg>
                      <p className="text-gray-500 dark:text-gray-400">No addresses found</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {state.data && state.data.items.length > 0 && (
          <div className="px-6 py-4 bg-gray-50 dark:bg-gray-900/50 border-t-2 border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <div className="text-sm text-gray-700 dark:text-gray-300 font-medium">
              Showing <span className="font-semibold text-gray-900 dark:text-white">{offset + 1}</span> - <span className="font-semibold text-gray-900 dark:text-white">{offset + state.data.items.length}</span> of <span className="font-semibold text-gray-900 dark:text-white">{state.data.totalAddresses.toLocaleString()}</span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handlePrevPage}
                disabled={!hasPrevPage || state.loading}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 
                         bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300
                         hover:bg-gray-50 dark:hover:bg-gray-700 
                         disabled:opacity-50 disabled:cursor-not-allowed
                         focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900
                         transition-colors shadow-sm"
              >
                <div className="flex items-center gap-1">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                  </svg>
                  Previous
                </div>
              </button>
              <button
                onClick={handleNextPage}
                disabled={!hasNextPage || state.loading}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 
                         bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300
                         hover:bg-gray-50 dark:hover:bg-gray-700 
                         disabled:opacity-50 disabled:cursor-not-allowed
                         focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900
                         transition-colors shadow-sm"
              >
                <div className="flex items-center gap-1">
                  Next
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </div>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Info Box */}
      <div className="mt-6 p-6 bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 border border-blue-200 dark:border-blue-800 rounded-xl shadow-sm">
        <div className="flex items-start gap-3">
          <svg className="w-6 h-6 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div className="flex-1">
            <h3 className="text-sm font-semibold text-blue-900 dark:text-blue-100 mb-2">About Rich List</h3>
            <p className="text-sm text-blue-800 dark:text-blue-300 leading-relaxed">
              The Rich List shows addresses ranked by their ANM balance at the current indexed height. 
              Balances are computed from the canonical chain state and updated with each new block.
              Only addresses with non-zero balances are included.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
