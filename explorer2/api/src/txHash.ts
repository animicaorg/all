export function normalizeTxHash(hash: string): string {
  const trimmed = String(hash || '').trim()
  const noPrefix = trimmed.replace(/^0x/i, '').toLowerCase()
  return `0x${noPrefix}`
}

