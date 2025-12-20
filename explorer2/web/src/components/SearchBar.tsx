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
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={placeholder ?? 'Search by block height, hash, tx hash, or address'}
          className="w-full rounded-lg border border-night-700 bg-night-900 px-4 py-3 text-sm text-slate-100 placeholder:text-slate-500 focus:border-animica-500 focus:outline-none"
        />
        <button
          type="submit"
          className="rounded-lg bg-animica-500 px-4 py-3 text-sm font-semibold text-night-950 hover:bg-animica-400"
        >
          Search
        </button>
      </div>
    </form>
  )
}
