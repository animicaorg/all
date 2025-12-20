import { useState } from 'react'

interface CopyButtonProps {
  value: string
}

export default function CopyButton({ value }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="rounded-md border border-night-700 px-2 py-1 text-xs text-slate-300 hover:border-animica-500 hover:text-animica-400"
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}
