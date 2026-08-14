'use client';

// Client half of /cloud/functions/[id]. Data arrives fully-loaded (jsonSafe) from the server
// component; mutations go through the cloud API and re-render via router.refresh() so the page
// keeps showing DB truth, never an optimistic guess.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { fmtAnm, fmtDate, nanmToAnmInput, anmToNanm } from '@/app/dev/ui';
import DiffView from '@/components/cloud/DiffView';
import TestInvoke from '@/components/cloud/TestInvoke';
import {
  api, type CloudApiError, ApiErrBox, ErrBox, OkBox, CopyButton, CloudStatusPill, ConfirmDialog,
  CAPABILITY_INFO, timeAgo, fmtMs, inputStyle, labelStyle,
} from '@/components/cloud/ui';

export interface FunctionDetailDto {
  fn: {
    id: string; slug: string; name: string; description: string; status: string; visibility: string;
    entrypoint: string; runtime: string; timeoutMs: number; memoryMb: number; capabilities: string[];
    requiresAuth: boolean; perCallNanm: string; currentVersion: number;
    suspendedAt: string | null; suspendedReason: string | null; createdAt: string;
    app: { id: string; slug: string; name: string } | null;
  };
  ownerSegment: string;
  publicBase: string;
  anchorConfirmations: number;
  versions: {
    id: string; version: number; source: string; sourceSha3: string; artifactSha3: string;
    sizeBytes: number; entrypoint: string; packages: string[]; estimateNanm: string; createdAt: string;
  }[];
  deployments: {
    id: string; status: string; version: number; daBlobId: string | null; anchorTxid: string | null;
    anchorHeight: number | null; anchorConfirms: number; registryName: string | null;
    deployerAddress: string | null; endpoint: string | null; error: string | null; logsJson: string;
    createdAt: string; activatedAt: string | null;
  }[];
  executions: {
    id: string; requestId: string; status: string; errorCode: string | null; durationMs: number;
    cpuMs: number; priceNanm: string; developerNanm: string; freeTier: boolean; callerKind: string;
    lane: string; createdAt: string;
  }[];
  logs: { id: string; ts: string; level: string; message: string; executionId: string }[];
  stats: { execTotal: number; netNanm: string; grossNanm: string; succeeded30: number; failed30: number };
  secrets: { id: string; name: string; hint: string; createdAt: string }[];
  schedules: {
    id: string; kind: string; intervalMinutes: number | null; cron: string | null; enabled: boolean;
    nextRunAt: string | null; lastRunAt: string | null; lastStatus: string | null; runsTotal: number;
  }[];
}

type Tab = 'overview' | 'versions' | 'deployments' | 'logs' | 'settings';

function parseDeployLogs(logsJson: string): { ts?: string; level?: string; message: string }[] {
  try {
    const p = JSON.parse(logsJson);
    return Array.isArray(p) ? p : [];
  } catch { return []; }
}

function unanchoredReason(logsJson: string): string | null {
  const logs = parseDeployLogs(logsJson);
  for (let i = logs.length - 1; i >= 0; i--) {
    const m = logs[i]?.message ?? '';
    if (/anchor|DA put|unanchored/i.test(m) && (logs[i].level === 'warn' || logs[i].level === 'error')) return m;
  }
  return null;
}

