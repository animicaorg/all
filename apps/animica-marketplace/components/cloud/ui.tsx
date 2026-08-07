'use client';

// The pure formatters live in ./format so SERVER components can call them too.
// Re-exported here because client code already imports them from this module,
// and because an export of a 'use client' file is a client reference in the
// server graph — calling one server-side throws "is not a function".
export { timeAgo, fmtMs, fmtInt, usd } from './format';

// Animica Python Cloud — shared client-side pieces for the /cloud console.
// Client-safe: never imports lib/config or lib/nanm (they read process.env and throw in the
// browser). Money/date formatting comes from app/dev/ui.tsx, which exists for that reason.
//
// ── API contract the console consumes ────────────────────────────────────────
// Existing routes (already live):
//   POST /api/cloud/v1/fn/{owner}/{slug}        public invoke (test panel)
//   POST /api/cloud/v1/enterprise               contact-sales intake
//   GET|POST /api/mkt/v1/withdrawals            payout history / request payout
//   GET  /api/mkt/v1/billing/plans|summary  POST /billing/change|subscribe|confirm|cancel
// Routes served by the cloud API surface (thin wrappers over lib/cloud/*):
//   POST /api/cloud/v1/validate                 {source,entrypoint} -> ValidationReport
//   POST /api/cloud/v1/estimate                 {functionId?|timeoutMs,memoryMb,surchargeNanm?} -> DeployCostEstimate
//   POST /api/cloud/v1/functions                create + first deploy -> CreateVersionResult
//   POST /api/cloud/v1/functions/:id/versions   redeploy new source -> CreateVersionResult
//   POST /api/cloud/v1/functions/:id/rollback   {version} -> deployment
//   PATCH|DELETE /api/cloud/v1/functions/:id    settings / delete
//   GET  /api/cloud/v1/functions/:id/logs       recent execution logs (live tail)
//   GET  /api/cloud/v1/deployments/:id          deployment + refreshed anchor confirms
//   POST|PATCH|DELETE /api/cloud/v1/agents(/:id)
//   POST|DELETE /api/cloud/v1/secrets(/:id)
//   GET  /api/cloud/v1/me/analytics?days=N      day-bucketed series + per-function rollup

import { useCallback, useEffect, useState } from 'react';

// ── fetch helper (error envelope aware: {error:{code,message,details}}) ──────
export interface CloudApiError extends Error {
  status?: number;
  code?: string;
  details?: any;
}

