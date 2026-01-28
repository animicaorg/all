import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { RichListEntry, RichListResponse, RichListSummary } from '@animica/explorer2-shared'
import ErrorDisplay from '../components/ErrorDisplay'
import Skeleton from '../components/Skeleton'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8081'

interface RichListState {
  data: RichListResponse | null
  summary: RichListSummary | null
  loading: boolean
  error: string | null
}

export function RichListPage() {
  const [state, setState] = useState<RichListState>({
    data: null,
    summary: null,
    loading: true,
    error: null
  })
  const [limit] = useState(100)
  const [offset, setOffset] = useState(0)

  useEffect(() => {
    let cancelled = false

    const fetchData = async () => {
      try {
        setState(prev => ({ ...prev, loading: true, error: null }))

        // Fetch rich list and summary in parallel
        const [listRes, summaryRes] = await Promise.all([
          fetch(`${API_BASE}/api/richlist?limit=${limit}&offset=${offset}`),
          offset === 0 ? fetch(`${API_BASE}/api/richlist/summary`) : Promise.resolve(null)
        ])

        if (cancelled) return

        if (!listRes.ok) {
          const errorData = await listRes.json().catch(() => ({ message: 'Failed to fetch rich list' }))
          throw new Error(errorData.message || 'Failed to fetch rich list')
        }

        const listData: RichListResponse = await listRes.json()
        let summaryData: RichListSummary | null = null

        if (summaryRes && summaryRes.ok) {
          summaryData = await summaryRes.json()
        } else if (offset === 0) {
          // If summary fails but list succeeds, use list data
          summaryData = state.summary
        }

        if (cancelled) return

        setState({
          data: listData,
          summary: summaryData ?? state.summary,
          loading: false,
          error: null
        })
      } catch (err) {
        if (cancelled) return
        setState(prev => ({
          ...prev,
          loading: false,
          error: err instanceof Error ? err.message : 'Unknown error occurred'
        }))
      }
    }

    fetchData()

    return () => {
      cancelled = true
    }
  }, [limit, offset])

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

  if (state.error) {
    return (
      <div className="container mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold mb-6">Rich List</h1>
        <ErrorDisplay error={state.error} />
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
          <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
            <div className="text-sm text-gray-600 dark:text-gray-400 mb-1">Total Supply</div>
            <div className="text-2xl font-bold">{formatSupply(state.summary.totalSupply)} ANM</div>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
            <div className="text-sm text-gray-600 dark:text-gray-400 mb-1">Total Addresses</div>
            <div className="text-2xl font-bold">{state.summary.addressCount.toLocaleString()}</div>
          </div>
          {state.summary.top10Pct !== undefined && (
            <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-1">Top 10 Hold</div>
              <div className="text-2xl font-bold">{state.summary.top10Pct.toFixed(2)}%</div>
            </div>
          )}
          {state.summary.top100Pct !== undefined && (
            <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-1">Top 100 Hold</div>
              <div className="text-2xl font-bold">{state.summary.top100Pct.toFixed(2)}%</div>
            </div>
          )}
        </div>
      )}

      {/* Rich List Table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Rank
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Address
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Balance (ANM)
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
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
                  <tr key={entry.rank} className="hover:bg-gray-50 dark:hover:bg-gray-900/50">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                      #{entry.rank}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      <Link 
                        to={`/address/${entry.address}`}
                        className="text-blue-600 dark:text-blue-400 hover:underline font-mono"
                      >
                        {entry.address.slice(0, 12)}...{entry.address.slice(-8)}
                      </Link>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-right font-mono">
                      {formatBalance(entry.balance)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-right">
                      {entry.pctSupply !== undefined ? entry.pctSupply.toFixed(4) : '0.00'}%
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                    No addresses found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {state.data && state.data.items.length > 0 && (
          <div className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <div className="text-sm text-gray-600 dark:text-gray-400">
              Showing {offset + 1} - {offset + state.data.items.length} of {state.data.totalAddresses.toLocaleString()}
            </div>
            <div className="flex gap-2">
              <button
                onClick={handlePrevPage}
                disabled={!hasPrevPage || state.loading}
                className="px-4 py-2 text-sm font-medium rounded-md border border-gray-300 dark:border-gray-600 
                         bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300
                         hover:bg-gray-50 dark:hover:bg-gray-700 
                         disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <button
                onClick={handleNextPage}
                disabled={!hasNextPage || state.loading}
                className="px-4 py-2 text-sm font-medium rounded-md border border-gray-300 dark:border-gray-600 
                         bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300
                         hover:bg-gray-50 dark:hover:bg-gray-700 
                         disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Info Box */}
      <div className="mt-6 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
        <h3 className="text-sm font-medium text-blue-900 dark:text-blue-300 mb-2">About Rich List</h3>
        <p className="text-sm text-blue-800 dark:text-blue-400">
          The Rich List shows addresses ranked by their ANM balance at the current indexed height. 
          Balances are computed from the canonical chain state and updated with each new block.
          Only addresses with non-zero balances are included.
        </p>
      </div>
    </div>
  )
}
