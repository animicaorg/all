import { useState } from 'react'

interface JsonViewerProps {
  data: unknown
  label?: string
}

export default function JsonViewer({ data, label = 'Raw JSON' }: JsonViewerProps) {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-lg border border-night-800 bg-night-900">
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-semibold text-slate-200"
        onClick={() => setOpen((prev) => !prev)}
      >
        <span>{label}</span>
        <span className="text-xs text-slate-500">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (
        <pre className="max-h-96 overflow-auto border-t border-night-800 px-4 py-3 text-xs text-slate-300">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}
