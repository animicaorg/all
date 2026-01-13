import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { RichListView } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatBalance, formatNumber, shorten } from '../lib/format'
import CopyButton from '../components/CopyButton'
import Skeleton from '../components/Skeleton'

export default function RichListPage() {
  const [data, setData] = useState<RichListView | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const limit = 100

  const load = async (newOffset: number) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.getRichList(limit, newOffset)
      setData(res)
      setOffset(newOffset)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(0)
  }, [])

  if (error) {
    return (
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-100">
        Failed to load rich list. {error}
      </div>
    )
  }

  const handlePrevPage = () => {
    if (offset >= limit) {
      load(offset - limit)
    }
  }

  const handleNextPage = () => {
    if (data?.hasMore) {
      load(offset + limit)
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">Rich List</h1>
        <p className="mt-2 text-sm text-gray-600 dark:text-slate-400">
          Top addresses by ANM balance
        </p>

        {data && (
          <div className="mt-6 grid gap-4 sm:grid-cols-2">
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">
                Total Supply
              </p>
              <p className="mt-2 font-mono text-lg font-semibold text-gray-900 dark:text-slate-200">
                {formatBalance(data.totalSupply).anm}{' '}
                <span className="text-base font-normal text-gray-600 dark:text-slate-400">ANM</span>
              </p>
              <p className="mt-1 font-mono text-xs text-gray-500 dark:text-slate-500">
                {formatBalance(data.totalSupply).nanm} nANM
              </p>
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">
                Total Accounts
              </p>
              <p className="mt-2 font-mono text-lg font-semibold text-gray-900 dark:text-slate-200">
                {formatNumber(data.totalAccounts)}
              </p>
              <p className="mt-1 text-xs text-gray-500 dark:text-slate-500">
                with non-zero balance
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="border-b border-day-200 px-6 py-4 dark:border-night-800">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">
            Top Addresses
            {data && (
              <span className="ml-2 text-sm font-normal text-gray-500 dark:text-slate-400">
                ({offset + 1} - {Math.min(offset + limit, data.totalAccounts)} of {formatNumber(data.totalAccounts)})
              </span>
            )}
          </h2>
        </div>

        {!data && loading ? (
          <div className="space-y-2 p-6">
            {Array.from({ length: 10 }).map((_, i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : data && data.entries.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-day-200 bg-day-50 dark:border-night-700 dark:bg-night-800">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-400">
                    Rank
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-400">
                    Address
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-400">
                    Balance (ANM)
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-400">
                    Percentage
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-day-200 dark:divide-night-800">
                {data.entries.map((entry, index) => (
                  <tr
                    key={entry.address}
                    className="hover:bg-day-50 dark:hover:bg-night-800/50"
                  >
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-slate-200">
                      #{offset + index + 1}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/address/${entry.address}`}
                          className="font-mono text-sm text-animica-600 hover:underline dark:text-animica-400"
                        >
                          {shorten(entry.address, 12, 8)}
                        </Link>
                        <CopyButton value={entry.address} />
                      </div>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <p className="font-mono text-sm font-medium text-gray-900 dark:text-slate-200">
                        {formatBalance(entry.balance).anm}
                      </p>
                      <p className="font-mono text-xs text-gray-500 dark:text-slate-500">
                        {formatBalance(entry.balance).nanm} nANM
                      </p>
                    </td>
                    <td className="px-6 py-4 text-right font-mono text-sm text-gray-700 dark:text-slate-300">
                      {(entry.percentage ?? 0).toFixed(4)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="px-6 py-8 text-center text-sm text-gray-500 dark:text-slate-400">
            No addresses found.
          </div>
        )}

        {data && (data.entries.length > 0 || offset > 0) && (
          <div className="flex items-center justify-between border-t border-day-200 px-6 py-4 dark:border-night-800">
            <button
              type="button"
              onClick={handlePrevPage}
              disabled={offset === 0 || loading}
              className="rounded-lg border border-day-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-day-50 disabled:opacity-40 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:bg-night-700"
            >
              Previous
            </button>
            <span className="text-sm text-gray-600 dark:text-slate-400">
              Page {Math.floor(offset / limit) + 1}
            </span>
            <button
              type="button"
              onClick={handleNextPage}
              disabled={!data.hasMore || loading}
              className="rounded-lg border border-day-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-day-50 disabled:opacity-40 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:bg-night-700"
            >
              {loading ? 'Loading...' : 'Next'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
