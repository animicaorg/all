export interface PageOptions {
  limit: number
  cursor?: string
}

export function clampLimit(limit: number, max = 50): number {
  if (!Number.isFinite(limit) || limit <= 0) return 20
  return Math.min(Math.floor(limit), max)
}

export function parseCursor(cursor?: string | null): number | undefined {
  if (!cursor) return undefined
  const n = Number.parseInt(cursor, 10)
  return Number.isNaN(n) ? undefined : n
}

export function nextCursorForHeight(minHeight: number): string | null {
  const next = minHeight - 1
  return next >= 0 ? String(next) : null
}
