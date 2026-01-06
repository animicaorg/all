import { useState } from 'react'

interface CopyButtonProps {
  value: string
  className?: string
}

export default function CopyButton({ value, className = '' }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`rounded-lg border border-day-300 bg-day-50 px-3 py-1.5 text-xs font-medium text-gray-700 transition-all hover:border-animica-500 hover:bg-animica-50 hover:text-animica-700 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:border-animica-500 dark:hover:bg-night-700 dark:hover:text-animica-400 ${className}`}
    >
      {copied ? (
        <span className="flex items-center gap-1">
          <svg className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
          Copied
        </span>
      ) : (
        <span>Copy</span>
      )}
    </button>
  )
}