export async function api(path: string, init?: RequestInit): Promise<any> {
  const r = await fetch(path, {
    credentials: 'include',
    ...init,
    headers: {
      ...(init?.body ? { 'content-type': 'application/json' } : {}),
      ...(init?.headers as Record<string, string>),
    },
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(
      data?.error?.message || (r.status === 404 ? `endpoint not found (${path})` : `${r.status} ${r.statusText}`),
    ) as CloudApiError;
    e.status = r.status;
    e.code = data?.error?.code;
    e.details = data?.error?.details;
    throw e;
  }
  return data;
}

// ── capability catalog (client-safe mirror of lib/cloud/config CAPABILITIES) ─
export const CAPABILITY_INFO: { key: string; label: string; blurb: string; sensitive: boolean }[] = [
  { key: 'AI_INFERENCE', label: 'AI inference', blurb: 'Call Animica AI models from the function', sensitive: false },
  { key: 'CALL_FUNCTION', label: 'Call functions', blurb: 'Invoke other cloud functions (nested calls)', sensitive: true },
  { key: 'CALL_APP', label: 'Call apps', blurb: 'Invoke marketplace apps on behalf of the caller', sensitive: true },
  { key: 'READ_CHAIN', label: 'Read chain', blurb: 'Read Animica chain state (balances, heads, txs)', sensitive: false },
  { key: 'SPEND_ANM', label: 'Spend ANM', blurb: 'Spend the caller’s ANM under an explicit grant', sensitive: true },
  { key: 'PERSIST_STATE', label: 'Persist state', blurb: 'Key-value state that survives between executions', sensitive: false },
  { key: 'SCHEDULE', label: 'Schedules', blurb: 'Run on a schedule (cron / interval)', sensitive: false },
  { key: 'HTTP_FETCH', label: 'HTTP fetch', blurb: 'Outbound HTTP from the sandbox (brokered)', sensitive: true },
];

// ── status colors for every Cloud enum the console renders ───────────────────
const CLOUD_STATUS_COLOR: Record<string, string> = {
  // CloudStatus / CloudVisibility
  DRAFT: 'var(--text-faint)', PUBLISHED: 'var(--good)', SUSPENDED: 'var(--bad)', ARCHIVED: 'var(--text-faint)',
  PUBLIC: 'var(--good)', UNLISTED: 'var(--warn)', PRIVATE: 'var(--text-faint)',
  // CloudDeployStatus
  VALIDATING: 'var(--accent-2)', BUILDING: 'var(--accent-2)', AWAITING_SIGNATURE: 'var(--warn)',
  BROADCASTING: 'var(--accent-2)', CONFIRMING: 'var(--warn)', ACTIVE: 'var(--good)',
  FAILED: 'var(--bad)', PAUSED: 'var(--text-faint)',
  // CloudExecStatus
  QUEUED: 'var(--text-faint)', DISPATCHED: 'var(--accent-2)', RUNNING: 'var(--accent-2)',
  SUCCEEDED: 'var(--good)', TIMEOUT: 'var(--bad)', CANCELLED: 'var(--text-faint)', REJECTED: 'var(--bad)',
  // CloudAgentStatus
  DISABLED: 'var(--text-faint)',
  // Withdrawal
  REQUESTED: 'var(--warn)', SENT: 'var(--accent-2)', CONFIRMED: 'var(--good)',
};

export function CloudStatusPill({ status, title }: { status?: string | null; title?: string }) {
  if (!status) return null;
  const c = CLOUD_STATUS_COLOR[status] ?? 'var(--text-dim)';
  return (
    <span className="pill" title={title} style={{ color: c, borderColor: c, fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap' }}>
      {status.replace(/_/g, ' ').toLowerCase()}
    </span>
  );
}

// ── small display helpers ────────────────────────────────────────────────────




// ── copy-to-clipboard button ─────────────────────────────────────────────────
export function CopyButton({ text, label = 'Copy', small = false }: { text: string; label?: string; small?: boolean }) {
  const [done, setDone] = useState(false);
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard API denied (http / permissions): fall back to a selection-based copy.
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    setDone(true);
    setTimeout(() => setDone(false), 1400);
  }, [text]);
  return (
    <button
      type="button"
      className="btn ghost"
      onClick={copy}
      style={small ? { fontSize: 12, padding: '5px 10px', minHeight: 30 } : { fontSize: 13, padding: '7px 12px' }}
      title="Copy to clipboard"
    >
      {done ? '✓ Copied' : label}
    </button>
  );
}

// ── message boxes ────────────────────────────────────────────────────────────
export function ErrBox({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return (
    <div style={{
      border: '1px solid rgba(255,92,114,0.45)', background: 'rgba(255,92,114,0.08)', color: 'var(--bad)',
      borderRadius: 10, padding: '10px 12px', fontSize: 13.5, marginTop: 12, overflowWrap: 'anywhere',
    }}>
      {children}
    </div>
  );
}

export function OkBox({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return (
    <div style={{
      border: '1px solid rgba(36,209,139,0.4)', background: 'rgba(36,209,139,0.07)',
      borderRadius: 10, padding: '10px 12px', fontSize: 13.5, marginTop: 12, overflowWrap: 'anywhere',
    }}>
      {children}
    </div>
  );
}

/** Renders a plan-limit (402) error with its contextual upgrade CTA, else the plain message. */
export function ApiErrBox({ error }: { error: CloudApiError | null }) {
  if (!error) return null;
  const upgrade = error.status === 402 && (error.details?.upgradeUrl || error.code === 'plan_limit');
  return (
    <ErrBox>
      {error.message}
      {upgrade && (
        <div style={{ marginTop: 8 }}>
          <a className="btn" href={error.details?.upgradeUrl ?? '/cloud/pricing'} style={{ fontSize: 12.5, padding: '6px 12px' }}>
            View plans →
          </a>
        </div>
      )}
    </ErrBox>
  );
}

// ── confirmation dialog for destructive actions ──────────────────────────────
export function ConfirmDialog({
  open, title, body, confirmLabel = 'Confirm', danger = true, requireText, busy = false, onConfirm, onCancel,
}: {
  open: boolean;
  title: string;
  body: React.ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  /** When set, the user must type this exact string to enable the confirm button. */
  requireText?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState('');
  useEffect(() => {
    if (open) setTyped('');
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onCancel();
    };
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [open, busy, onCancel]);
  if (!open) return null;
  const blocked = !!requireText && typed !== requireText;
  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(3,4,7,0.62)', backdropFilter: 'blur(3px)', display: 'grid', placeItems: 'center', padding: 18 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}
    >
      <div className="panel" role="dialog" aria-modal="true" style={{ width: 'min(440px, 96vw)', borderColor: danger ? 'rgba(255,92,114,0.5)' : 'var(--border-bright)' }}>
        <h3 style={{ margin: '0 0 8px', fontSize: 17 }}>{title}</h3>
        <div className="muted" style={{ fontSize: 13.5, lineHeight: 1.55 }}>{body}</div>
        {requireText && (
          <div style={{ marginTop: 12 }}>
            <label style={{ display: 'block', fontSize: 12.5, color: 'var(--text-dim)', marginBottom: 5 }}>
              Type <code className="inline">{requireText}</code> to confirm
            </label>
            <input
              className="cl-input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoFocus
              spellCheck={false}
              autoComplete="off"
            />
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, marginTop: 16, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          <button className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button>
          <button
            className="btn"
            onClick={onConfirm}
            disabled={busy || blocked}
            style={danger ? { borderColor: 'var(--bad)', color: 'var(--bad)' } : undefined}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── shared form styles (mirrors app/dev/ui.tsx, plus the .cl-input class in the
//    /cloud layout stylesheet so plain server-rendered inputs match too) ───────
export const inputStyle: React.CSSProperties = {
  width: '100%', background: 'var(--bg-elev)', border: '1px solid var(--border-bright)',
  borderRadius: 8, color: 'var(--text)', padding: '9px 11px', fontSize: 13.5, outline: 'none',
  fontFamily: 'inherit', minHeight: 40,
};
export const labelStyle: React.CSSProperties = {
  color: 'var(--text-dim)', fontSize: 12.5, marginBottom: 5, display: 'block',
};
