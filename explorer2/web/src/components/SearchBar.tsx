import { FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { parseSearch } from '../lib/search'

interface SearchBarProps {
  placeholder?: string
  className?: string
}

export default function SearchBar({ placeholder, className }: SearchBarProps) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const target = parseSearch(query)
    if (!target) return
    if (target.type === 'address') navigate(`/address/${target.value}`)
    if (target.type === 'block') navigate(`/block/${target.value}`)
    if (target.type === 'tx') navigate(`/tx/${target.value}`)
  }

  return (
    <form onSubmit={onSubmit} className={className}>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <svg
            className="absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-gray-400 dark:text-slate-500"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={placeholder ?? 'Search block, transaction, or address...'}
            className="w-full rounded-lg border border-day-300 bg-white py-2.5 pl-10 pr-4 text-sm text-gray-900 placeholder:text-gray-400 focus:border-animica-500 focus:outline-none focus:ring-2 focus:ring-animica-500/20 dark:border-night-700 dark:bg-night-900 dark:text-slate-100 dark:placeholder:text-slate-500"
          />
        </div>
        <button
          type="submit"
          className="rounded-lg bg-animica-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-animica-700 focus:outline-none focus:ring-2 focus:ring-animica-500/50 dark:bg-animica-500 dark:hover:bg-animica-600"
        >
          Search
        </button>
      </div>
    </form>
  )
}
