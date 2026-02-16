type Entry = { count: number; resetAt: number };

const buckets = new Map<string, Entry>();

const WINDOW_MS = 60_000;
const MAX_PER_WINDOW = 60;

export function checkProxyRateLimit(key: string): { allowed: boolean; remaining: number; retryAfterMs: number } {
  const now = Date.now();
  const current = buckets.get(key);

  if (!current || current.resetAt < now) {
    buckets.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return { allowed: true, remaining: MAX_PER_WINDOW - 1, retryAfterMs: 0 };
  }

  current.count += 1;
  const remaining = Math.max(0, MAX_PER_WINDOW - current.count);
  const allowed = current.count <= MAX_PER_WINDOW;
  return { allowed, remaining, retryAfterMs: Math.max(0, current.resetAt - now) };
}
