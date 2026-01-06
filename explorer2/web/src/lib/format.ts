export function shorten(value: string, head = 8, tail = 6): string {
  if (!value) return ''
  if (value.length <= head + tail + 3) return value
  return `${value.slice(0, head)}…${value.slice(-tail)}`
}

export function formatNumber(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return new Intl.NumberFormat('en-US').format(value)
}

export function formatTimestamp(seconds?: number | null): string {
  if (!seconds) return '—'
  const date = new Date(seconds * 1000)
  return date.toLocaleString()
}

export function timeAgo(seconds?: number | null): string {
  if (!seconds) return '—'
  const diff = Date.now() - seconds * 1000
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function formatHashLink(hash?: string | null): string {
  if (!hash) return '—'
  return shorten(hash)
}

export function formatBalance(balance?: string | null): { decimal: string; hex: string } {
  if (!balance) return { decimal: '—', hex: '—' }
  
  try {
    // Handle hex format (e.g., "0x5") or plain number string
    let value: bigint
    if (balance.startsWith('0x')) {
      value = BigInt(balance)
    } else {
      value = BigInt(balance)
    }
    
    // Format decimal with thousand separators
    const decimal = new Intl.NumberFormat('en-US').format(value)
    
    return {
      decimal,
      hex: balance.startsWith('0x') ? balance : `0x${value.toString(16)}`
    }
  } catch (error) {
    // If parsing fails, return the original value
    return { decimal: balance, hex: balance }
  }
}