export default function FunctionDetailClient({ dto }: { dto: FunctionDetailDto }) {
  const router = useRouter();
  const { fn } = dto;
  const [tab, setTab] = useState<Tab>('overview');
  const [apiError, setApiError] = useState<CloudApiError | null>(null);
  const [notice, setNotice] = useState('');

  const endpoint = `${dto.publicBase}/api/cloud/v1/fn/${dto.ownerSegment}/${fn.slug}`;

  const TABS: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'versions', label: `Versions (${dto.versions.length})` },
    { key: 'deployments', label: `Deployments (${dto.deployments.length})` },
    { key: 'logs', label: 'Logs' },
    { key: 'settings', label: 'Settings' },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <h1 className="mono" style={{ margin: 0, fontSize: 22, letterSpacing: '-0.02em', overflowWrap: 'anywhere' }}>
            {dto.ownerSegment}/{fn.slug}
          </h1>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
            <CloudStatusPill status={fn.status} />
            <CloudStatusPill status={fn.visibility} />
            {fn.suspendedAt && <CloudStatusPill status="SUSPENDED" title={fn.suspendedReason ?? undefined} />}
            <span className="muted" style={{ fontSize: 12.5 }}>v{fn.currentVersion} · {fn.runtime} · {fn.name}</span>
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <a className="btn primary" href={`/cloud/functions/new?from=${fn.id}`}>✎ Edit &amp; deploy</a>
          <a className="btn ghost" href="/cloud/functions">← All functions</a>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <code className="inline" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{endpoint}</code>
        <CopyButton text={endpoint} label="Copy" small />
      </div>

      {fn.suspendedAt && (
        <ErrBox>This function was suspended by the platform{fn.suspendedReason ? `: ${fn.suspendedReason}` : ''}. Its endpoint returns 404 until the suspension is lifted.</ErrBox>
      )}

      <nav className="cl-scroll" style={{ display: 'flex', gap: 6, marginTop: 16, overflowX: 'auto', borderBottom: '1px solid var(--border)', paddingBottom: 10 }}>
        {TABS.map((t) => (
          <button
            key={t.key}
            className="pill"
            onClick={() => setTab(t.key)}
            style={{
              fontSize: 13, padding: '8px 14px', cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0, background: tab === t.key ? 'rgba(108,92,255,0.08)' : 'transparent',
              color: tab === t.key ? 'var(--text)' : 'var(--text-dim)',
              borderColor: tab === t.key ? 'var(--accent)' : 'var(--border)',
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {notice && <OkBox>{notice}</OkBox>}
      <ApiErrBox error={apiError} />

      <div style={{ marginTop: 18 }}>
        {tab === 'overview' && <Overview dto={dto} />}
        {tab === 'versions' && (
          <Versions dto={dto} onError={setApiError} onNotice={setNotice} refresh={() => router.refresh()} />
        )}
        {tab === 'deployments' && <Deployments dto={dto} />}
        {tab === 'logs' && <Logs dto={dto} />}
        {tab === 'settings' && (
          <Settings dto={dto} onError={setApiError} onNotice={setNotice} refresh={() => router.refresh()} />
        )}
      </div>
    </div>
  );
}

// ── Overview ────────────────────────────────────────────────────────────────
function Overview({ dto }: { dto: FunctionDetailDto }) {
  const { fn, stats } = dto;
  const done30 = stats.succeeded30 + stats.failed30;
  return (
    <div>
      <div className="cl-kpis">
        <div className="cl-kpi"><b>{stats.execTotal.toLocaleString()}</b><span>Executions all-time</span></div>
        <div className="cl-kpi"><b>{fmtAnm(stats.netNanm)}</b><span>ANM earned (net)</span></div>
        <div className="cl-kpi"><b>{fmtAnm(stats.grossNanm)}</b><span>Gross billed (ANM)</span></div>
        <div className="cl-kpi">
          <b style={{ color: done30 > 0 && stats.failed30 > 0 ? 'var(--warn)' : undefined }}>
            {done30 > 0 ? `${((stats.succeeded30 / done30) * 100).toFixed(1)}%` : '—'}
          </b>
          <span>Success rate (30d)</span>
        </div>
        <div className="cl-kpi"><b>{fn.perCallNanm !== '0' ? `+${fmtAnm(fn.perCallNanm)}` : 'metered'}</b><span>Price per call</span></div>
      </div>

      <div className="cl-grid2" style={{ marginTop: 16 }}>
        <div className="panel">
          <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Test invoke</h3>
          <TestInvoke ownerSegment={dto.ownerSegment} slug={fn.slug} displayBase={dto.publicBase} />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <div className="panel">
            <h3 style={{ margin: '0 0 8px', fontSize: 15 }}>Configuration</h3>
            <table style={{ fontSize: 13, borderSpacing: 0 }}>
              <tbody>
                {[
                  ['Entrypoint', <code className="inline" key="e">{fn.entrypoint}</code>],
                  ['Timeout', `${Math.round(fn.timeoutMs / 1000)}s`],
                  ['Memory', `${fn.memoryMb} MB`],
                  ['Requires API key', fn.requiresAuth ? 'yes' : 'no'],
                  ['Capabilities', fn.capabilities.length ? fn.capabilities.join(', ') : 'none'],
                  ['App', fn.app ? fn.app.name : '—'],
                  ['Created', fmtDate(fn.createdAt)],
                ].map(([k, v], i) => (
                  <tr key={i}>
                    <td className="muted" style={{ padding: '4px 14px 4px 0', whiteSpace: 'nowrap', fontSize: 12.5 }}>{k}</td>
                    <td style={{ padding: '4px 0', overflowWrap: 'anywhere' }}>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <h3 style={{ margin: '0 0 8px', fontSize: 15 }}>Schedules &amp; secrets</h3>
            {dto.schedules.length === 0 ? (
              <p className="muted" style={{ fontSize: 12.5, margin: '0 0 8px' }}>No schedules for this function.</p>
            ) : (
              dto.schedules.map((s) => (
                <div key={s.id} style={{ fontSize: 12.5, padding: '4px 0' }}>
                  ⏱ {s.kind === 'cron' ? <code className="inline">{s.cron}</code> : `every ${s.intervalMinutes} min`}
                  {' · '}{s.enabled ? 'enabled' : 'disabled'} · {s.runsTotal} runs
                  {s.lastStatus && <> · last: {s.lastStatus}</>}
                </div>
              ))
            )}
            <div className="grad-line" />
            {dto.secrets.length === 0 ? (
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
                No function-scoped secrets. <a href="/cloud/secrets" style={{ textDecoration: 'underline' }}>Manage secrets →</a>
              </p>
            ) : (
              <>
                {dto.secrets.map((s) => (
                  <div key={s.id} className="mono" style={{ fontSize: 12.5, padding: '3px 0' }}>
                    🔒 {s.name} <span className="muted">(…{s.hint})</span>
                  </div>
                ))}
                <a href="/cloud/secrets" className="muted" style={{ fontSize: 12, textDecoration: 'underline' }}>manage →</a>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 16 }}>
        <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Executions</h3>
        {dto.executions.length === 0 ? (
          <div className="empty" style={{ padding: '24px 12px', fontSize: 13 }}>
            No executions yet — share the endpoint above, or use Test invoke.
          </div>
        ) : (
          <div className="cl-scroll">
            <table className="cl-table" style={{ minWidth: 720 }}>
              <thead>
                <tr><th>Request</th><th>Status</th><th>Caller</th><th>Lane</th><th>Duration</th><th>Billed</th><th>Your share</th><th>When</th></tr>
              </thead>
              <tbody>
                {dto.executions.map((e) => (
                  <tr key={e.id}>
                    <td className="mono" style={{ fontSize: 11.5 }}>{e.requestId.slice(0, 18)}…</td>
                    <td>
                      <CloudStatusPill status={e.status} title={e.errorCode ?? undefined} />
                    </td>
                    <td className="muted" style={{ fontSize: 12 }}>{e.freeTier ? 'free tier' : e.callerKind}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{e.lane}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{fmtMs(e.durationMs)}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{fmtAnm(e.priceNanm)}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{fmtAnm(e.developerNanm)}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{timeAgo(e.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Versions (diff + rollback) ──────────────────────────────────────────────
function Versions({
  dto, onError, onNotice, refresh,
}: {
  dto: FunctionDetailDto;
  onError: (e: CloudApiError | null) => void;
  onNotice: (s: string) => void;
  refresh: () => void;
}) {
  const { versions, fn } = dto;
  const [diffA, setDiffA] = useState<number | ''>('');
  const [diffB, setDiffB] = useState<number | ''>(versions[0]?.version ?? '');
  const [rollbackTo, setRollbackTo] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const byVersion = useMemo(() => new Map(versions.map((v) => [v.version, v])), [versions]);
  const a = diffA === '' ? null : byVersion.get(diffA) ?? null;
  const b = diffB === '' ? null : byVersion.get(diffB) ?? null;

  const doRollback = useCallback(async () => {
    if (rollbackTo == null) return;
    setBusy(true);
    onError(null);
    try {
      await api(`/api/cloud/v1/functions/${encodeURIComponent(fn.id)}/rollback`, {
        method: 'POST',
        body: JSON.stringify({ version: rollbackTo }),
      });
      onNotice(`Rolled back: version ${rollbackTo} was redeployed as the live version.`);
      setRollbackTo(null);
      refresh();
    } catch (e) {
      onError(e as CloudApiError);
      setRollbackTo(null);
    } finally {
      setBusy(false);
    }
  }, [rollbackTo, fn.id, onError, onNotice, refresh]);

  if (versions.length === 0) {
    return <div className="empty">No versions yet — deploy from the editor to create version 1.</div>;
  }

  return (
    <div>
      <div className="cl-scroll" style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--bg-card)' }}>
        <table className="cl-table" style={{ minWidth: 720 }}>
          <thead>
            <tr><th>Version</th><th>Size</th><th>Entrypoint</th><th>Source sha3-256</th><th>Created</th><th></th></tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.id}>
                <td>
                  <b>v{v.version}</b>
                  {v.version === fn.currentVersion && (
                    <span className="pill" style={{ marginLeft: 8, color: 'var(--good)', borderColor: 'var(--good)', fontSize: 10.5 }}>live</span>
                  )}
                </td>
                <td className="muted" style={{ fontSize: 12 }}>{v.sizeBytes.toLocaleString()} B</td>
                <td className="mono" style={{ fontSize: 12 }}>{v.entrypoint}</td>
                <td className="mono" style={{ fontSize: 11.5 }} title={v.sourceSha3}>{v.sourceSha3.slice(0, 16)}…</td>
                <td className="muted" style={{ fontSize: 12 }}>{fmtDate(v.createdAt)}</td>
                <td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <a className="btn ghost" style={{ fontSize: 12, padding: '5px 10px', minHeight: 30 }}
                      href={`/cloud/functions/new?from=${fn.id}&version=${v.version}`}>
                      Open in editor
                    </a>
                    {v.version !== fn.currentVersion && (
                      <button className="btn ghost" style={{ fontSize: 12, padding: '5px 10px', minHeight: 30 }}
                        onClick={() => setRollbackTo(v.version)}>
                        Roll back to
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        Versions are immutable snapshots — a rollback deploys the old source as a NEW deployment;
        history is never rewritten. Showing the latest {versions.length} versions.
      </p>

      {versions.length >= 2 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Diff</h3>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <select className="cl-input" style={{ width: 'auto', minWidth: 120 }} value={diffA} onChange={(e) => setDiffA(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">older…</option>
              {versions.map((v) => <option key={v.id} value={v.version}>v{v.version}</option>)}
            </select>
            <span className="muted">→</span>
            <select className="cl-input" style={{ width: 'auto', minWidth: 120 }} value={diffB} onChange={(e) => setDiffB(e.target.value === '' ? '' : Number(e.target.value))}>
              {versions.map((v) => <option key={v.id} value={v.version}>v{v.version}</option>)}
            </select>
          </div>
          <div style={{ marginTop: 12 }}>
            {a && b ? (
              a.version === b.version
                ? <p className="muted" style={{ fontSize: 13 }}>Pick two different versions.</p>
                : <DiffView oldLabel={`v${a.version}`} newLabel={`v${b.version}`} oldSource={a.source} newSource={b.source} />
            ) : (
              <p className="muted" style={{ fontSize: 13 }}>Pick the older version to compare against.</p>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={rollbackTo != null}
        title={`Roll back to version ${rollbackTo}?`}
        danger={false}
        busy={busy}
        confirmLabel={`Deploy v${rollbackTo}`}
        body={
          <>
            This creates a <b>new deployment</b> of version {rollbackTo}&apos;s exact source (with a fresh
            on-chain anchor when anchoring is available) and points the live endpoint at it. Version{' '}
            {fn.currentVersion} stays in history. It counts against your daily deployment quota.
          </>
        }
        onConfirm={doRollback}
        onCancel={() => setRollbackTo(null)}
      />
    </div>
  );
}

// ── Deployments (anchor truth) ──────────────────────────────────────────────
function Deployments({ dto }: { dto: FunctionDetailDto }) {
  const [live, setLive] = useState<Record<string, Partial<FunctionDetailDto['deployments'][number]>>>({});
  const [busyId, setBusyId] = useState('');
  const [err, setErr] = useState('');

  const refreshOne = useCallback(async (id: string) => {
    setBusyId(id);
    setErr('');
    try {
      const j = await api(`/api/cloud/v1/deployments/${encodeURIComponent(id)}`);
      const d = j?.deployment ?? j;
      setLive((m) => ({ ...m, [id]: {
        status: d.status, anchorTxid: d.anchorTxid, anchorHeight: d.anchorHeight,
        anchorConfirms: Number(d.anchorConfirms ?? 0), daBlobId: d.daBlobId, error: d.error,
        logsJson: typeof d.logsJson === 'string' ? d.logsJson : JSON.stringify(d.logs ?? []),
      } }));
    } catch (e: any) {
      setErr(e?.message ?? 'refresh failed');
    } finally {
      setBusyId('');
    }
  }, []);

  if (dto.deployments.length === 0) {
    return <div className="empty">No deployments yet.</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {err && <ErrBox>{err}</ErrBox>}
      {dto.deployments.map((d0) => {
        const d = { ...d0, ...live[d0.id] };
        const logs = parseDeployLogs(d.logsJson);
        const reason = unanchoredReason(d.logsJson);
        return (
          <div key={d.id} className="panel" style={{ padding: 16 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <b>v{d.version}</b>
              <CloudStatusPill status={d.status} />
              <span className="muted" style={{ fontSize: 12 }}>{fmtDate(d.createdAt)}</span>
              <span style={{ flex: 1 }} />
              <button className="btn ghost" style={{ fontSize: 12, padding: '5px 10px', minHeight: 30 }}
                onClick={() => refreshOne(d.id)} disabled={busyId === d.id}>
                {busyId === d.id ? '…' : '↻ Refresh'}
              </button>
            </div>

            {d.error && <ErrBox>{d.error}</ErrBox>}

            <div style={{ marginTop: 10, fontSize: 12.5 }}>
              {d.anchorTxid ? (
                <div className="cl-code" style={{ whiteSpace: 'normal', lineHeight: 1.8 }}>
                  ⚓ <b>Anchored on-chain</b> — a signed DEPLOY (t=1) tx binds this version&apos;s source hash,
                  artifact hash and DA blob id.
                  <br />tx <span className="mono" style={{ overflowWrap: 'anywhere' }}>{d.anchorTxid}</span>{' '}
                  <CopyButton text={d.anchorTxid} label="copy" small />
                  <br />
                  {d.anchorHeight != null ? <>height {d.anchorHeight} · </> : <>not yet included · </>}
                  {Math.min(d.anchorConfirms, dto.anchorConfirmations)}/{dto.anchorConfirmations} confirmations
                  {d.anchorConfirms >= dto.anchorConfirmations && <span style={{ color: 'var(--good)' }}> · final</span>}
                  {d.daBlobId && <><br />DA blob <span className="mono" style={{ overflowWrap: 'anywhere' }}>{d.daBlobId}</span></>}
                  {d.deployerAddress && <><br />deployer <span className="mono" style={{ overflowWrap: 'anywhere' }}>{d.deployerAddress}</span></>}
                  {d.registryName && <><br />node registry <span className="mono">{d.registryName}</span></>}
                </div>
              ) : (
                <div className="muted">
                  <b style={{ color: 'var(--warn)' }}>Not anchored.</b>{' '}
                  {reason ?? 'No anchor transaction was recorded for this deployment.'}
                  {' '}The deployment still serves traffic; anchoring binds it to the chain but is not required to run.
                </div>
              )}
            </div>

            {logs.length > 0 && (
              <details style={{ marginTop: 10 }}>
                <summary className="muted" style={{ fontSize: 12, cursor: 'pointer' }}>Deployment log ({logs.length})</summary>
                <div className="cl-loglines" style={{ marginTop: 6, maxHeight: 220, overflowY: 'auto' }}>
                  {logs.map((l, i) => (
                    <div key={i} style={{ color: l.level === 'error' ? 'var(--bad)' : l.level === 'warn' ? 'var(--warn)' : 'var(--text-dim)' }}>
                      {l.ts ? `${new Date(l.ts).toLocaleTimeString()} ` : ''}{l.message}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Logs (live tail) ────────────────────────────────────────────────────────
function Logs({ dto }: { dto: FunctionDetailDto }) {
  const [rows, setRows] = useState(dto.logs);
  const [liveOn, setLiveOn] = useState(false);
  const [pollErr, setPollErr] = useState('');
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!liveOn) {
      if (timer.current) clearInterval(timer.current);
      timer.current = null;
      return;
    }
    const poll = async () => {
      try {
        const j = await api(`/api/cloud/v1/functions/${encodeURIComponent(dto.fn.id)}/logs?take=100`);
        const incoming: FunctionDetailDto['logs'] = Array.isArray(j) ? j : j?.logs ?? [];
        setPollErr('');
        setRows((prev) => {
          const seen = new Set(prev.map((r) => r.id));
          const merged = [...incoming.filter((r) => !seen.has(r.id)), ...prev];
          merged.sort((a, b) => (a.ts < b.ts ? 1 : -1));
          return merged.slice(0, 300);
        });
      } catch (e: any) {
        setPollErr(e?.message ?? 'log poll failed');
      }
    };
    poll();
    timer.current = setInterval(poll, 4000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [liveOn, dto.fn.id]);

  return (
    <div className="panel">
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>Execution logs</h3>
        <span className="muted" style={{ fontSize: 12 }}>structured `animica.log()` lines from your code</span>
        <span style={{ flex: 1 }} />
        <button className="btn ghost" style={{ fontSize: 12.5 }} onClick={() => setLiveOn((v) => !v)}>
          {liveOn ? '⏸ Pause live tail' : '▶ Live tail'}
        </button>
      </div>
      {liveOn && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 12, color: pollErr ? 'var(--bad)' : 'var(--good)' }}>
          <span style={{ width: 7, height: 7, borderRadius: 99, background: pollErr ? 'var(--bad)' : 'var(--good)' }} />
          {pollErr ? `polling failed: ${pollErr}` : 'polling every 4s'}
        </div>
      )}
      {rows.length === 0 ? (
        <div className="empty" style={{ marginTop: 12, padding: '26px 12px', fontSize: 13 }}>
          No log lines yet. Call <code className="inline">animica.log(&quot;…&quot;)</code> in your function and invoke it.
        </div>
      ) : (
        <div className="cl-loglines" style={{ marginTop: 12, maxHeight: 480, overflowY: 'auto' }}>
          {rows.map((l) => (
            <div key={l.id}>
              <span className="muted">{new Date(l.ts).toLocaleString()} </span>
              <span style={{
                color: l.level === 'error' ? 'var(--bad)' : l.level === 'warn' ? 'var(--warn)' : 'var(--accent-2)',
                fontWeight: 600,
              }}>{l.level}</span>{' '}
              <span style={{ color: 'var(--text-dim)' }}>{l.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Settings + danger zone ──────────────────────────────────────────────────
function Settings({
  dto, onError, onNotice, refresh,
}: {
  dto: FunctionDetailDto;
  onError: (e: CloudApiError | null) => void;
  onNotice: (s: string) => void;
  refresh: () => void;
}) {
  const { fn } = dto;
  const [name, setName] = useState(fn.name);
  const [description, setDescription] = useState(fn.description);
  const [visibility, setVisibility] = useState(fn.visibility);
  const [requiresAuth, setRequiresAuth] = useState(fn.requiresAuth);
  const [priceAnm, setPriceAnm] = useState(fn.perCallNanm === '0' ? '' : nanmToAnmInput(fn.perCallNanm));
  const [timeoutS, setTimeoutS] = useState(Math.round(fn.timeoutMs / 1000));
  const [memoryMb, setMemoryMb] = useState(fn.memoryMb);
  const [caps, setCaps] = useState<string[]>(fn.capabilities);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmUnpublish, setConfirmUnpublish] = useState(false);
  const [busy, setBusy] = useState(false);

  let priceNanm = '0';
  let priceOk = true;
  try { priceNanm = priceAnm.trim() ? anmToNanm(priceAnm.trim()) : '0'; } catch { priceOk = false; }

  const save = useCallback(async () => {
    onError(null);
    setSaving(true);
    try {
      await api(`/api/cloud/v1/functions/${encodeURIComponent(fn.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name, description, visibility, requiresAuth,
          perCallNanm: priceNanm,
          timeoutMs: timeoutS * 1000,
          memoryMb,
          capabilities: caps,
        }),
      });
      onNotice('Settings saved.');
      refresh();
    } catch (e) {
      onError(e as CloudApiError);
    } finally {
      setSaving(false);
    }
  }, [fn.id, name, description, visibility, requiresAuth, priceNanm, timeoutS, memoryMb, caps, onError, onNotice, refresh]);

  const setStatus = useCallback(async (status: string) => {
    onError(null);
    setBusy(true);
    try {
      await api(`/api/cloud/v1/functions/${encodeURIComponent(fn.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      });
      onNotice(status === 'PUBLISHED' ? 'Function published — the endpoint is live.' : 'Function unpublished — the endpoint now returns 404.');
      setConfirmUnpublish(false);
      refresh();
    } catch (e) {
      onError(e as CloudApiError);
      setConfirmUnpublish(false);
    } finally {
      setBusy(false);
    }
  }, [fn.id, onError, onNotice, refresh]);

  const doDelete = useCallback(async () => {
    onError(null);
    setBusy(true);
    try {
      await api(`/api/cloud/v1/functions/${encodeURIComponent(fn.id)}`, { method: 'DELETE' });
      window.location.href = '/cloud/functions';
    } catch (e) {
      onError(e as CloudApiError);
      setConfirmDelete(false);
      setBusy(false);
    }
  }, [fn.id, onError]);

  const toggleCap = (key: string) =>
    setCaps((c) => (c.includes(key) ? c.filter((k) => k !== key) : [...c, key]));

  return (
    <div className="cl-grid2">
      <div className="panel">
        <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>Settings</h3>

        <label style={labelStyle}>Name</label>
        <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} />

        <label style={{ ...labelStyle, marginTop: 12 }}>Description</label>
        <textarea className="cl-input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} style={{ fontFamily: 'inherit', fontSize: 13 }} />

        <label style={{ ...labelStyle, marginTop: 12 }}>Visibility</label>
        <select className="cl-input" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
          <option value="PUBLIC">Public</option>
          <option value="UNLISTED">Unlisted</option>
          <option value="PRIVATE">Private</option>
        </select>

        <label style={{ ...labelStyle, marginTop: 12 }}>Price per call (ANM surcharge)</label>
        <input style={{ ...inputStyle, borderColor: priceOk ? undefined : 'var(--bad)' }} value={priceAnm}
          onChange={(e) => setPriceAnm(e.target.value)} placeholder="0 — pure metered" inputMode="decimal" />

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12 }}>
          <div>
            <label style={labelStyle}>Timeout (s)</label>
            <input type="number" style={inputStyle} value={timeoutS} onChange={(e) => setTimeoutS(Math.max(1, Number(e.target.value) || 1))} />
          </div>
          <div>
            <label style={labelStyle}>Memory (MB)</label>
            <input type="number" step={64} style={inputStyle} value={memoryMb} onChange={(e) => setMemoryMb(Math.max(64, Number(e.target.value) || 64))} />
          </div>
        </div>

        <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, fontSize: 13, cursor: 'pointer', minHeight: 40 }}>
          <input type="checkbox" checked={requiresAuth} onChange={(e) => setRequiresAuth(e.target.checked)} style={{ width: 16, height: 16 }} />
          Require an API key to call
        </label>

        <div style={{ marginTop: 8 }}>
          <label style={labelStyle}>Capabilities</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {CAPABILITY_INFO.map((c) => (
              <button key={c.key} className="chip" onClick={() => toggleCap(c.key)}
                title={c.blurb}
                style={caps.includes(c.key) ? { color: 'var(--text)', borderColor: 'var(--accent)', background: 'rgba(108,92,255,0.1)' } : undefined}>
                {caps.includes(c.key) ? '✓ ' : ''}{c.label}
              </button>
            ))}
          </div>
        </div>

        <button className="btn primary" style={{ marginTop: 16 }} onClick={save} disabled={saving || !priceOk}>
          {saving ? 'Saving…' : 'Save settings'}
        </button>
        <p className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
          Limit changes are clamped server-side to the platform ceilings and apply to NEW executions immediately.
        </p>
      </div>

      <div className="panel" style={{ borderColor: 'rgba(255,92,114,0.35)' }}>
        <h3 style={{ margin: '0 0 12px', fontSize: 15, color: 'var(--bad)' }}>Danger zone</h3>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <b style={{ fontSize: 13.5 }}>{fn.status === 'PUBLISHED' ? 'Unpublish' : 'Publish'}</b>
            <p className="muted" style={{ fontSize: 12.5, margin: '4px 0 8px' }}>
              {fn.status === 'PUBLISHED'
                ? 'Takes the endpoint offline (404) without deleting anything. Republish any time.'
                : 'Makes the endpoint live again at its existing URL.'}
            </p>
            {fn.status === 'PUBLISHED' ? (
              <button className="btn" onClick={() => setConfirmUnpublish(true)} disabled={busy}>Unpublish</button>
            ) : (
              <button className="btn" onClick={() => setStatus('PUBLISHED')} disabled={busy}>Publish</button>
            )}
          </div>

          <div className="grad-line" />

          <div>
            <b style={{ fontSize: 13.5, color: 'var(--bad)' }}>Delete this function</b>
            <p className="muted" style={{ fontSize: 12.5, margin: '4px 0 8px' }}>
              Permanently removes the function, its {dto.versions.length}+ versions and deployments.
              Execution and billing history is retained for accounting (it&apos;s money). This cannot be undone.
            </p>
            <button className="btn" style={{ borderColor: 'var(--bad)', color: 'var(--bad)' }}
              onClick={() => setConfirmDelete(true)} disabled={busy}>
              Delete function
            </button>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmUnpublish}
        title="Unpublish this function?"
        danger
        busy={busy}
        confirmLabel="Unpublish"
        body={<>Callers immediately get 404 from <code className="inline">{dto.ownerSegment}/{fn.slug}</code>. Schedules and agents that call it will start failing. Nothing is deleted.</>}
        onConfirm={() => setStatus('DRAFT')}
        onCancel={() => setConfirmUnpublish(false)}
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete this function forever?"
        danger
        busy={busy}
        confirmLabel="Delete forever"
        requireText={fn.slug}
        body={<>This permanently deletes <b>{dto.ownerSegment}/{fn.slug}</b>, all its versions and deployments. The public endpoint stops resolving immediately.</>}
        onConfirm={doDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
