// Pure formatting helpers for the PUBLIC Python Cloud surface (apps / functions / developers).
//
// Deliberately free of any import that touches process.env (lib/config.ts, lib/nanm.ts) so the
// SAME module is usable from server components AND 'use client' components — see app/dev/ui.tsx
// for why the client side must never pull lib/config transitively.

export const NANM_PER_ANM = 1_000_000_000n;

/** Integer-exact nANM -> human ANM with thousands grouping, up to 4 decimals. */
export function fmtAnm(nanm: string | number | bigint): string {
  try {
    const n = BigInt(nanm);
    const neg = n < 0n;
    const abs = neg ? -n : n;
    const whole = abs / NANM_PER_ANM;
    const frac = abs % NANM_PER_ANM;
    const fs = frac.toString().padStart(9, '0').replace(/0+$/, '').slice(0, 4);
    const grouped = whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-' : '') + (fs ? `${grouped}.${fs}` : grouped);
  } catch {
    return String(nanm);
  }
}

/** Small nANM unit price (e.g. per CPU-ms) -> full-precision ANM decimal, no truncation. */
export function fmtAnmExact(nanm: string | number | bigint): string {
  try {
    const n = BigInt(nanm);
    const neg = n < 0n;
    const abs = neg ? -n : n;
    const whole = abs / NANM_PER_ANM;
    const frac = (abs % NANM_PER_ANM).toString().padStart(9, '0').replace(/0+$/, '');
    return (neg ? '-' : '') + (frac ? `${whole}.${frac}` : `${whole}`);
  } catch {
    return String(nanm);
  }
}

/** 1234 -> "1.2k", 2_500_000 -> "2.5M". */
export function compact(n: number): string {
  if (!Number.isFinite(n)) return '0';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1).replace(/\.0$/, '')}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(abs >= 10_000 ? 0 : 1).replace(/\.0$/, '')}k`;
  return String(Math.round(n));
}

export function shortAddr(a: string | null | undefined): string {
  if (!a) return '—';
  return a.length > 22 ? `${a.slice(0, 12)}…${a.slice(-6)}` : a;
}

export function shortHash(h: string | null | undefined, keep = 18): string {
  if (!h) return '—';
  return h.length > keep ? `${h.slice(0, keep)}…` : h;
}

export function fmtDate(d: string | Date | null | undefined): string {
  if (!d) return '—';
  try {
    return new Date(d).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return String(d);
  }
}

// ── Cloud catalog vocabulary (mirrors prisma CloudCategory / CloudPricingModel) ─────────────

export const CLOUD_CATEGORIES: { value: string; label: string; icon: string }[] = [
  { value: 'AI', label: 'AI', icon: '🧠' },
  { value: 'AGENTS', label: 'Agents', icon: '🤖' },
  { value: 'DEVELOPER_TOOLS', label: 'Developer Tools', icon: '🛠️' },
  { value: 'AUTOMATION', label: 'Automation', icon: '⚙️' },
  { value: 'DATA', label: 'Data', icon: '📊' },
  { value: 'GAMES', label: 'Games', icon: '🎮' },
  { value: 'PRODUCTIVITY', label: 'Productivity', icon: '📝' },
  { value: 'BLOCKCHAIN', label: 'Blockchain', icon: '⛓️' },
  { value: 'UTILITIES', label: 'Utilities', icon: '🧰' },
  { value: 'APIS', label: 'APIs', icon: '🔌' },
];

export function categoryLabel(v: string | null | undefined): string {
  return CLOUD_CATEGORIES.find((c) => c.value === v)?.label ?? 'Utilities';
}

/** Human price label for a CloudApp's CURRENT terms. */
export function priceLabel(pricingModel: string, priceNanm: string | bigint): { text: string; sub?: string } {
  switch (pricingModel) {
    case 'FREE':
      return { text: 'Free' };
    case 'ONE_TIME':
      return { text: fmtAnm(priceNanm), sub: 'ANM once' };
    case 'SUBSCRIPTION':
      return { text: fmtAnm(priceNanm), sub: 'ANM/mo' };
    default:
      return { text: 'Pay per use' };
  }
}
