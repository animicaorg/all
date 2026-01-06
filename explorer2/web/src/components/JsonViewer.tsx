import { useState } from 'react'

interface JsonViewerProps {
  data: unknown
  label?: string
}

export default function JsonViewer({ data, label = 'Raw JSON' }: JsonViewerProps) {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
      <button
        type="button"
        className="flex w-full items-center justify-between px-6 py-4 text-left text-sm font-semibold text-gray-900 transition-colors hover:bg-day-50 dark:text-slate-200 dark:hover:bg-night-800/50"
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className="flex items-center gap-2">
          <svg 
            className={`h-4 w-4 transition-transform ${open ? 'rotate-90' : ''}`} 
            fill="none" 
            stroke="currentColor" 
            strokeWidth={2} 
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          {label}
        </span>
        <span className="text-xs text-gray-500 dark:text-slate-500">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (
        <pre className="max-h-96 overflow-auto border-t border-day-200 bg-day-50 px-6 py-4 text-xs text-gray-800 dark:border-night-800 dark:bg-night-950 dark:text-slate-300">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}
