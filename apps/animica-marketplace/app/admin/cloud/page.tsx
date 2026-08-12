'use client';
import { Fragment, useCallback, useEffect, useState } from 'react';
import {
  AdminStyles,
  ErrBox,
  StatusPill,
  TokenGate,
  useAdmin,
  readErr,
  fmtAnmFull,
  fmtDate,
  fmtDateTime,
  fmtInt,
  fmtMs,
  shortAddr,
  shortHash,
  type AdminFetch,
} from './kit';

// /admin/cloud — the Python Cloud operational console (§39).
//
// Overview (system health), Executions (with the FULL nested-call trace and the ledger
// entries settlement posted), Apps, Developers, Providers, abuse Reports, Deployments
// (failures + compromise response), the code-hash Denylist, and the CloudAuditLog browser.
// Every action here goes through an audited admin API; nothing mutates state client-side.
// Finance lives in /admin/profitability.

const API = '/api/cloud/v1/admin';

function confirmReason(promptText: string): string | null {
  const reason = window.prompt(`${promptText}\n\nReason (required — recorded in the audit log):`) ?? '';
  return reason.trim() ? reason.trim() : null;
}

// ── Overview ─────────────────────────────────────────────────────────────────
function OverviewTab({ adminFetch, goTo }: { adminFetch: AdminFetch; goTo: (tab: string) => void }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/overview`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch]);
  useEffect(() => {
    load();
  }, [load]);

  async function reap() {
    const reason = confirmReason('Kill orphaned sandbox containers older than the TTL?');
    if (reason == null) return;
    setBusy(true);
    setMsg('');
    try {
      const r = await adminFetch(`${API}/overview`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ action: 'reap_orphans', reason }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setMsg(`Reaped ${d.killed} orphaned container(s).`);
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !data) return <ErrBox text={error} />;
  if (!data) return <div className="empty" style={{ marginTop: 16 }}>Loading system health…</div>;

  const s = data.sandbox;
  const ex = data.executions;
  const fl = data.fleet;
  const at = data.attention;
  const cat = data.catalog;

  return (
    <>
      {error && <ErrBox text={error} />}
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="cadm-inline" style={{ justifyContent: 'space-between' }}>
          <b>Sandbox</b>
          <button className="btn ghost" disabled={busy} onClick={reap}>{busy ? 'Reaping…' : 'Reap orphaned containers'}</button>
        </div>
        <div className="kpi" style={{ marginTop: 10 }}>
          <div className="k"><b style={{ color: s.inFlight >= s.capacity ? 'var(--warn)' : 'var(--good)' }}>{s.inFlight} / {s.capacity}</b><span>containers in flight</span></div>
          <div className="k"><b style={{ color: s.waiting > 0 ? 'var(--warn)' : undefined }}>{s.waiting}</b><span>waiting for a slot</span></div>
          <div className="k"><b style={{ color: 'var(--warn)' }}>{ex.queued}</b><span>executions queued</span></div>
          <div className="k"><b style={{ color: 'var(--accent-2)' }}>{ex.running}</b><span>running</span></div>
          <div className="k"><b>{fmtInt(ex.last24h)}</b><span>executions · 24h</span></div>
          <div className="k"><b style={{ color: ex.failed24h > 0 ? 'var(--bad)' : 'var(--good)' }}>{fmtInt(ex.failed24h)}</b><span>failed/timeout · 24h</span></div>
        </div>
        {msg && <div style={{ marginTop: 8, fontSize: 13, color: 'var(--good)' }}>{msg}</div>}
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <b>Compute fleet</b>
        <div className="kpi" style={{ marginTop: 10 }}>
          <div className="k"><b style={{ color: 'var(--good)' }}>{fl.providersActive}</b><span>providers active</span></div>
          <div className="k"><b style={{ color: fl.providersStale > 0 ? 'var(--warn)' : undefined }}>{fl.providersStale}</b><span>stale (&gt;{fl.staleAfterSeconds}s silent)</span></div>
          <div className="k"><b>{fl.providersSuspended}</b><span>suspended/disabled</span></div>
          <div className="k"><b style={{ color: fl.jobsPending > 0 ? 'var(--warn)' : undefined }}>{fl.jobsPending}</b><span>jobs pending</span></div>
          <div className="k"><b>{fl.jobsInFlight}</b><span>jobs in flight</span></div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <b>Catalog</b>
        <div className="kpi" style={{ marginTop: 10 }}>
          <div className="k"><b>{fmtInt(cat.functionsPublished)}</b><span>functions published</span></div>
          <div className="k"><b>{fmtInt(cat.appsPublished)}</b><span>apps published</span></div>
          <div className="k"><b style={{ color: cat.appsSuspended > 0 ? 'var(--warn)' : undefined }}>{fmtInt(cat.appsSuspended)}</b><span>apps suspended</span></div>
          <div className="k"><b>{fmtInt(cat.agentsActive)}</b><span>agents active</span></div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <b>Needs attention</b>
        <div className="cadm-kpis" style={{ marginTop: 10 }}>
          <button className="cadm-kpi" onClick={() => goTo('reports')}>
            <span className="v" style={{ color: at.reportsOpen > 0 ? 'var(--warn)' : 'var(--good)' }}>{at.reportsOpen}</span>
            <span className="l">open abuse reports</span>
          </button>
          <button className="cadm-kpi" onClick={() => goTo('deployments')}>
            <span className="v" style={{ color: at.deployFailed7d > 0 ? 'var(--warn)' : 'var(--good)' }}>{at.deployFailed7d}</span>
            <span className="l">deployment failures · 7d</span>
          </button>
          <a className="cadm-kpi" href="/admin/profitability?tab=alerts">
            <span className="v" style={{ color: at.alertsOpen > 0 ? 'var(--bad)' : 'var(--good)' }}>{at.alertsOpen}</span>
            <span className="l">unresolved finance alerts</span>
          </a>
          <button className="cadm-kpi" onClick={() => goTo('denylist')}>
            <span className="v">{at.denylistCount}</span>
            <span className="l">blocked code hashes</span>
          </button>
        </div>
      </div>

      <div className="cadm-sub-h">Recent admin actions</div>
      <div className="panel">
        {data.audit.length === 0 ? (
          <div className="muted" style={{ fontSize: 13 }}>No admin actions recorded yet.</div>
        ) : (
          data.audit.map((a: any) => (
            <div key={a.id} className="cadm-event">
              <span className="muted" style={{ flex: 'none', width: 150 }}>{fmtDateTime(a.createdAt)}</span>
              <span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>{a.action}</span>
              <span className="mono cadm-ellipsis" style={{ fontSize: 12 }}>{a.subject}</span>
              <span className="muted cadm-ellipsis">{a.reason} · by {shortAddr(a.actor)}</span>
            </div>
          ))
        )}
      </div>
    </>
  );
}

// ── Execution detail (full trace + ledger) ───────────────────────────────────
function ExecDetail({ id, adminFetch }: { id: string; adminFetch: AdminFetch }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const r = await adminFetch(`${API}/executions/${id}`);
        if (!r.ok) throw new Error(await readErr(r));
        setData(await r.json());
      } catch (e: any) {
        setError(e.message);
      }
    })();
  }, [adminFetch, id]);

  if (error) return <ErrBox text={error} />;
  if (!data) return <div className="muted" style={{ padding: 8 }}>Loading execution…</div>;
  const e = data.execution;

  return (
    <div style={{ padding: '6px 2px 10px' }}>
      <div className="cadm-facts">
        <div className="cadm-fact"><span className="k">request id</span><span className="mono">{e.requestId}</span></div>
        <div className="cadm-fact"><span className="k">function</span><span className="mono">{e.function?.slug} v{e.version?.version}</span></div>
        <div className="cadm-fact"><span className="k">developer</span><span className="mono">{shortAddr(e.developer?.address)}</span></div>
        <div className="cadm-fact"><span className="k">caller</span><span className="mono">{e.caller ? shortAddr(e.caller.address) : 'anonymous'}</span></div>
        <div className="cadm-fact"><span className="k">lane</span><span>{e.lane}{e.provider ? ` · ${e.provider.name || shortAddr(e.provider.address)}` : ''}</span></div>
        <div className="cadm-fact"><span className="k">timing</span><span>{fmtDateTime(e.queuedAt)} · {fmtMs(e.durationMs)}</span></div>
        <div className="cadm-fact"><span className="k">resources</span><span>{e.cpuMs}ms CPU · {e.memoryMbMs} MB-ms · AI {e.aiTokensIn}/{e.aiTokensOut} tok · {e.egressBytes}B out</span></div>
        <div className="cadm-fact"><span className="k">price</span><span className="mono">{fmtAnmFull(e.priceNanm)} ANM{e.freeTier ? ' (free tier)' : ''}</span></div>
        <div className="cadm-fact"><span className="k">split</span><span className="mono">fee {fmtAnmFull(e.platformFeeNanm)} + dev {fmtAnmFull(e.developerNanm)} + prov {fmtAnmFull(e.providerNanm)} @ {e.feeBps}bps {data.splitExact ? '✓ exact' : '✗ MISMATCH'}</span></div>
        <div className="cadm-fact"><span className="k">COGS</span><span className="mono">{fmtAnmFull(e.cogsNanm)} ANM (compute {fmtAnmFull(e.cogsComputeNanm)} · ai {fmtAnmFull(e.cogsAiNanm)} · infra {fmtAnmFull(e.cogsInfraNanm)} · promo {fmtAnmFull(e.cogsPromoNanm)})</span></div>
        <div className="cadm-fact"><span className="k">contribution</span><span className="mono" style={{ color: BigInt(e.contributionNanm) < 0n ? 'var(--bad)' : 'var(--good)' }}>{fmtAnmFull(e.contributionNanm)} ANM</span></div>
        <div className="cadm-fact"><span className="k">source sha3</span><span className="mono">{shortHash(e.version?.sourceSha3, 24)}</span></div>
        {e.error && <div className="cadm-fact"><span className="k">error</span><span style={{ color: 'var(--bad)' }}>{e.errorCode}: {e.error}</span></div>}
      </div>

      <div className="cadm-sub-h">Execution trace ({data.trace.length} call{data.trace.length === 1 ? '' : 's'})</div>
      <div className="cadm-scroll">
        <table className="cadm-table" style={{ minWidth: 700 }}>
          <thead><tr><th>call</th><th>status</th><th>lane</th><th style={{ textAlign: 'right' }}>duration</th><th style={{ textAlign: 'right' }}>price ANM</th><th style={{ textAlign: 'right' }}>contribution</th></tr></thead>
          <tbody>
            {data.trace.map((t: any) => (
              <tr key={t.id} style={t.id === e.id ? { background: 'rgba(108,92,255,0.07)' } : undefined}>
                <td>
                  <span style={{ paddingLeft: t.depth * 18 }} className="mono">
                    {t.depth > 0 ? '└ ' : ''}{t.function?.slug ?? t.id}
                  </span>
                </td>
                <td><StatusPill status={t.status} /></td>
                <td>{t.lane}</td>
                <td style={{ textAlign: 'right' }}>{fmtMs(t.durationMs)}</td>
                <td style={{ textAlign: 'right' }} className="mono">{fmtAnmFull(t.priceNanm)}{t.freeTier ? ' (free)' : ''}</td>
                <td style={{ textAlign: 'right', color: BigInt(t.contributionNanm) < 0n ? 'var(--bad)' : undefined }} className="mono">{fmtAnmFull(t.contributionNanm)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="cadm-sub-h">Ledger entries (net {fmtAnmFull(data.ledgerNetNanm)} ANM — must be 0 once settled)</div>
      {data.ledger.length === 0 ? (
        <div className="muted" style={{ fontSize: 12.5 }}>No ledger entries — the execution was free-tier, unsettled, or charged nothing.</div>
      ) : (
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 640 }}>
            <thead><tr><th>when</th><th>account</th><th>kind</th><th style={{ textAlign: 'right' }}>Δ ANM</th><th>memo</th></tr></thead>
            <tbody>
              {data.ledger.map((l: any) => (
                <tr key={l.id}>
                  <td>{fmtDateTime(l.createdAt)}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{shortAddr(l.account?.address)}{l.account?.displayName ? ` (${l.account.displayName})` : ''}</td>
                  <td>{l.kind}</td>
                  <td className="mono" style={{ textAlign: 'right', color: BigInt(l.deltaNanm) < 0n ? 'var(--bad)' : 'var(--good)' }}>{fmtAnmFull(l.deltaNanm)}</td>
                  <td className="muted cadm-ellipsis" style={{ maxWidth: 220 }}>{l.memo}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="cadm-sub-h">Logs</div>
      {data.execution.logs.length === 0 ? (
        <div className="muted" style={{ fontSize: 12.5 }}>No log lines captured (or already past retention).</div>
      ) : (
        <pre className="mono" style={{ fontSize: 11.5, background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 10, padding: 10, overflowX: 'auto', maxHeight: 260 }}>
          {data.execution.logs.map((l: any) => `[${l.level}] ${l.message}`).join('\n')}
        </pre>
      )}
    </div>
  );
}

// ── Executions tab ───────────────────────────────────────────────────────────
const EXEC_STATUSES = ['QUEUED', 'DISPATCHED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'REJECTED'];
const EXEC_RANGES = ['today', '24h', '7d', '30d', 'mtd', '90d', 'all'];

function ExecutionsTab({ adminFetch, initial }: { adminFetch: AdminFetch; initial: Record<string, string> }) {
  const [filters, setFilters] = useState<Record<string, string>>({ range: initial.range ?? '7d', ...initial });
  const [rows, setRows] = useState<any[]>([]);
  const [totals, setTotals] = useState<any>(null);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState<string | null>(initial.exec ?? null);
  const TAKE = 50;

  const load = useCallback(
    async (nextSkip: number) => {
      setLoading(true);
      setError('');
      try {
        const p = new URLSearchParams({ take: String(TAKE), skip: String(nextSkip) });
        for (const [k, v] of Object.entries(filters)) if (v && k !== 'exec') p.set(k, v);
        const r = await adminFetch(`${API}/executions?${p}`);
        if (!r.ok) throw new Error(await readErr(r));
        const d = await r.json();
        setRows(d.rows ?? []);
        setTotals(d.totals ?? null);
        setTotal(d.total ?? 0);
        setSkip(nextSkip);
      } catch (e: any) {
        setError(e.message);
        setRows([]);
      } finally {
        setLoading(false);
      }
    },
    [adminFetch, filters],
  );
  useEffect(() => {
    load(0);
  }, [load]);

  const toggles: Array<[string, string]> = [
    ['priced', 'priced only'],
    ['free', 'free tier'],
    ['ai', 'AI-consuming'],
    ['negative', 'losing money'],
  ];

  return (
    <>
      <div className="panel" style={{ marginTop: 16, padding: 14 }}>
        <div className="cadm-inline">
          <select className="cadm-input" value={filters.range ?? '7d'} onChange={(e) => setFilters({ ...filters, range: e.target.value })}>
            {EXEC_RANGES.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <select className="cadm-input" value={filters.status ?? ''} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">any status</option>
            {EXEC_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="cadm-input" value={filters.lane ?? ''} onChange={(e) => setFilters({ ...filters, lane: e.target.value })}>
            <option value="">any lane</option>
            <option value="local">local</option>
            <option value="fleet">fleet</option>
          </select>
          <input className="cadm-input" style={{ flex: 1, minWidth: 180 }} placeholder="request id / execution id" value={filters.q ?? ''} onChange={(e) => setFilters({ ...filters, q: e.target.value.trim() })} />
          {toggles.map(([k, label]) => (
            <button key={k} className={`chip${filters[k] === '1' ? ' active' : ''}`} onClick={() => setFilters({ ...filters, [k]: filters[k] === '1' ? '' : '1' })}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {error && <ErrBox text={error} />}
      {totals && (
        <div className="cadm-inline" style={{ marginTop: 10, fontSize: 12.5, gap: 16 }}>
          <span className="muted">Σ in filter:</span>
          <span>gross <b className="mono">{fmtAnmFull(totals.grossNanm)} ANM</b></span>
          <span>fees <b className="mono">{fmtAnmFull(totals.platformFeeNanm)} ANM</b></span>
          <span>COGS <b className="mono">{fmtAnmFull(totals.cogsNanm)} ANM</b></span>
          <span>contribution <b className="mono" style={{ color: BigInt(totals.contributionNanm ?? 0) < 0n ? 'var(--bad)' : 'var(--good)' }}>{fmtAnmFull(totals.contributionNanm)} ANM</b></span>
        </div>
      )}
      <div className="panel" style={{ marginTop: 10, padding: 0, overflow: 'hidden' }}>
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 1020 }}>
            <thead><tr><th>when</th><th>function</th><th>caller</th><th>status</th><th>lane</th><th style={{ textAlign: 'right' }}>ms</th><th style={{ textAlign: 'right' }}>price ANM</th><th style={{ textAlign: 'right' }}>fee</th><th style={{ textAlign: 'right' }}>contribution</th></tr></thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={9} className="muted" style={{ padding: 24, textAlign: 'center' }}>{loading ? 'Loading…' : 'No executions match.'}</td></tr>
              ) : (
                rows.map((r) => (
                  <Fragment key={r.id}>
                    <tr className={`cadm-row${openId === r.id ? ' open' : ''}`} onClick={() => setOpenId(openId === r.id ? null : r.id)}>
                      <td>{fmtDateTime(r.createdAt)}</td>
                      <td>
                        <b>{r.function?.slug}</b>
                        {r.app ? <span className="muted"> · {r.app.slug}</span> : null}
                        {r.agent ? <span className="muted"> · agent {r.agent.slug}</span> : null}
                        {r.depth > 0 ? <span className="pill" style={{ marginLeft: 6, fontSize: 10 }}>nested d{r.depth}</span> : null}
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>{r.caller ? shortAddr(r.caller.address) : 'anon'}</td>
                      <td><StatusPill status={r.status} /></td>
                      <td>{r.lane}</td>
                      <td style={{ textAlign: 'right' }}>{r.durationMs}</td>
                      <td style={{ textAlign: 'right' }} className="mono">{fmtAnmFull(r.priceNanm)}{r.freeTier ? ' (free)' : ''}</td>
                      <td style={{ textAlign: 'right' }} className="mono">{fmtAnmFull(r.platformFeeNanm)}</td>
                      <td style={{ textAlign: 'right' }} className="mono" >
                        <span style={{ color: BigInt(r.contributionNanm) < 0n ? 'var(--bad)' : undefined }}>{fmtAnmFull(r.contributionNanm)}</span>
                      </td>
                    </tr>
                    {openId === r.id && (
                      <tr className="cadm-detail-row">
                        <td colSpan={9}><ExecDetail id={r.id} adminFetch={adminFetch} /></td>
                      </tr>
                    )}
                  </Fragment>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
      <div className="cadm-inline" style={{ marginTop: 12, justifyContent: 'space-between' }}>
        <span className="muted" style={{ fontSize: 12.5 }}>{total === 0 ? '0 executions' : `${skip + 1}–${skip + rows.length} of ${fmtInt(total)}`}</span>
        <span className="cadm-inline">
          <button className="btn ghost" disabled={loading || skip === 0} onClick={() => load(Math.max(0, skip - TAKE))}>← Prev</button>
          <button className="btn ghost" disabled={loading || skip + rows.length >= total} onClick={() => load(skip + TAKE)}>Next →</button>
        </span>
      </div>
    </>
  );
}

// ── Apps tab ─────────────────────────────────────────────────────────────────
function AppsTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [rows, setRows] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const p = new URLSearchParams();
      if (q) p.set('q', q);
      if (status) p.set('status', status);
      const r = await adminFetch(`${API}/apps?${p}`);
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setRows(d.rows ?? []);
      setTotal(d.total ?? 0);
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch, q, status]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(appId: string, action: 'pause' | 'unpause') {
    const reason = action === 'pause' ? confirmReason('Pause this app? It stops serving immediately.') : window.prompt('Reason (optional):') ?? '';
    if (action === 'pause' && reason == null) return;
    setBusy(appId);
    setError('');
    try {
      const r = await adminFetch(`${API}/apps`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ appId, action, reason }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="panel" style={{ marginTop: 16, padding: 14 }}>
        <div className="cadm-inline">
          <input className="cadm-input" style={{ flex: 1, minWidth: 200 }} placeholder="slug · name · owner address/handle" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && load()} />
          <select className="cadm-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">any status</option>
            {['DRAFT', 'PUBLISHED', 'SUSPENDED', 'ARCHIVED'].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button className="btn" onClick={load}>Search</button>
        </div>
      </div>
      {error && <ErrBox text={error} />}
      <div className="panel" style={{ marginTop: 12, padding: 0, overflow: 'hidden' }}>
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 960 }}>
            <thead><tr><th>app</th><th>owner</th><th>status</th><th style={{ textAlign: 'right' }}>fns</th><th style={{ textAlign: 'right' }}>execs</th><th style={{ textAlign: 'right' }}>gross ANM</th><th style={{ textAlign: 'right' }}>fees ANM</th><th style={{ textAlign: 'right' }}>installs</th><th>actions</th></tr></thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={9} className="muted" style={{ padding: 24, textAlign: 'center' }}>No apps match.</td></tr>
              ) : (
                rows.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <b>{r.name}</b> <span className="muted mono" style={{ fontSize: 11.5 }}>/{r.slug}</span>
                      {r.suspendedReason && <div className="muted" style={{ fontSize: 11, whiteSpace: 'normal', maxWidth: 260 }}>{r.suspendedReason}</div>}
                    </td>
                    <td className="mono" style={{ fontSize: 12 }}>{r.owner?.handle || shortAddr(r.owner?.address)}</td>
                    <td><StatusPill status={r.status} /></td>
                    <td style={{ textAlign: 'right' }}>{r._count?.functions ?? 0}</td>
                    <td style={{ textAlign: 'right' }}>{fmtInt(r.live?.executions)}</td>
                    <td style={{ textAlign: 'right' }} className="mono">{fmtAnmFull(r.live?.grossNanm)}</td>
                    <td style={{ textAlign: 'right' }} className="mono">{fmtAnmFull(r.live?.platformFeeNanm)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtInt(r.live?.purchases)}</td>
                    <td>
                      {r.status === 'SUSPENDED' ? (
                        <button className="btn ghost" style={{ color: 'var(--good)' }} disabled={busy === r.id} onClick={() => act(r.id, 'unpause')}>Unpause</button>
                      ) : (
                        <button className="btn ghost" style={{ color: 'var(--warn)' }} disabled={busy === r.id} onClick={() => act(r.id, 'pause')}>Pause</button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>{fmtInt(total)} app(s). Execs/gross/fees are live sums over CloudExecution — not the cached counters.</p>
    </>
  );
}

// ── Developers tab ───────────────────────────────────────────────────────────
function DevelopersTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const p = new URLSearchParams();
      if (q) p.set('q', q);
      const r = await adminFetch(`${API}/developers?${p}`);
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setRows(d.rows ?? []);
      setTotal(d.total ?? 0);
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch, q]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(accountId: string, action: 'suspend' | 'unsuspend') {
    const reason =
      action === 'suspend'
        ? confirmReason('Suspend this developer? Every PUBLISHED app/function and ACTIVE agent they own goes offline.')
        : window.prompt('Reason (optional):') ?? '';
    if (action === 'suspend' && reason == null) return;
    setBusy(accountId);
    setError('');
    try {
      const r = await adminFetch(`${API}/developers`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ accountId, action, reason }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="panel" style={{ marginTop: 16, padding: 14 }}>
        <div className="cadm-inline">
          <input className="cadm-input" style={{ flex: 1, minWidth: 220 }} placeholder="anim1… address (exact) · handle · display name" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && load()} />
          <button className="btn" onClick={load}>Search</button>
        </div>
      </div>
      {error && <ErrBox text={error} />}
      <div className="panel" style={{ marginTop: 12, padding: 0, overflow: 'hidden' }}>
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 920 }}>
            <thead><tr><th>developer</th><th>joined</th><th style={{ textAlign: 'right' }}>fns</th><th style={{ textAlign: 'right' }}>apps</th><th style={{ textAlign: 'right' }}>agents</th><th style={{ textAlign: 'right' }}>execs</th><th style={{ textAlign: 'right' }}>earned ANM</th><th>founding</th><th>actions</th></tr></thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={9} className="muted" style={{ padding: 24, textAlign: 'center' }}>No developers match.</td></tr>
              ) : (
                rows.map((r) => {
                  const suspended = (r.live?.suspendedFunctions ?? 0) > 0;
                  return (
                    <tr key={r.id}>
                      <td>
                        <b>{r.handle || r.displayName || '—'}</b>
                        <div className="mono muted" style={{ fontSize: 11.5 }}>{shortAddr(r.address)}</div>
                      </td>
                      <td>{fmtDate(r.createdAt)}</td>
                      <td style={{ textAlign: 'right' }}>{r._count?.cloudFunctions ?? 0}{suspended ? <span style={{ color: 'var(--warn)' }}> ({r.live.suspendedFunctions} susp.)</span> : null}</td>
                      <td style={{ textAlign: 'right' }}>{r._count?.cloudApps ?? 0}</td>
                      <td style={{ textAlign: 'right' }}>{r._count?.cloudAgents ?? 0}</td>
                      <td style={{ textAlign: 'right' }}>{fmtInt(r.live?.executions)}</td>
                      <td style={{ textAlign: 'right' }} className="mono">{fmtAnmFull(r.live?.earnedNanm)}</td>
                      <td>{r.foundingDev ? <span className="pill" style={{ fontSize: 10.5 }}>{r.foundingDev.status}{r.foundingDev.seq ? ` #${r.foundingDev.seq}` : ''}</span> : '—'}</td>
                      <td>
                        <span className="cadm-inline">
                          <button className="btn ghost" style={{ color: 'var(--warn)' }} disabled={busy === r.id} onClick={() => act(r.id, 'suspend')}>Suspend</button>
                          {suspended && (
                            <button className="btn ghost" style={{ color: 'var(--good)' }} disabled={busy === r.id} onClick={() => act(r.id, 'unsuspend')}>Unsuspend</button>
                          )}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>{fmtInt(total)} developer account(s) owning cloud resources. Earnings are live sums of CloudExecution.developerNanm.</p>
    </>
  );
}

