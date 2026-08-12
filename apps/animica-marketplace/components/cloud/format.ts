// Animica Python Cloud — pure formatters, importable from BOTH graphs.
//
// These lived in `components/cloud/ui.tsx`, which carries `'use client'`. In the
// App Router every export of a client module becomes a *client reference* in the
// server graph, so a server component that imported `timeAgo` from there got a
// reference object rather than a function and crashed on the call:
//
//     TypeError: (0 , c.Sy) is not a function
//       at .next/server/app/cloud/functions/page.js
//
// which Next renders as a bare "Application error". `/cloud`, `/cloud/earnings`
// and `/cloud/functions` all did it, and `/cloud` is where wallet sign-in lands —
// so connecting a wallet looked like it broke the site.
//
// Nothing here touches the DOM, React or a hook, so there is no reason for any of
// it to be client-only. `ui.tsx` re-exports these so existing client imports keep
// working unchanged.

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (!isFinite(t)) return '—';
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || !isFinite(Number(ms))) return '—';
  const n = Number(ms);
  if (n < 1000) return `${Math.round(n)}ms`;
  return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}s`;
}

export function fmtInt(n: number | string | null | undefined): string {
  if (n == null) return '—';
  const v = typeof n === 'string' ? Number(n) : n;
  if (!isFinite(v)) return '—';
  return v.toLocaleString();
}

export function usd(cents: number): string {
  const v = Number(cents || 0) / 100;
  return v % 1 === 0 ? String(v) : v.toFixed(2);
}
