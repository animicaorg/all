'use client';
import { useCallback, useEffect, useState } from 'react';

// Shared client helpers for the Python Cloud admin consoles (/admin/cloud and
// /admin/profitability). Client-safe: NO imports from lib/config or lib/nanm (they read
// process.env and throw in the browser) — money formatting is implemented locally, same as
// app/dev/ui.tsx does.

export const NANM_PER_ANM = 1_000_000_000n;

/** Full-precision ANM rendering (up to 9 decimals, trimmed) — finance amounts are tiny. */
export function fmtAnmFull(nanm: string | number | bigint | null | undefined): string {
  if (nanm == null) return '—';
  try {
    const n = BigInt(nanm);
    const neg = n < 0n;
    const abs = neg ? -n : n;
    const whole = abs / NANM_PER_ANM;
    const frac = (abs % NANM_PER_ANM).toString().padStart(9, '0').replace(/0+$/, '');
    const grouped = whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-' : '') + (frac ? `${grouped}.${frac}` : grouped);
  } catch {
    return String(nanm);
  }
}

/** USD-equivalent string for an nANM amount at usdMicros per ANM; null ref => null. */
export function usdEq(nanm: string | number | bigint | null | undefined, usdMicros: string | number | bigint | null | undefined): string | null {
  if (nanm == null || usdMicros == null) return null;
  try {
    const n = BigInt(nanm);
    const m = BigInt(usdMicros);
    if (m <= 0n) return null;
    const neg = n < 0n;
    const abs = neg ? -n : n;
    // total micro-dollars = nanm * usdMicros / 1e9
    const micros = (abs * m) / NANM_PER_ANM;
    const cents = micros / 10_000n;
    let body: string;
    if (cents >= 1n) {
      const dollars = Number(cents) / 100;
      body = `$${dollars.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    } else {
      const usd = Number(micros) / 1_000_000;
      body = `$${usd.toFixed(6)}`;
    }
    return `${neg ? '-' : ''}${body}`;
  } catch {
    return null;
  }
}

export function fmtUsd(cents: number | string | null | undefined): string {
  const n = Number(cents);
  if (cents == null || !isFinite(n)) return '—';
  return '$' + (n / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function fmtBps(bps: number | null | undefined): string {
  if (bps == null || !isFinite(Number(bps))) return 'n/a';
  return `${(Number(bps) / 100).toFixed(1)}%`;
}

export function fmtInt(n: number | string | bigint | null | undefined): string {
  if (n == null) return '—';
  try {
    return BigInt(n).toLocaleString('en-US');
  } catch {
    return Number(n).toLocaleString('en-US');
  }
}

export function fmtDate(s?: string | null): string {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleDateString();
  } catch {
    return String(s);
  }
}

export function fmtDateTime(s?: string | null): string {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleString();
  } catch {
    return String(s);
  }
}

export function fmtMs(ms?: number | null): string {
  if (ms == null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

export function shortAddr(a?: string | null): string {
  if (!a) return '—';
  return a.length > 22 ? `${a.slice(0, 12)}…${a.slice(-6)}` : a;
}

export function shortHash(h?: string | null, keep = 16): string {
  if (!h) return '—';
  return h.length > keep ? `${h.slice(0, keep)}…` : h;
}

// ── shared fetch: attach x-admin-token when present, always send cookies ──────
export type AdminFetch = (path: string, init?: RequestInit) => Promise<Response>;

// Same sessionStorage key as the store/billing consoles: one paste covers every admin surface.
const TOKEN_KEY = 'anm_store_admin_token';

export function useAdmin() {
  const [token, setToken] = useState('');
  useEffect(() => {
    try {
      setToken(sessionStorage.getItem(TOKEN_KEY) ?? '');
    } catch {
      /* no storage */
    }
  }, []);
  const saveToken = useCallback((t: string) => {
    setToken(t);
    try {
      t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  }, []);
  const adminFetch = useCallback<AdminFetch>(
    (path, init) => {
      const headers: Record<string, string> = { ...(init?.headers as Record<string, string> | undefined) };
      if (token) headers['x-admin-token'] = token;
      return fetch(path, { ...init, headers, credentials: 'include' });
    },
    [token],
  );
  return { token, saveToken, adminFetch };
}

export async function readErr(r: Response): Promise<string> {
  try {
    const d = await r.json();
    return d?.error?.message ?? `HTTP ${r.status}`;
  } catch {
    return `HTTP ${r.status}`;
  }
}

// ── small shared components ──────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  SUCCEEDED: 'var(--good)',
  DONE: 'var(--good)',
  ACTIVE: 'var(--good)',
  PUBLISHED: 'var(--good)',
  ACCEPTED: 'var(--good)',
  COMPLETED: 'var(--good)',
  RUNNING: 'var(--accent-2)',
  DISPATCHED: 'var(--accent-2)',
  CLAIMED: 'var(--accent-2)',
  QUEUED: 'var(--warn)',
  PENDING: 'var(--warn)',
  APPLIED: 'var(--warn)',
  REVIEWING: 'var(--warn)',
  PAST_DUE: 'var(--warn)',
  GRACE_PERIOD: 'var(--warn)',
  IDLE: 'var(--text-faint)',
  PAUSED: 'var(--warn)',
  OPEN: 'var(--warn)',
  ACCRUING: 'var(--warn)',
  FAILED: 'var(--bad)',
  TIMEOUT: 'var(--bad)',
  REJECTED: 'var(--bad)',
  SUSPENDED: 'var(--bad)',
  DISABLED: 'var(--bad)',
  REVOKED: 'var(--bad)',
  REFUNDED: 'var(--text-faint)',
  EXPIRED: 'var(--text-faint)',
  CANCELLED: 'var(--text-faint)',
  CANCELED: 'var(--text-faint)',
  DISMISSED: 'var(--text-faint)',
  ARCHIVED: 'var(--text-faint)',
  DRAFT: 'var(--text-faint)',
  ACTIONED: 'var(--good)',
};

export function StatusPill({ status }: { status?: string | null }) {
  if (!status) return null;
  const c = STATUS_COLOR[status] ?? 'var(--text-dim)';
  return (
    <span className="pill" style={{ color: c, borderColor: c, fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap' }}>
      {status.replace(/_/g, ' ').toLowerCase()}
    </span>
  );
}

export function TokenGate({
  token,
  saveToken,
}: {
  token: string;
  saveToken: (t: string) => void;
}) {
  const [input, setInput] = useState('');
  return (
    <div className="panel" style={{ marginTop: 18, padding: 16 }}>
      <div className="cadm-inline">
        <span style={{ color: 'var(--text-faint)', fontSize: 12, minWidth: 90, flexShrink: 0 }}>admin token</span>
        <input
          type="password"
          className="cadm-input"
          style={{ flex: 1, minWidth: 200 }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={token ? '•••••• (set — leave blank to keep)' : 'MKT_ADMIN_TOKEN'}
        />
        <button
          className="btn primary"
          onClick={() => {
            if (input) {
              saveToken(input);
              setInput('');
            }
          }}
        >
          Use token
        </button>
        {token && (
          <button className="btn ghost" onClick={() => saveToken('')}>
            Clear
          </button>
        )}
      </div>
      <p className="muted" style={{ fontSize: 12, margin: '10px 0 0' }}>
        Send the <code className="inline">MKT_ADMIN_TOKEN</code> (ops), or sign in with an <b>ADMIN</b>-role wallet — either
        satisfies <code className="inline">requireAdmin</code>. The token stays in this tab only.
      </p>
    </div>
  );
}

export function ErrBox({ text }: { text: string }) {
  if (!text) return null;
  return <div className="cadm-err">{text}</div>;
}

/** Shared local styles for both consoles (globals.css has no form/table/button reset). */
export function AdminStyles() {
  return (
    <style>{`
      button.chip { color: var(--text-dim); font-family: var(--font); }
      button.chip.active, button.chip:hover { color: var(--text); }
      .cadm-inline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
      .cadm-input {
        background: var(--bg-elev); border: 1px solid var(--border-bright); border-radius: 8px;
        color: var(--text); padding: 9px 11px; font-size: 13px; font-family: var(--font); outline: none;
        min-height: 40px;
      }
      .cadm-err { border: 1px solid rgba(255,92,114,0.45); background: rgba(255,92,114,0.08); color: var(--bad);
        border-radius: 10px; padding: 10px 12px; font-size: 13.5px; margin-top: 12px; }
      .cadm-warnbox { border: 1px solid rgba(255,180,84,0.45); background: rgba(255,180,84,0.08); color: var(--warn);
        border-radius: 10px; padding: 10px 12px; font-size: 12.5px; margin-top: 10px; }
      .cadm-okbox { border: 1px solid rgba(36,209,139,0.45); background: rgba(36,209,139,0.08); color: var(--good);
        border-radius: 10px; padding: 10px 12px; font-size: 12.5px; margin-top: 10px; }

      .cadm-scroll { overflow-x: auto; }
      .cadm-table { width: 100%; border-collapse: collapse; font-size: 13px; }
      .cadm-table th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
        color: var(--text-faint); font-weight: 600; padding: 11px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
      .cadm-table td { padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; white-space: nowrap; }
      .cadm-table tbody tr:last-child td { border-bottom: none; }
      .cadm-row { cursor: pointer; }
      .cadm-row:hover td { background: rgba(108,92,255,0.05); }
      .cadm-row.open td { background: rgba(108,92,255,0.08); }
      .cadm-detail-row td { background: rgba(108,92,255,0.04); white-space: normal; }

      .cadm-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 2px 24px; margin: 4px 0; }
      .cadm-fact { display: flex; font-size: 12.5px; padding: 2px 0; min-width: 0; }
      .cadm-fact .k { color: var(--text-faint); min-width: 120px; flex: none; }
      .cadm-fact .mono { font-size: 12px; overflow: hidden; text-overflow: ellipsis; }
      .cadm-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 12px;
        border-top: 1px solid var(--border); padding-top: 12px; }
      .cadm-sub-h { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
        color: var(--text-faint); margin: 16px 0 6px; }
      .cadm-event { display: flex; gap: 10px; font-size: 12.5px; padding: 5px 0; border-top: 1px solid var(--border);
        align-items: baseline; min-width: 0; flex-wrap: wrap; }
      .cadm-event:first-of-type { border-top: none; }
      .cadm-ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

      /* drillable KPI numbers */
      .cadm-kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 12px; }
      .cadm-kpi { background: var(--bg-elev); border: 1px solid var(--border); border-radius: 12px; padding: 13px 15px;
        display: flex; flex-direction: column; gap: 4px; min-width: 0; }
      .cadm-kpi .v { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; overflow: hidden; text-overflow: ellipsis; }
      .cadm-kpi .l { font-size: 12px; color: var(--text-faint); }
      .cadm-kpi .s { font-size: 11.5px; color: var(--text-dim); }
      a.cadm-kpi:hover, button.cadm-kpi:hover { border-color: var(--accent); }
      button.cadm-kpi { cursor: pointer; text-align: left; font-family: var(--font); color: var(--text); }

      .cadm-funnel-row { display: flex; align-items: center; gap: 12px; padding: 6px 0; }
      .cadm-funnel-label { width: 170px; flex: none; font-size: 13px; color: var(--text-dim); }
      .cadm-funnel-track { flex: 1; height: 9px; background: var(--bg-elev); border: 1px solid var(--border);
        border-radius: 999px; overflow: hidden; min-width: 60px; }
      .cadm-bar { height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
      .cadm-funnel-count { width: 70px; flex: none; text-align: right; font-size: 13.5px; }

      @media (max-width: 640px) {
        .cadm-funnel-label { width: 110px; font-size: 12px; }
        .cadm-kpi .v { font-size: 17px; }
      }
    `}</style>
  );
}
