'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';

// App Store admin review console (ops surface). Talks to the live store admin routes:
//   GET  /api/mkt/v1/store/admin/builds?status=            — the review queue
//   POST /api/mkt/v1/store/admin/builds/[id]/review        — approve | reject | delist
//   POST /api/mkt/v1/store/admin/purchases/[id]/refund     — refund an ACTIVE purchase
//   GET  /api/mkt/v1/store/admin/stats                     — disk budget + recent purchases
// Auth mirrors lib/adminAuth.ts requireAdmin(): the MKT_ADMIN_TOKEN via the x-admin-token
// header (ops path) OR an ADMIN-role account session cookie (sent automatically). The token
// is held in sessionStorage so it survives navigation but not a tab close.

// ── local formatting (no server config in the client bundle) ─────────────────
const NANM_PER_ANM = 1_000_000_000n;
function fmtAnm(nanm: string | number | bigint): string {
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
function fmtBytes(b: string | number | bigint): string {
  let n = Number(b);
  if (!isFinite(n)) return `${b} B`;
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i === 0 ? n : n.toFixed(1)} ${u[i]}`;
}
function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  try { return new Date(s).toLocaleString(); } catch { return String(s); }
}
function shortHash(h: string | null | undefined, keep = 16): string {
  if (!h) return '—';
  return h.length > keep ? `${h.slice(0, keep)}…` : h;
}
function shortAddr(a: string | null | undefined): string {
  if (!a) return '—';
  return a.length > 22 ? `${a.slice(0, 12)}…${a.slice(-6)}` : a;
}

const STATUSES = ['PENDING_REVIEW', 'UPLOADED', 'CHECKS_FAILED', 'APPROVED', 'REJECTED', 'DELISTED'] as const;
type Status = (typeof STATUSES)[number];

// Mirror of TRANSITIONS in app/api/.../builds/[id]/review/route.ts — only offer valid actions.
function allowedActions(status: string): ('approve' | 'reject' | 'delist')[] {
  switch (status) {
    case 'UPLOADED':
    case 'PENDING_REVIEW': return ['approve', 'reject'];
    case 'REJECTED': return ['approve'];
    case 'APPROVED': return ['reject', 'delist'];
    default: return []; // CHECKS_FAILED / DELISTED are terminal here
  }
}

const STATUS_COLOR: Record<string, string> = {
  PENDING_REVIEW: 'var(--warn)',
  UPLOADED: 'var(--accent-2)',
  CHECKS_FAILED: 'var(--bad)',
  APPROVED: 'var(--good)',
  REJECTED: 'var(--bad)',
  DELISTED: 'var(--text-faint)',
};

function StatusPill({ status }: { status: string }) {
  const c = STATUS_COLOR[status] ?? 'var(--text-dim)';
  return (
    <span className="pill" style={{ color: c, borderColor: c, fontWeight: 600, fontSize: 11 }}>
      {status.replace('_', ' ').toLowerCase()}
    </span>
  );
}

// ── shared fetch: attach x-admin-token when present, always send cookies ──────
function useAdmin() {
  const [token, setToken] = useState('');
  useEffect(() => {
    try { setToken(sessionStorage.getItem('anm_store_admin_token') ?? ''); } catch { /* no storage */ }
  }, []);
  const saveToken = useCallback((t: string) => {
    setToken(t);
    try { t ? sessionStorage.setItem('anm_store_admin_token', t) : sessionStorage.removeItem('anm_store_admin_token'); } catch { /* ignore */ }
  }, []);
  const adminFetch = useCallback((path: string, init?: RequestInit) => {
    const headers: Record<string, string> = { ...(init?.headers as Record<string, string> | undefined) };
    if (token) headers['x-admin-token'] = token;
    return fetch(path, { ...init, headers });
  }, [token]);
  return { token, saveToken, adminFetch };
}

async function readErr(r: Response): Promise<string> {
  try { const d = await r.json(); return d?.error?.message ?? `HTTP ${r.status}`; }
  catch { return `HTTP ${r.status}`; }
}

const box: React.CSSProperties = { background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 8, padding: '2px 7px' };
const rowLabel: React.CSSProperties = { color: 'var(--text-faint)', fontSize: 12, minWidth: 118, flexShrink: 0 };

// ── build review card ─────────────────────────────────────────────────────────
function BuildCard({ build, adminFetch, onDone }: { build: any; adminFetch: ReturnType<typeof useAdmin>['adminFetch']; onDone: () => void }) {
  const [note, setNote] = useState('');
  const [rotateCert, setRotateCert] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showPerms, setShowPerms] = useState(false);

  const listing = build.listing ?? {};
  const badging = (build.badgingJson ?? {}) as any;
  const perms: string[] = Array.isArray(build.permissionsJson) ? build.permissionsJson
    : Array.isArray(badging.permissions) ? badging.permissions : [];
  const sensitive: string[] = Array.isArray(badging.sensitivePermissions) ? badging.sensitivePermissions : [];
  const schemes = badging.schemes as { v1: boolean; v2: boolean; v3: boolean } | null | undefined;

  // Cert continuity vs the listing pin (same rule the review route enforces).
  const pinned: string | null = listing.pinnedCertSha256 ?? null;
  const certMismatch = !!pinned && build.certSha256 && build.certSha256 !== pinned;
  const certFirst = !pinned;
  // packageName claim continuity.
  const pkgMismatch = listing.packageName && build.packageName && listing.packageName !== build.packageName;

  const actions = allowedActions(build.status);

  async function act(action: 'approve' | 'reject' | 'delist') {
    setBusy(action); setMsg(null);
    try {
      const body: any = { action };
      if (note.trim()) body.note = note.trim();
      if (action === 'approve' && certMismatch && rotateCert) body.rotateCert = true;
      const r = await adminFetch(`/api/mkt/v1/store/admin/builds/${build.id}/review`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await readErr(r));
      setMsg({ ok: true, text: `${action} ok` });
      setTimeout(onDone, 500);
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(null); }
  }

  return (
    <div className="panel" style={{ padding: 18 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0, fontSize: 17, letterSpacing: '-0.01em' }}>{listing.name ?? build.packageName}</h3>
            <StatusPill status={build.status} />
            {listing.type && <span className="badge type">{listing.type}</span>}
            {listing.verified && <span className="badge verified">verified</span>}
            {build.channel && build.channel !== 'stable' && <span className="pill" style={{ fontSize: 11 }}>{build.channel}</span>}
          </div>
          <a className="mono" href={`/marketplace/${listing.slug ?? ''}`} target="_blank"
            style={{ color: 'var(--accent)', fontSize: 12.5 }}>/{listing.slug}</a>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>v{build.versionName || '—'}</div>
          <div className="muted" style={{ fontSize: 12 }}>versionCode {build.versionCode}</div>
        </div>
      </div>

      {/* flags row */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '12px 0 2px' }}>
        {sensitive.length > 0 && (
          <span className="badge" style={{ background: 'rgba(255,92,114,0.14)', color: 'var(--bad)' }}>
            ⚠ {sensitive.length} sensitive permission{sensitive.length > 1 ? 's' : ''}
          </span>
        )}
        {certMismatch && (
          <span className="badge" style={{ background: 'rgba(255,92,114,0.14)', color: 'var(--bad)' }}>⚠ cert ≠ pinned signer</span>
        )}
        {certFirst && build.status !== 'CHECKS_FAILED' && (
          <span className="badge" style={{ background: 'rgba(255,180,84,0.14)', color: 'var(--warn)' }}>first build · pins signer on approve</span>
        )}
        {!certMismatch && !certFirst && (
          <span className="badge verified">signer matches pin</span>
        )}
        {pkgMismatch && (
          <span className="badge" style={{ background: 'rgba(255,92,114,0.14)', color: 'var(--bad)' }}>⚠ package ≠ listing claim</span>
        )}
        {schemes && (
          <span className="pill" style={{ fontSize: 11 }}>
            sig {schemes.v3 ? 'v3' : schemes.v2 ? 'v2' : schemes.v1 ? 'v1' : '—'}
            {typeof badging.signerCount === 'number' ? ` · ${badging.signerCount} signer${badging.signerCount === 1 ? '' : 's'}` : ''}
          </span>
        )}
        {build.status === 'CHECKS_FAILED' && (
          <span className="pill" style={{ fontSize: 11, color: 'var(--text-faint)' }}>binary discarded at upload</span>
        )}
      </div>

      {/* facts grid */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 12, fontSize: 13 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <span style={rowLabel}>publisher</span>
          <span className="mono" style={{ fontSize: 12.5 }}>
            {listing.owner?.displayName ? `${listing.owner.displayName} · ` : ''}{shortAddr(listing.owner?.address)}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <span style={rowLabel}>package</span>
          <code className="inline">{build.packageName}</code>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <span style={rowLabel}>size · SDK</span>
          <span className="muted">{fmtBytes(build.sizeBytes)} · min {build.minSdk} / target {build.targetSdk}</span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <span style={rowLabel}>sha3-256</span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--text-dim)' }} title={build.sha3}>{shortHash(build.sha3, 24)}</span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <span style={rowLabel}>signer cert</span>
          <span className="mono" style={{ fontSize: 12, color: certMismatch ? 'var(--bad)' : 'var(--text-dim)' }} title={build.certSha256}>
            {shortHash(build.certSha256, 24)}
          </span>
        </div>
        {pinned && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
            <span style={rowLabel}>pinned cert</span>
            <span className="mono" style={{ fontSize: 12, color: 'var(--text-faint)' }} title={pinned}>{shortHash(pinned, 24)}</span>
          </div>
        )}
        {badging.certDn && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
            <span style={rowLabel}>cert DN</span>
            <span className="muted" style={{ fontSize: 12 }}>{String(badging.certDn).slice(0, 120)}</span>
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
          <span style={rowLabel}>uploaded</span>
          <span className="muted" style={{ fontSize: 12.5 }}>{fmtDate(build.createdAt)}</span>
        </div>
      </div>

      {/* sensitive permissions, called out */}
      {sensitive.length > 0 && (
        <div style={{ marginTop: 12, border: '1px solid rgba(255,92,114,0.35)', borderRadius: 8, padding: '9px 11px', background: 'rgba(255,92,114,0.06)' }}>
          <div style={{ color: 'var(--bad)', fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Sensitive permissions</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {sensitive.map((p) => (
              <span key={p} className="mono" style={{ ...box, fontSize: 11, color: 'var(--bad)', borderColor: 'rgba(255,92,114,0.35)' }}>
                {p.replace('android.permission.', '')}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* all permissions (collapsed) */}
      {perms.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button className="pill" onClick={() => setShowPerms((v) => !v)}
            style={{ cursor: 'pointer', background: 'transparent', fontFamily: 'inherit', fontSize: 11.5, color: 'var(--text-dim)' }}>
            {showPerms ? 'Hide' : 'Show'} all {perms.length} permission{perms.length > 1 ? 's' : ''}
          </button>
          {showPerms && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
              {perms.map((p) => {
                const s = sensitive.includes(p);
                return (
                  <span key={p} className="mono" style={{ ...box, fontSize: 11, color: s ? 'var(--bad)' : 'var(--text-dim)', borderColor: s ? 'rgba(255,92,114,0.35)' : 'var(--border)' }}>
                    {p.replace('android.permission.', '')}
                  </span>
                );
              })}
            </div>
          )}
        </div>
      )}

      {build.releaseNotes && (
        <div style={{ marginTop: 12 }}>
          <div style={rowLabel}>release notes</div>
          <p className="muted" style={{ fontSize: 13, margin: '4px 0 0', whiteSpace: 'pre-wrap' }}>{build.releaseNotes}</p>
        </div>
      )}
      {build.reviewNote && (
        <div style={{ marginTop: 10, fontSize: 12.5, color: build.status === 'CHECKS_FAILED' || build.status === 'REJECTED' ? 'var(--bad)' : 'var(--text-dim)' }}>
          <span style={rowLabel}>review note</span> {build.reviewNote}
        </div>
      )}

      {/* actions */}
      {actions.length > 0 ? (
        <div style={{ marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 14 }}>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="review note (optional, recorded in the audit trail)"
            style={{ width: '100%', background: 'var(--bg-elev)', border: '1px solid var(--border-bright)', borderRadius: 8, color: 'var(--text)', padding: '9px 11px', fontSize: 13, outline: 'none' }}
          />
          {certMismatch && actions.includes('approve') && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, fontSize: 12.5, color: 'var(--warn)' }}>
              <input type="checkbox" checked={rotateCert} onChange={(e) => setRotateCert(e.target.checked)} />
              Rotate the pinned signer cert to this build&apos;s cert (required to approve a cert change)
            </label>
          )}
          <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
            {actions.includes('approve') && (
              <button className="btn primary" disabled={!!busy || (certMismatch && !rotateCert)} onClick={() => act('approve')}>
                {busy === 'approve' ? 'Approving…' : 'Approve'}
              </button>
            )}
            {actions.includes('reject') && (
              <button className="btn" disabled={!!busy} onClick={() => act('reject')}
                style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)' }}>
                {busy === 'reject' ? 'Rejecting…' : 'Reject'}
              </button>
            )}
            {actions.includes('delist') && (
              <button className="btn ghost" disabled={!!busy} onClick={() => act('delist')}
                style={{ color: 'var(--warn)' }}>
                {busy === 'delist' ? 'Delisting…' : 'Delist release'}
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="muted" style={{ marginTop: 14, fontSize: 12.5, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          Terminal state — no transitions from here.{build.status === 'CHECKS_FAILED' ? ' The publisher must re-upload.' : ''}
        </div>
      )}

      {msg && (
        <div style={{ marginTop: 10, fontSize: 13, color: msg.ok ? 'var(--good)' : 'var(--bad)' }}>{msg.text}</div>
      )}
    </div>
  );
}

// ── refundable purchase row ─────────────────────────────────────────────────────
function PurchaseRow({ p, adminFetch, onDone }: { p: any; adminFetch: ReturnType<typeof useAdmin>['adminFetch']; onDone: () => void }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const refundable = p.status === 'ACTIVE';

  async function refund() {
    setBusy(true); setMsg(null);
    try {
      const r = await adminFetch(`/api/mkt/v1/store/admin/purchases/${p.id}/refund`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(reason.trim() ? { reason: reason.trim() } : {}),
      });
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setMsg({ ok: true, text: d.already ? 'already refunded' : `refunded · ${d.licensesRevoked ?? 0} license(s) revoked` });
      setTimeout(onDone, 600);
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ borderTop: '1px solid var(--border)', padding: '12px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <a href={`/marketplace/${p.listing?.slug ?? ''}`} target="_blank" style={{ fontWeight: 600, fontSize: 14 }}>{p.listing?.name ?? p.listing?.slug ?? '—'}</a>
            <StatusPill status={p.status} />
            {p.listing?.type && <span className="badge type" style={{ fontSize: 10 }}>{p.listing.type}</span>}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 3 }}>
            <span className="mono">{shortAddr(p.buyer?.address)}</span> · {p.priceModel} · {p.source} · {fmtDate(p.createdAt)}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>{fmtAnm(p.amountNanm)} <small className="muted">ANM</small></div>
        </div>
        {refundable ? (
          <button className="btn" onClick={() => setOpen((v) => !v)} disabled={busy}
            style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)', fontSize: 13 }}>
            {open ? 'Cancel' : 'Refund'}
          </button>
        ) : (
          <span className="muted" style={{ fontSize: 12, minWidth: 70, textAlign: 'right' }}>
            {p.status === 'REFUNDED' ? 'refunded' : 'not refundable'}
          </span>
        )}
      </div>
      {open && refundable && (
        <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="refund reason (recorded in the ledger memo)"
            style={{ flex: 1, minWidth: 220, background: 'var(--bg-elev)', border: '1px solid var(--border-bright)', borderRadius: 8, color: 'var(--text)', padding: '8px 11px', fontSize: 13, outline: 'none' }}
          />
          <button className="btn primary" onClick={refund} disabled={busy}
            style={{ background: 'linear-gradient(135deg,var(--bad),#ff7a8c)', boxShadow: 'none' }}>
            {busy ? 'Refunding…' : 'Confirm refund'}
          </button>
        </div>
      )}
      {msg && <div style={{ marginTop: 8, fontSize: 12.5, color: msg.ok ? 'var(--good)' : 'var(--bad)' }}>{msg.text}</div>}
    </div>
  );
}

// ── disk-budget publisher bar ───────────────────────────────────────────────────
function PublisherBar({ pub, quota }: { pub: any; quota: string }) {
  const used = Number(pub.bytesUsed);
  const q = Number(quota) || 1;
  const pct = Math.min(100, Math.max(0, (used / q) * 100));
  const color = pct >= 95 ? 'var(--bad)' : pct >= 70 ? 'var(--warn)' : 'var(--good)';
  return (
    <div style={{ padding: '11px 0', borderTop: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span className="mono" style={{ fontSize: 12.5 }}>
          {pub.displayName ? `${pub.displayName} · ` : ''}{shortAddr(pub.address)}
        </span>
        <span style={{ fontSize: 12.5, color }}>
          {fmtBytes(pub.bytesUsed)} / {fmtBytes(quota)} · {pct.toFixed(1)}%
        </span>
      </div>
      <div style={{ height: 7, background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 999, marginTop: 7, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, transition: 'width .2s' }} />
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 5 }}>
        {pub.buildCount} build{pub.buildCount === 1 ? '' : 's'} across {pub.listings?.length ?? 0} listing{(pub.listings?.length ?? 0) === 1 ? '' : 's'}
        {Array.isArray(pub.listings) && pub.listings.length ? ` · ${pub.listings.slice(0, 4).join(', ')}${pub.listings.length > 4 ? '…' : ''}` : ''}
      </div>
    </div>
  );
}

// ── page ────────────────────────────────────────────────────────────────────────
export default function StoreReviewAdmin() {
  const { token, saveToken, adminFetch } = useAdmin();
  const [tokenInput, setTokenInput] = useState('');
  const [status, setStatus] = useState<Status>('PENDING_REVIEW');
  const [builds, setBuilds] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [authErr, setAuthErr] = useState('');

  const loadBuilds = useCallback(async (st: Status) => {
    setLoading(true); setAuthErr('');
    try {
      const r = await adminFetch(`/api/mkt/v1/store/admin/builds?status=${st}&limit=200`);
      if (r.status === 401 || r.status === 404) { setAuthErr(await readErr(r)); setBuilds([]); return; }
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setBuilds(d.builds ?? []);
    } catch (e: any) { setAuthErr(e.message); setBuilds([]); }
    finally { setLoading(false); }
  }, [adminFetch]);

  const loadStats = useCallback(async () => {
    try {
      const r = await adminFetch('/api/mkt/v1/store/admin/stats');
      if (!r.ok) { setStats(null); return; }
      setStats(await r.json());
    } catch { setStats(null); }
  }, [adminFetch]);

  const refresh = useCallback(() => { loadBuilds(status); loadStats(); }, [loadBuilds, loadStats, status]);

  useEffect(() => { loadBuilds(status); }, [status, loadBuilds]);
  useEffect(() => { loadStats(); }, [loadStats]);

  const counts = stats?.buildCounts ?? {};
  const publishers: any[] = stats?.publishers ?? [];
  const purchases: any[] = stats?.recentPurchases ?? [];
  const overQuota = useMemo(
    () => (stats ? publishers.filter((p) => Number(p.bytesUsed) / (Number(stats.quotaBytes) || 1) >= 0.95).length : 0),
    [publishers, stats],
  );

  return (
    <div className="wrap" style={{ paddingTop: 34, paddingBottom: 60 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 30, letterSpacing: '-0.03em', margin: 0 }}>App Store review</h1>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 14 }}>Approve builds, delist releases, refund purchases, and watch publisher disk budgets.</p>
        </div>
        <button className="btn" onClick={refresh} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
      </div>

      {/* admin token gate */}
      <div className="panel" style={{ marginTop: 18, padding: 16 }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={rowLabel}>admin token</span>
          <input
            type="password"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder={token ? '•••••• (set — leave blank to keep)' : 'MKT_ADMIN_TOKEN'}
            style={{ flex: 1, minWidth: 220, background: 'var(--bg-elev)', border: '1px solid var(--border-bright)', borderRadius: 8, color: 'var(--text)', padding: '9px 11px', fontSize: 13, outline: 'none' }}
          />
          <button className="btn primary" onClick={() => { if (tokenInput) { saveToken(tokenInput); setTokenInput(''); } setTimeout(refresh, 50); }}>Use token</button>
          {token && <button className="btn ghost" onClick={() => { saveToken(''); setTimeout(refresh, 50); }}>Clear</button>}
        </div>
        <p className="muted" style={{ fontSize: 12, margin: '10px 0 0' }}>
          Send the <code className="inline">MKT_ADMIN_TOKEN</code> (ops), or sign in with an <b>ADMIN</b>-role wallet — either satisfies <code className="inline">requireAdmin</code>. The token stays in this tab only.
        </p>
      </div>

      {authErr && (
        <div className="panel" style={{ marginTop: 16, borderColor: 'rgba(255,92,114,0.4)' }}>
          <div style={{ color: 'var(--bad)', fontSize: 14 }}>Admin request denied: {authErr}</div>
          <p className="muted" style={{ fontSize: 12.5, margin: '6px 0 0' }}>Enter the admin token above, or log in with an ADMIN account.</p>
        </div>
      )}

      {/* KPIs */}
      {stats && (
        <div className="panel" style={{ marginTop: 16 }}>
          <div className="kpi">
            <div className="k"><b style={{ color: 'var(--warn)' }}>{counts.PENDING_REVIEW ?? 0}</b><span>pending review</span></div>
            <div className="k"><b style={{ color: 'var(--accent-2)' }}>{counts.UPLOADED ?? 0}</b><span>uploaded</span></div>
            <div className="k"><b style={{ color: 'var(--good)' }}>{counts.APPROVED ?? 0}</b><span>approved</span></div>
            <div className="k"><b style={{ color: 'var(--bad)' }}>{counts.CHECKS_FAILED ?? 0}</b><span>checks failed</span></div>
            <div className="k"><b>{counts.REJECTED ?? 0}</b><span>rejected</span></div>
            <div className="k"><b>{counts.DELISTED ?? 0}</b><span>delisted</span></div>
            <div className="k"><b style={{ color: overQuota ? 'var(--bad)' : 'var(--text)' }}>{overQuota}</b><span>publishers ≥95% quota</span></div>
          </div>
        </div>
      )}

      {/* status tabs */}
      <div className="chips" style={{ marginTop: 20 }}>
        {STATUSES.map((s) => (
          <button key={s} className={`chip${status === s ? ' active' : ''}`} onClick={() => setStatus(s)} style={{ font: 'inherit', fontSize: '13.5px' }}>
            {s.replace('_', ' ').toLowerCase()}
            {stats && counts[s] != null ? ` · ${counts[s]}` : ''}
          </button>
        ))}
      </div>

      {/* build queue */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 12 }}>
        {loading && builds.length === 0 ? (
          <div className="empty">Loading builds…</div>
        ) : builds.length === 0 ? (
          <div className="empty">{authErr ? 'No access.' : `No ${status.replace('_', ' ').toLowerCase()} builds.`}</div>
        ) : (
          builds.map((b) => <BuildCard key={b.id} build={b} adminFetch={adminFetch} onDone={refresh} />)
        )}
      </div>

      {/* disk budget */}
      <section className="section" style={{ paddingBottom: 6 }}>
        <h2>Publisher disk budget</h2>
        <p className="sub">Durable build bytes per publisher vs <code className="inline">STORE_PUBLISHER_QUOTA_BYTES</code>{stats ? ` (${fmtBytes(stats.quotaBytes)})` : ''}. Excludes checks-failed uploads (their binary was discarded).</p>
        <div className="panel">
          {publishers.length === 0 ? (
            <div className="muted" style={{ fontSize: 13 }}>No publisher storage recorded yet.</div>
          ) : (
            publishers.map((p, i) => <PublisherBar key={p.address ?? i} pub={p} quota={String(stats.quotaBytes)} />)
          )}
        </div>
      </section>

      {/* refunds */}
      <section className="section" style={{ paddingTop: 10 }}>
        <h2>Recent purchases</h2>
        <p className="sub">The 30 latest store purchases — the feed the refund action works from. Only <b>ACTIVE</b> purchases are refundable; a refund reverses the split, credits the buyer, and revokes the license.</p>
        <div className="panel" style={{ paddingTop: 4 }}>
          {purchases.length === 0 ? (
            <div className="muted" style={{ fontSize: 13, paddingTop: 12 }}>No store purchases yet.</div>
          ) : (
            purchases.map((p) => <PurchaseRow key={p.id} p={p} adminFetch={adminFetch} onDone={refresh} />)
          )}
        </div>
      </section>
    </div>
  );
}
