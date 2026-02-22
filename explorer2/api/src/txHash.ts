import { HttpError } from './errors.js'

const TX_HASH_HEX = /^[0-9a-fA-F]+$/

export function normalizeTxHash(hash: string): string {
  const trimmed = String(hash || '').trim()
  const noPrefix = trimmed.replace(/^0x/i, '')

  if (!noPrefix || !TX_HASH_HEX.test(noPrefix) || noPrefix.length !== 64) {
    throw new HttpError(400, 'Invalid transaction hash', 'Expected 32-byte hex hash (64 hex chars), with or without 0x prefix')
  }

  return `0x${noPrefix.toLowerCase()}`
}
