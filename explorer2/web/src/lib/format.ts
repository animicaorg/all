export function shorten(value: string, head = 8, tail = 6): string {
  if (!value) return ''
  if (value.length <= head + tail + 3) return value
  return `${value.slice(0, head)}…${value.slice(-tail)}`
}

export function formatNumber(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return new Intl.NumberFormat('en-US').format(value)
}

const timestampFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'numeric',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit'
})

export function formatTimestamp(seconds?: number | null): string {
  if (!seconds) return '—'
  const date = new Date(seconds * 1000)
  return timestampFormatter.format(date)
}

export function timeAgo(seconds?: number | null): string {
  if (!seconds) return '—'
  const diffMs = Math.max(0, Date.now() - seconds * 1000)
  const totalSeconds = Math.floor(diffMs / 1000)
  if (totalSeconds < 60) return `${totalSeconds}s ago`

  const mins = Math.floor(totalSeconds / 60)
  const secRemainder = totalSeconds % 60
  if (mins < 60) {
    return secRemainder > 0 ? `${mins}m ${secRemainder}s ago` : `${mins}m ago`
  }

  const hours = Math.floor(mins / 60)
  const minRemainder = mins % 60
  if (hours < 24) {
    return minRemainder > 0 ? `${hours}h ${minRemainder}m ago` : `${hours}h ago`
  }

  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function timeAgoNoSeconds(seconds?: number | null): string {
  if (!seconds) return '—'
  const diffMs = Math.max(0, Date.now() - seconds * 1000)
  const totalSeconds = Math.floor(diffMs / 1000)
  if (totalSeconds < 60) return 'just now'

  const mins = Math.floor(totalSeconds / 60)
  if (mins < 60) return `${mins}m ago`

  const hours = Math.floor(mins / 60)
  const minRemainder = mins % 60
  if (hours < 24) {
    return minRemainder > 0 ? `${hours}h ${minRemainder}m ago` : `${hours}h ago`
  }

  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function formatHashLink(hash?: string | null): string {
  if (!hash) return '—'
  return shorten(hash)
}

export function formatBalance(balance?: string | null): { 
  anm: string;      // Balance in ANM (user-friendly)
  nanm: string;     // Balance in nANM (raw native units)
  hex: string;      // Hexadecimal representation
} {
  if (!balance) return { anm: '—', nanm: '—', hex: '—' }
  
  try {
    // Handle hex format (e.g., "0x5") or plain number string
    let valueNanoAnm: bigint
    if (balance.startsWith('0x')) {
      valueNanoAnm = BigInt(balance)
    } else {
      valueNanoAnm = BigInt(balance)
    }
    
    // 1 ANM = 1,000,000,000 nANM (10^9)
    const NANO_PER_ANM = 1_000_000_000n
    
    // Convert nANM to ANM with decimal precision
    const anmWhole = valueNanoAnm / NANO_PER_ANM
    const anmRemainder = valueNanoAnm % NANO_PER_ANM
    
    // Format ANM with up to 9 decimal places (removing trailing zeros)
    let anmStr: string
    if (anmRemainder === 0n) {
      // No fractional part - format whole number only
      anmStr = anmWhole.toLocaleString('en-US')
    } else {
      // Construct decimal string manually for precision
      const remainderStr = anmRemainder.toString().padStart(9, '0')
      const trimmedRemainder = remainderStr.replace(/0+$/, '')
      // Format whole part using BigInt's toLocaleString to avoid precision loss
      // Safety: only add decimal point if there's a non-zero fractional part
      if (trimmedRemainder) {
        anmStr = anmWhole.toLocaleString('en-US') + '.' + trimmedRemainder
      } else {
        anmStr = anmWhole.toLocaleString('en-US')
      }
    }
    
    // Format nANM with thousand separators
    const nanmStr = new Intl.NumberFormat('en-US').format(valueNanoAnm)
    
    return {
      anm: anmStr,
      nanm: nanmStr,
      hex: balance.startsWith('0x') ? balance : `0x${valueNanoAnm.toString(16)}`
    }
  } catch (error) {
    // If parsing fails, return the original value
    return { anm: balance, nanm: balance, hex: balance }
  }
}