// ── Providers tab ────────────────────────────────────────────────────────────
function ProvidersTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [rows, setRows] = useState<any[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/providers`);
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setRows(d.rows ?? []);
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(providerId: string, action: 'suspend' | 'reactivate' | 'disable') {
    const reason = action === 'reactivate' ? window.prompt('Reason (optional):') ?? '' : confirmReason(`${action} this provider? In-flight jobs are released back to the queue.`);
    if (action !== 'reactivate' && reason == null) return;
    setBusy(providerId);
    setError('');
    try {
      const r = await adminFetch(`${API}/providers`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ providerId, action, reason }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {error && <ErrBox text={error} />}
      <div className="panel" style={{ marginTop: 16, padding: 0, overflow: 'hidden' }}>
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 960 }}>
            <thead><tr><th>provider</th><th>payout address</th><th>status</th><th>last seen</th><th style={{ textAlign: 'right' }}>done</th><th style={{ textAlign: 'right' }}>failed</th><th style={{ textAlign: 'right' }}>earned ANM (live)</th><th style={{ textAlign: 'right' }}>cache</th><th>actions</th></tr></thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={9} className="muted" style={{ padding: 24, textAlign: 'center' }}>No compute providers registered.</td></tr>
              ) : (
                rows.map((r) => (
                  <tr key={r.id}>
                    <td><b>{r.name || '—'}</b>{r.gpu ? <span className="muted"> · {r.gpu}</span> : null}<div className="muted" style={{ fontSize: 11 }}>{r.cpuCores} cores · {r.memoryMb}MB</div></td>
                    <td className="mono" style={{ fontSize: 12 }}>{shortAddr(r.address)}</td>
                    <td><StatusPill status={r.status} />{r.stale && r.status === 'ACTIVE' ? <span className="pill" style={{ marginLeft: 6, fontSize: 10, color: 'var(--warn)', borderColor: 'var(--warn)' }}>stale</span> : null}</td>
                    <td>{fmtDateTime(r.lastSeenAt)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtInt(r.jobsDone)}</td>
                    <td style={{ textAlign: 'right', color: r.jobsFailed > 0 ? 'var(--warn)' : undefined }}>{fmtInt(r.jobsFailed)}</td>
                    <td style={{ textAlign: 'right' }} className="mono">{fmtAnmFull(r.live?.earnedNanm)}</td>
                    <td style={{ textAlign: 'right' }} className="mono muted">{fmtAnmFull(r.live?.cacheEarnedNanm)}</td>
                    <td>
                      <span className="cadm-inline">
                        {r.status === 'ACTIVE' ? (
                          <>
                            <button className="btn ghost" style={{ color: 'var(--warn)' }} disabled={busy === r.id} onClick={() => act(r.id, 'suspend')}>Suspend</button>
                            <button className="btn ghost" style={{ color: 'var(--bad)' }} disabled={busy === r.id} onClick={() => act(r.id, 'disable')}>Disable</button>
                          </>
                        ) : (
                          <button className="btn ghost" style={{ color: 'var(--good)' }} disabled={busy === r.id} onClick={() => act(r.id, 'reactivate')}>Reactivate</button>
                        )}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>“Earned (live)” recomputes SUM(CloudExecution.providerNanm); “cache” is CloudProvider.earnedNanm — a divergence is a reconciliation finding.</p>
    </>
  );
}

// ── Reports tab ──────────────────────────────────────────────────────────────
function ReportsTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [status, setStatus] = useState('');
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const p = new URLSearchParams();
      if (status) p.set('status', status);
      const r = await adminFetch(`${API}/reports?${p}`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch, status]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(id: string, action: 'reviewing' | 'action' | 'dismiss') {
    const resolution = action === 'reviewing' ? '' : window.prompt(`Resolution note for "${action}" (required):`) ?? '';
    if (action !== 'reviewing' && !resolution.trim()) return;
    setBusy(id);
    setError('');
    try {
      const r = await adminFetch(`${API}/reports`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id, action, resolution }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="chips" style={{ marginTop: 16 }}>
        {['', 'OPEN', 'REVIEWING', 'ACTIONED', 'DISMISSED'].map((s) => (
          <button key={s || 'active'} className={`chip${status === s ? ' active' : ''}`} onClick={() => setStatus(s)}>{s || 'open + reviewing'}</button>
        ))}
      </div>
      {error && <ErrBox text={error} />}
      {!data ? (
        <div className="empty" style={{ marginTop: 16 }}>Loading reports…</div>
      ) : data.rows.length === 0 ? (
        <div className="empty" style={{ marginTop: 16 }}>No abuse reports in this state.</div>
      ) : (
        data.rows.map((r: any) => {
          const subject: any = data.subjects?.[`${r.subjectKind}:${r.subjectId}`];
          return (
            <div key={r.id} className="panel" style={{ marginTop: 10 }}>
              <div className="cadm-inline" style={{ justifyContent: 'space-between' }}>
                <div style={{ minWidth: 0 }}>
                  <StatusPill status={r.status} /> <b>{r.reason}</b>
                  <div className="muted" style={{ fontSize: 12, marginTop: 3 }}>
                    {r.subjectKind}: {subject ? (subject.name ?? subject.handle ?? shortAddr(subject.address)) : r.subjectId}
                    {subject?.status ? <> · subject status <StatusPill status={subject.status} /></> : null} · reported {fmtDateTime(r.createdAt)}
                  </div>
                  {r.detail && <div style={{ fontSize: 13, marginTop: 6, whiteSpace: 'pre-wrap' }}>{r.detail}</div>}
                  {r.resolution && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>resolution: {r.resolution} · by {shortAddr(r.resolvedBy)} · {fmtDateTime(r.resolvedAt)}</div>}
                </div>
                {(r.status === 'OPEN' || r.status === 'REVIEWING') && (
                  <span className="cadm-inline">
                    {r.status === 'OPEN' && <button className="btn ghost" disabled={busy === r.id} onClick={() => act(r.id, 'reviewing')}>Mark reviewing</button>}
                    <button className="btn ghost" style={{ color: 'var(--good)' }} disabled={busy === r.id} onClick={() => act(r.id, 'action')}>Actioned</button>
                    <button className="btn ghost" disabled={busy === r.id} onClick={() => act(r.id, 'dismiss')}>Dismiss</button>
                  </span>
                )}
              </div>
            </div>
          );
        })
      )}
      <p className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>Enforcement itself (pause app, suspend developer, block hash) happens in the matching tab — each is separately audited.</p>
    </>
  );
}

// ── Deployments tab ──────────────────────────────────────────────────────────
function DeploymentsTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [status, setStatus] = useState('FAILED');
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/deployments?status=${status}`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch, status]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(deploymentId: string, action: 'disable' | 'block_hash') {
    const reason = confirmReason(
      action === 'disable'
        ? 'Disable this deployment? Its endpoint stops serving until redeployed.'
        : 'Block this code hash? The exact source/artifact can NEVER deploy again (denylist), and this deployment is disabled.',
    );
    if (reason == null) return;
    setBusy(deploymentId);
    setError('');
    try {
      const r = await adminFetch(`${API}/deployments`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ deploymentId, action, reason }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="chips" style={{ marginTop: 16 }}>
        {['FAILED', 'ACTIVE', 'PAUSED', 'CONFIRMING', 'all'].map((s) => (
          <button key={s} className={`chip${status === s ? ' active' : ''}`} onClick={() => setStatus(s)}>{s.toLowerCase()}</button>
        ))}
      </div>
      {error && <ErrBox text={error} />}
      {!data ? (
        <div className="empty" style={{ marginTop: 16 }}>Loading deployments…</div>
      ) : (
        <>
          <div className="panel" style={{ marginTop: 12, padding: 0, overflow: 'hidden' }}>
            <div className="cadm-scroll">
              <table className="cadm-table" style={{ minWidth: 1000 }}>
                <thead><tr><th>when</th><th>function</th><th>owner</th><th>v</th><th>status</th><th>anchor</th><th>error</th><th>actions</th></tr></thead>
                <tbody>
                  {data.rows.length === 0 ? (
                    <tr><td colSpan={8} className="muted" style={{ padding: 24, textAlign: 'center' }}>No deployments in this state.</td></tr>
                  ) : (
                    data.rows.map((r: any) => (
                      <tr key={r.id}>
                        <td>{fmtDateTime(r.createdAt)}</td>
                        <td><b>{r.function?.slug}</b>{r.function?.status === 'SUSPENDED' ? <span className="pill" style={{ marginLeft: 6, fontSize: 10, color: 'var(--warn)', borderColor: 'var(--warn)' }}>fn suspended</span> : null}</td>
                        <td className="mono" style={{ fontSize: 12 }}>{r.function?.owner?.handle || shortAddr(r.function?.owner?.address)}</td>
                        <td>v{r.version?.version}</td>
                        <td><StatusPill status={r.status} /></td>
                        <td className="mono" style={{ fontSize: 11.5 }}>{r.anchorTxid ? `${shortHash(r.anchorTxid, 14)} @${r.anchorHeight ?? '?'} (${r.anchorConfirms} conf)` : '— unanchored'}</td>
                        <td className="muted cadm-ellipsis" style={{ maxWidth: 220 }}>{r.error ?? '—'}</td>
                        <td>
                          <span className="cadm-inline">
                            {r.status !== 'PAUSED' && <button className="btn ghost" style={{ color: 'var(--warn)' }} disabled={busy === r.id} onClick={() => act(r.id, 'disable')}>Disable</button>}
                            <button className="btn ghost" style={{ color: 'var(--bad)' }} disabled={busy === r.id} onClick={() => act(r.id, 'block_hash')}>Block hash</button>
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>{fmtInt(data.failed7d)} failed deployment(s) in the last 7 days.</p>
        </>
      )}
    </>
  );
}

// ── Denylist tab ─────────────────────────────────────────────────────────────
function DenylistTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [sha3, setSha3] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/denylist`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch]);
  useEffect(() => {
    load();
  }, [load]);

  async function post(body: Record<string, unknown>) {
    setBusy(true);
    setError('');
    try {
      const r = await adminFetch(`${API}/denylist`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await readErr(r));
      setSha3('');
      setReason('');
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="cadm-inline">
          <input className="cadm-input mono" style={{ flex: 2, minWidth: 260 }} placeholder="sha3-256 hex of the source/artifact to block" value={sha3} onChange={(e) => setSha3(e.target.value.trim().toLowerCase())} />
          <input className="cadm-input" style={{ flex: 1, minWidth: 180 }} placeholder="reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="btn" style={{ color: 'var(--bad)', borderColor: 'var(--bad)' }} disabled={busy || !/^[0-9a-f]{64}$/.test(sha3) || !reason.trim()} onClick={() => post({ sha3, action: 'add', reason })}>
            Block hash
          </button>
        </div>
      </div>
      {error && <ErrBox text={error} />}
      {!data ? (
        <div className="empty" style={{ marginTop: 16 }}>Loading denylist…</div>
      ) : data.rows.length === 0 ? (
        <div className="empty" style={{ marginTop: 16 }}>No blocked code hashes.</div>
      ) : (
        <div className="panel" style={{ marginTop: 12, padding: 0, overflow: 'hidden' }}>
          <div className="cadm-scroll">
            <table className="cadm-table" style={{ minWidth: 820 }}>
              <thead><tr><th>sha3</th><th>reason</th><th>added by</th><th>added</th><th>matching versions</th><th></th></tr></thead>
              <tbody>
                {data.rows.map((r: any) => {
                  const matches = (data.matches ?? []).filter((m: any) => m.sourceSha3 === r.sha3 || m.artifactSha3 === r.sha3);
                  return (
                    <tr key={r.sha3}>
                      <td className="mono" style={{ fontSize: 11.5 }}>{shortHash(r.sha3, 24)}</td>
                      <td style={{ whiteSpace: 'normal', maxWidth: 240 }}>{r.reason}</td>
                      <td className="mono" style={{ fontSize: 11.5 }}>{shortAddr(r.addedBy)}</td>
                      <td>{fmtDateTime(r.createdAt)}</td>
                      <td style={{ whiteSpace: 'normal' }}>
                        {matches.length === 0 ? <span className="muted">none deployed</span> : matches.map((m: any, i: number) => (
                          <span key={i} className="pill" style={{ marginRight: 4, fontSize: 10.5 }}>{m.function?.slug} v{m.version} ({m.function?.status})</span>
                        ))}
                      </td>
                      <td>
                        <button
                          className="btn ghost"
                          disabled={busy}
                          onClick={() => {
                            const why = confirmReason('Unblock this hash? The code becomes deployable again.');
                            if (why != null) post({ sha3: r.sha3, action: 'remove', reason: why });
                          }}
                        >
                          Unblock
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

// ── Audit tab ────────────────────────────────────────────────────────────────
function AuditTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [action, setAction] = useState('');
  const [rows, setRows] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const p = new URLSearchParams();
      if (action) p.set('action', action);
      const r = await adminFetch(`${API}/audit?${p}`);
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setRows(d.rows ?? []);
      setTotal(d.total ?? 0);
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch, action]);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <div className="panel" style={{ marginTop: 16, padding: 14 }}>
        <div className="cadm-inline">
          <input className="cadm-input" style={{ flex: 1, minWidth: 200 }} placeholder="action prefix (e.g. pricing. / app.pause / founding.)" value={action} onChange={(e) => setAction(e.target.value.trim())} onKeyDown={(e) => e.key === 'Enter' && load()} />
          <button className="btn" onClick={load}>Filter</button>
          <span className="muted" style={{ fontSize: 12.5 }}>{fmtInt(total)} entries</span>
        </div>
      </div>
      {error && <ErrBox text={error} />}
      <div className="panel" style={{ marginTop: 12, padding: 0, overflow: 'hidden' }}>
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 760 }}>
            <thead><tr><th>when</th><th>action</th><th>subject</th><th>actor</th><th>reason</th></tr></thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={5} className="muted" style={{ padding: 24, textAlign: 'center' }}>No audit entries.</td></tr>
              ) : (
                rows.map((r) => (
                  <Fragment key={r.id}>
                    <tr className={`cadm-row${openId === r.id ? ' open' : ''}`} onClick={() => setOpenId(openId === r.id ? null : r.id)}>
                      <td>{fmtDateTime(r.createdAt)}</td>
                      <td className="mono" style={{ fontSize: 12, fontWeight: 600 }}>{r.action}</td>
                      <td className="mono" style={{ fontSize: 12 }}>{r.subject}</td>
                      <td className="mono" style={{ fontSize: 11.5 }}>{shortAddr(r.actor)}</td>
                      <td className="muted cadm-ellipsis" style={{ maxWidth: 240 }}>{r.reason}</td>
                    </tr>
                    {openId === r.id && (
                      <tr className="cadm-detail-row">
                        <td colSpan={5}>
                          <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', padding: '6px 0' }}>
                            <div><div className="cadm-sub-h" style={{ margin: '0 0 4px' }}>Before</div><pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>{r.before}</pre></div>
                            <div><div className="cadm-sub-h" style={{ margin: '0 0 4px' }}>After</div><pre className="mono" style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>{r.after}</pre></div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// ── page ─────────────────────────────────────────────────────────────────────
const TABS = [
  ['overview', 'Overview'],
  ['executions', 'Executions'],
  ['apps', 'Apps'],
  ['developers', 'Developers'],
  ['providers', 'Providers'],
  ['reports', 'Reports'],
  ['deployments', 'Deployments'],
  ['denylist', 'Denylist'],
  ['audit', 'Audit log'],
] as const;
type Tab = (typeof TABS)[number][0];

export default function CloudAdminPage() {
  const { token, saveToken, adminFetch } = useAdmin();
  const [tab, setTab] = useState<Tab>('overview');
  const [execInitial, setExecInitial] = useState<Record<string, string>>({});

  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const t = p.get('tab');
    if (t && TABS.some(([k]) => k === t)) setTab(t as Tab);
    const init: Record<string, string> = {};
    for (const k of ['range', 'status', 'lane', 'free', 'priced', 'ai', 'negative', 'q', 'account', 'functionId', 'appId', 'agentId', 'providerId', 'exec']) {
      const v = p.get(k);
      if (v) init[k] = v;
    }
    if (Object.keys(init).length) {
      setExecInitial(init);
      if (!t) setTab('executions');
    }
  }, []);

  return (
    <div className="wrap" style={{ paddingTop: 34, paddingBottom: 60 }}>
      <h1 style={{ fontSize: 30, letterSpacing: '-0.03em', margin: 0 }}>Python Cloud — Operations</h1>
      <p className="muted" style={{ margin: '4px 0 0', fontSize: 14 }}>
        System health, executions with full traces and ledger entries, moderation and the compute fleet. Finance lives in{' '}
        <a href="/admin/profitability" style={{ color: 'var(--accent-2)' }}>/admin/profitability</a>.
      </p>

      <TokenGate token={token} saveToken={saveToken} />

      <div className="chips" style={{ marginTop: 20 }}>
        {TABS.map(([k, label]) => (
          <button key={k} className={`chip${tab === k ? ' active' : ''}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab adminFetch={adminFetch} goTo={(t) => setTab(t as Tab)} />}
      {tab === 'executions' && <ExecutionsTab adminFetch={adminFetch} initial={execInitial} />}
      {tab === 'apps' && <AppsTab adminFetch={adminFetch} />}
      {tab === 'developers' && <DevelopersTab adminFetch={adminFetch} />}
      {tab === 'providers' && <ProvidersTab adminFetch={adminFetch} />}
      {tab === 'reports' && <ReportsTab adminFetch={adminFetch} />}
      {tab === 'deployments' && <DeploymentsTab adminFetch={adminFetch} />}
      {tab === 'denylist' && <DenylistTab adminFetch={adminFetch} />}
      {tab === 'audit' && <AuditTab adminFetch={adminFetch} />}

      <AdminStyles />
    </div>
  );
}
