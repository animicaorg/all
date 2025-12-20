export type SearchTarget =
  | { type: 'block'; value: string }
  | { type: 'tx'; value: string }
  | { type: 'address'; value: string }

export function parseSearch(input: string): SearchTarget | null {
  const value = input.trim()
  if (!value) return null
  if (/^anim1/i.test(value)) return { type: 'address', value }
  if (/^[0-9]+$/.test(value)) return { type: 'block', value }
  if (/^0x[a-fA-F0-9]+$/.test(value)) return { type: 'tx', value }
  return null
}
