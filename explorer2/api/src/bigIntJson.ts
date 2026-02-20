/**
 * BigInt-safe JSON serialization utilities.
 * Standard JSON.stringify throws on BigInt values; these helpers convert them to strings.
 */

/**
 * JSON replacer that converts BigInt values to decimal strings.
 * Never throws on BigInt — outputs "123" (no quotes in JSON output since replacer returns string).
 */
export function bigIntReplacer(_key: string, value: unknown): unknown {
  if (typeof value === 'bigint') {
    return value.toString()
  }
  return value
}

/**
 * Stringify a value safely:
 * - BigInt → decimal string
 * - undefined → omitted (standard JSON behaviour)
 * - Circular references → "[Circular]"
 */
export function bigIntSafeStringify(value: unknown, space?: number): string {
  const seen = new WeakSet()
  return JSON.stringify(value, (key, val) => {
    if (typeof val === 'bigint') return val.toString()
    if (typeof val === 'object' && val !== null) {
      if (seen.has(val)) return '[Circular]'
      seen.add(val)
    }
    return val
  }, space)
}

/**
 * Parse JSON that may contain large integers represented as strings.
 * Returns the parsed value. Does NOT auto-convert strings to BigInt — caller
 * should use parseHexQuantity / parseDecimalQuantity when needed.
 */
export function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/**
 * Parse a hex quantity (0x-prefixed) to BigInt.
 * Returns 0n if the input is null/undefined/invalid.
 */
export function parseHexQuantity(value: string | null | undefined): bigint {
  if (!value || value === '0x' || value === '0X') return 0n
  try {
    const s = value.startsWith('0x') || value.startsWith('0X') ? value : `0x${value}`
    return BigInt(s)
  } catch {
    return 0n
  }
}

/**
 * Parse a decimal quantity (string or number) to BigInt.
 * Returns 0n if the input is null/undefined/invalid.
 */
export function parseDecimalQuantity(value: string | number | null | undefined): bigint {
  if (value === null || value === undefined || value === '') return 0n
  try {
    return BigInt(value)
  } catch {
    return 0n
  }
}

/**
 * Format a BigInt as a human-readable decimal with thousand separators.
 * e.g. 1000000n → "1,000,000"
 */
export function formatBigInt(value: bigint): string {
  return value.toLocaleString('en-US')
}
