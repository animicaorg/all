'use client';
import { createContext, useContext } from 'react';

// Shared client-side helpers for the Developer Center (/dev). Kept free of any server-only
// imports (lib/config reads process.env, which throws in the browser — see the admin console),
// so money/byte formatting is re-implemented locally.

// ── formatting (local; nANM is the base unit) ────────────────────────────────
export const NANM_PER_ANM = 1_000_000_000n;

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
  } catch { return String(nanm); }
}

// Parse a human ANM decimal string into an integer nANM string. Throws on malformed input.
export function anmToNanm(anm: string): string {
  const s = String(anm).trim();
  if (!/^\d+(\.\d+)?$/.test(s)) throw new Error(`invalid ANM amount: ${anm}`);
  const [whole, frac = ''] = s.split('.');
  const fracPadded = (frac + '000000000').slice(0, 9);
  return (BigInt(whole) * NANM_PER_ANM + BigInt(fracPadded || '0')).toString();
}

// Inverse of fmtAnm without grouping — for pre-filling an editable input from nANM.
export function nanmToAnmInput(nanm: string | number | bigint): string {
  try {
    const n = BigInt(nanm);
    const whole = n / NANM_PER_ANM;
    const frac = (n % NANM_PER_ANM).toString().padStart(9, '0').replace(/0+$/, '');
    return frac ? `${whole}.${frac}` : `${whole}`;
  } catch { return '0'; }
}

export function fmtBytes(b: string | number | bigint): string {
  let n = Number(b);
  if (!isFinite(n)) return `${b} B`;
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i === 0 ? n : n.toFixed(1)} ${u[i]}`;
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  try { return new Date(s).toLocaleString(); } catch { return String(s); }
}

export function shortHash(h: string | null | undefined, keep = 20): string {
  if (!h) return '—';
  return h.length > keep ? `${h.slice(0, keep)}…` : h;
}

export function shortAddr(a: string | null | undefined): string {
  if (!a) return '—';
  return a.length > 22 ? `${a.slice(0, 12)}…${a.slice(-6)}` : a;
}

// ── store constants (mirror lib/appStore.ts + lib/config.ts, client-safe copies) ──
export const APP_CATEGORIES: { value: string; label: string }[] = [
  { value: 'GAMES', label: 'Games' },
  { value: 'AI_AGENTS', label: 'AI Agents' },
  { value: 'TOOLS', label: 'Tools' },
  { value: 'DEV_APPS', label: 'Dev Apps' },
  { value: 'MOBILE', label: 'Mobile' },
  { value: 'WEB', label: 'Web' },
];

// Permissions that force a build into manual review even for a pinned signer. Mirrors
// SENSITIVE_PERMISSIONS in lib/apkVerify.ts — used ONLY to highlight in the UI; the server is
// the source of truth for the actual review decision.
export const SENSITIVE_PERMISSIONS = new Set([
  'android.permission.SEND_SMS', 'android.permission.RECEIVE_SMS', 'android.permission.READ_SMS',
  'android.permission.RECEIVE_MMS', 'android.permission.RECEIVE_WAP_PUSH',
  'android.permission.BIND_ACCESSIBILITY_SERVICE', 'android.permission.BIND_DEVICE_ADMIN',
  'android.permission.SYSTEM_ALERT_WINDOW', 'android.permission.REQUEST_INSTALL_PACKAGES',
  'android.permission.READ_CALL_LOG', 'android.permission.WRITE_CALL_LOG',
  'android.permission.PROCESS_OUTGOING_CALLS', 'android.permission.QUERY_ALL_PACKAGES',
  'android.permission.MANAGE_EXTERNAL_STORAGE', 'android.permission.ACCESS_BACKGROUND_LOCATION',
]);

// ── fetch helpers ────────────────────────────────────────────────────────────
export interface ApiError extends Error { status?: number }

export async function api(path: string, init?: RequestInit): Promise<any> {
  const r = await fetch(path, { credentials: 'include', ...init });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(data?.error?.message || `${r.status} ${r.statusText}`) as ApiError;
    e.status = r.status;
    throw e;
  }
  return data;
}

// Raw-body upload (APK builds / store imagery). content-type matters for the assets route
// (mime validation) but is ignored by the builds route, so we always send the file's own type.
export async function uploadRaw(path: string, file: File, headers: Record<string, string> = {}): Promise<any> {
  const r = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': file.type || 'application/octet-stream', ...headers },
    body: file,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(data?.error?.message || `${r.status} ${r.statusText}`) as ApiError;
    e.status = r.status;
    throw e;
  }
  return data;
}

// ── status chip ──────────────────────────────────────────────────────────────
const STATUS_COLOR: Record<string, string> = {
  DRAFT: 'var(--text-faint)', PUBLISHED: 'var(--good)', DELISTED: 'var(--text-faint)',
  UPLOADED: 'var(--accent-2)', PENDING_REVIEW: 'var(--warn)', APPROVED: 'var(--good)',
  CHECKS_FAILED: 'var(--bad)', REJECTED: 'var(--bad)',
  PUBLIC: 'var(--good)', UNLISTED: 'var(--warn)', PRIVATE: 'var(--text-faint)',
  ACTIVE: 'var(--good)', REFUNDED: 'var(--text-faint)', EXPIRED: 'var(--text-faint)',
  REQUESTED: 'var(--warn)', SENT: 'var(--accent-2)', CONFIRMED: 'var(--good)', FAILED: 'var(--bad)',
};

export function StatusChip({ status, title }: { status?: string | null; title?: string }) {
  if (!status) return null;
  const c = STATUS_COLOR[status] ?? 'var(--text-dim)';
  return (
    <span className="pill" title={title} style={{ color: c, borderColor: c, fontWeight: 600, fontSize: 11 }}>
      {status.replace(/_/g, ' ').toLowerCase()}
    </span>
  );
}

// ── shared inline styles ─────────────────────────────────────────────────────
export const inputStyle: React.CSSProperties = {
  width: '100%', background: 'var(--bg-elev)', border: '1px solid var(--border-bright)',
  borderRadius: 8, color: 'var(--text)', padding: '9px 11px', fontSize: 13.5, outline: 'none',
  fontFamily: 'inherit',
};
export const labelStyle: React.CSSProperties = { color: 'var(--text-dim)', fontSize: 12.5, marginBottom: 5, display: 'block' };
export const rowLabel: React.CSSProperties = { color: 'var(--text-faint)', fontSize: 12, minWidth: 110, flexShrink: 0 };

export function Msg({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null;
  return <div style={{ marginTop: 10, fontSize: 13, color: msg.ok ? 'var(--good)' : 'var(--bad)' }}>{msg.text}</div>;
}

// ── dev session context (provided by DevGate) ────────────────────────────────
export interface DevSession { address: string | null; reload: () => void; logout: () => Promise<void>; }
const DevSessionCtx = createContext<DevSession | null>(null);
export const DevSessionProvider = DevSessionCtx.Provider;
export function useDevSession(): DevSession {
  const c = useContext(DevSessionCtx);
  if (!c) throw new Error('useDevSession must be used inside the Developer Center');
  return c;
}
