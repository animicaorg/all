'use client';

// /cloud/agents client: list, create, pause/resume, budget edit, delete. An agent is a
// capability-bounded program bound to one of the developer's functions, with an atomic
// spend budget enforced by the host broker on every run.

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { fmtAnm, anmToNanm, nanmToAnmInput } from '@/app/dev/ui';
import {
  api, type CloudApiError, ApiErrBox, OkBox, CloudStatusPill, ConfirmDialog,
  CAPABILITY_INFO, timeAgo, inputStyle, labelStyle,
} from '@/components/cloud/ui';

export interface AgentsDto {
  ownerSegment: string;
  plan: { key: string; maxAgents: number; used: number };
  functions: { id: string; slug: string; name: string; status: string }[];
  agents: {
    id: string; slug: string; name: string; description: string; status: string;
    address: string | null; capabilities: string[];
    maxSpendPerRunNanm: string; dailySpendCapNanm: string; spentTodayNanm: string; spendDayKey: string;
    lastRunAt: string | null; runsTotal: number; createdAt: string;
    function: { id: string; slug: string; name: string; status: string };
    grants: number; execCount: number; execSpendNanm: string;
  }[];
}

function pct(spent: string, cap: string): number {
  try {
    const c = BigInt(cap);
    if (c <= 0n) return 0;
    return Math.min(100, Number((BigInt(spent) * 100n) / c));
  } catch { return 0; }
}

export default function AgentsClient({ dto }: { dto: AgentsDto }) {
  const router = useRouter();
  const [apiError, setApiError] = useState<CloudApiError | null>(null);
  const [notice, setNotice] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [editing, setEditing] = useState<AgentsDto['agents'][number] | null>(null);
  const [deleting, setDeleting] = useState<AgentsDto['agents'][number] | null>(null);

  const atCap = dto.plan.maxAgents !== -1 && dto.plan.used >= dto.plan.maxAgents;

  const setStatus = useCallback(async (id: string, status: 'ACTIVE' | 'PAUSED') => {
    setApiError(null);
    setBusyId(id);
    try {
      await api(`/api/cloud/v1/agents/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      });
      setNotice(status === 'ACTIVE' ? 'Agent activated.' : 'Agent paused — it will not run until resumed.');
      router.refresh();
    } catch (e) {
      setApiError(e as CloudApiError);
    } finally {
      setBusyId('');
    }
  }, [router]);

  const doDelete = useCallback(async () => {
    if (!deleting) return;
    setApiError(null);
    setBusyId(deleting.id);
    try {
      await api(`/api/cloud/v1/agents/${encodeURIComponent(deleting.id)}`, { method: 'DELETE' });
      setNotice(`Agent ${deleting.name} deleted.`);
      setDeleting(null);
      router.refresh();
    } catch (e) {
      setApiError(e as CloudApiError);
      setDeleting(null);
    } finally {
      setBusyId('');
    }
  }, [deleting, router]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26, letterSpacing: '-0.03em' }}>Agents</h1>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
            Autonomous programs with their own Animica identity, hard spend budgets, and only the
            capabilities you grant. {dto.plan.maxAgents === -1 ? '' : `${dto.plan.used} of ${dto.plan.maxAgents} used on the ${dto.plan.key} plan.`}
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => setShowCreate((v) => !v)} disabled={atCap && !showCreate}>
          {showCreate ? 'Close' : '+ New agent'}
        </button>
      </div>

      {atCap && (
        <div className="panel" style={{ marginTop: 14, borderColor: 'var(--warn)', fontSize: 13.5 }}>
          Your {dto.plan.key} plan allows {dto.plan.maxAgents} agent{dto.plan.maxAgents === 1 ? '' : 's'}.{' '}
          <a href="/pricing" style={{ textDecoration: 'underline' }}>Upgrade for more →</a>
        </div>
      )}

      {notice && <OkBox>{notice}</OkBox>}
      <ApiErrBox error={apiError} />

      {showCreate && (
        <CreateAgent
          functions={dto.functions}
          onDone={() => { setShowCreate(false); setNotice('Agent created (paused). Set it ACTIVE when you are ready.'); router.refresh(); }}
          onError={setApiError}
        />
      )}

      {dto.agents.length === 0 && !showCreate ? (
        <div className="empty" style={{ marginTop: 24 }}>
          <div style={{ fontSize: 34, marginBottom: 8 }}>🤖</div>
          <p style={{ margin: '0 0 4px', color: 'var(--text-dim)' }}>
            No agents yet. An agent wraps one of your functions with an identity and a budget so it
            can act (and spend) autonomously — within caps you set.
          </p>
          {dto.functions.length === 0 && (
            <p className="muted" style={{ fontSize: 13 }}>
              You need a function first — <a href="/cloud/functions/new" style={{ textDecoration: 'underline' }}>create one</a>.
            </p>
          )}
        </div>
      ) : (
        <div className="grid" style={{ marginTop: 20, gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
          {dto.agents.map((a) => (
            <div key={a.id} className="card" style={{ gap: 8 }}>
              <div className="top">
                <div className="ico">🤖</div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <h3 style={{ overflowWrap: 'anywhere' }}>{a.name}</h3>
                  <div className="by mono">{a.slug}</div>
                </div>
                <CloudStatusPill status={a.status} />
              </div>

              {a.description && <p style={{ fontSize: 12.5 }}>{a.description}</p>}

              <div style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                runs <code className="inline">{a.function.slug}</code>
                {a.function.status !== 'PUBLISHED' && <span style={{ color: 'var(--warn)' }}> (function not published)</span>}
              </div>

              {a.address && (
                <div className="mono muted" style={{ fontSize: 11, overflowWrap: 'anywhere' }} title="the agent's own payable address">
                  {a.address}
                </div>
              )}

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {a.capabilities.length === 0
                  ? <span className="pill" style={{ fontSize: 10.5 }}>no capabilities</span>
                  : a.capabilities.map((c) => <span key={c} className="pill" style={{ fontSize: 10.5 }}>{c}</span>)}
              </div>

              <div style={{ fontSize: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-dim)', marginBottom: 4 }}>
                  <span>today: {fmtAnm(a.spentTodayNanm)} / {a.dailySpendCapNanm === '0' ? '0 (no spend)' : fmtAnm(a.dailySpendCapNanm)} ANM</span>
                  <span>per run ≤ {fmtAnm(a.maxSpendPerRunNanm)}</span>
                </div>
                <div className="cl-meter"><div style={{ width: `${pct(a.spentTodayNanm, a.dailySpendCapNanm)}%` }} /></div>
              </div>

              <div className="meta">
                <span>{a.execCount.toLocaleString()} executions</span>
                <span>{a.grants} caller grant{a.grants === 1 ? '' : 's'}</span>
                <span>{a.lastRunAt ? `ran ${timeAgo(a.lastRunAt)}` : 'never ran'}</span>
              </div>

              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
                {a.status === 'ACTIVE' ? (
                  <button className="btn" style={{ fontSize: 12.5, padding: '6px 12px' }} disabled={busyId === a.id}
                    onClick={() => setStatus(a.id, 'PAUSED')}>⏸ Pause</button>
                ) : a.status === 'SUSPENDED' ? (
                  <span className="pill" style={{ color: 'var(--bad)', borderColor: 'var(--bad)', fontSize: 11 }}>suspended by platform</span>
                ) : (
                  <button className="btn" style={{ fontSize: 12.5, padding: '6px 12px' }} disabled={busyId === a.id}
                    onClick={() => setStatus(a.id, 'ACTIVE')}>▶ Activate</button>
                )}
                <button className="btn ghost" style={{ fontSize: 12.5, padding: '6px 12px' }} onClick={() => setEditing(a)}>Budget…</button>
                <span style={{ flex: 1 }} />
                <button className="btn ghost" style={{ fontSize: 12.5, padding: '6px 12px', color: 'var(--bad)' }}
                  onClick={() => setDeleting(a)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <BudgetModal
          agent={editing}
          onClose={(changed) => { setEditing(null); if (changed) { setNotice('Budget updated.'); router.refresh(); } }}
          onError={setApiError}
        />
      )}

      <ConfirmDialog
        open={deleting != null}
        title={`Delete agent ${deleting?.name}?`}
        danger
        busy={busyId === deleting?.id}
        confirmLabel="Delete agent"
        requireText={deleting?.slug}
        body={<>The agent stops permanently and its caller grants become useless. Its execution history is retained for accounting. The underlying function is NOT deleted.</>}
        onConfirm={doDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

// ── create form ──────────────────────────────────────────────────────────────
function CreateAgent({
  functions, onDone, onError,
}: {
  functions: AgentsDto['functions'];
  onDone: () => void;
  onError: (e: CloudApiError | null) => void;
}) {
  const [name, setName] = useState('');
  const [functionId, setFunctionId] = useState(functions[0]?.id ?? '');
  const [description, setDescription] = useState('');
  const [caps, setCaps] = useState<string[]>([]);
  const [perRunAnm, setPerRunAnm] = useState('0.1');
  const [dailyAnm, setDailyAnm] = useState('1');
  const [busy, setBusy] = useState(false);

  let perRunOk = true; let dailyOk = true; let perRunNanm = '0'; let dailyNanm = '0';
  try { perRunNanm = perRunAnm.trim() ? anmToNanm(perRunAnm.trim()) : '0'; } catch { perRunOk = false; }
  try { dailyNanm = dailyAnm.trim() ? anmToNanm(dailyAnm.trim()) : '0'; } catch { dailyOk = false; }

  const toggleCap = (key: string) => setCaps((c) => (c.includes(key) ? c.filter((k) => k !== key) : [...c, key]));

  const create = useCallback(async () => {
    onError(null);
    setBusy(true);
    try {
      await api('/api/cloud/v1/agents', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          functionId,
          description,
          capabilities: caps,
          maxSpendPerRunNanm: perRunNanm,
          dailySpendCapNanm: dailyNanm,
        }),
      });
      onDone();
    } catch (e) {
      onError(e as CloudApiError);
    } finally {
      setBusy(false);
    }
  }, [name, functionId, description, caps, perRunNanm, dailyNanm, onDone, onError]);

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>New agent</h3>
      {functions.length === 0 ? (
        <p className="muted" style={{ fontSize: 13.5 }}>
          An agent needs a function to run. <a href="/cloud/functions/new" style={{ textDecoration: 'underline' }}>Create a function first →</a>
        </p>
      ) : (
        <div className="cl-grid2">
          <div>
            <label style={labelStyle}>Name</label>
            <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} placeholder="Market watcher" />
            <label style={{ ...labelStyle, marginTop: 12 }}>Function it runs</label>
            <select className="cl-input" value={functionId} onChange={(e) => setFunctionId(e.target.value)}>
              {functions.map((f) => (
                <option key={f.id} value={f.id}>{f.slug}{f.status !== 'PUBLISHED' ? ' (unpublished)' : ''}</option>
              ))}
            </select>
            <label style={{ ...labelStyle, marginTop: 12 }}>Description</label>
            <textarea className="cl-input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} style={{ fontFamily: 'inherit', fontSize: 13 }} />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12 }}>
              <div>
                <label style={labelStyle}>Max spend / run (ANM)</label>
                <input style={{ ...inputStyle, borderColor: perRunOk ? undefined : 'var(--bad)' }} value={perRunAnm}
                  onChange={(e) => setPerRunAnm(e.target.value)} inputMode="decimal" />
              </div>
              <div>
                <label style={labelStyle}>Daily cap (ANM)</label>
                <input style={{ ...inputStyle, borderColor: dailyOk ? undefined : 'var(--bad)' }} value={dailyAnm}
                  onChange={(e) => setDailyAnm(e.target.value)} inputMode="decimal" />
              </div>
            </div>
          </div>
          <div>
            <label style={labelStyle}>Capabilities</label>
            {CAPABILITY_INFO.map((c) => (
              <label key={c.key} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '5px 0', fontSize: 12.5, cursor: 'pointer' }}>
                <input type="checkbox" checked={caps.includes(c.key)} onChange={() => toggleCap(c.key)} style={{ width: 15, height: 15, marginTop: 2 }} />
                <span>
                  <b>{c.label}</b>
                  {c.sensitive && <span className="pill" style={{ marginLeft: 6, fontSize: 9.5, color: 'var(--warn)', borderColor: 'var(--warn)' }}>grant required</span>}
                  <span className="muted" style={{ display: 'block', fontSize: 11.5 }}>{c.blurb}</span>
                </span>
              </label>
            ))}
            <button className="btn primary" style={{ marginTop: 12 }} onClick={create}
              disabled={busy || !name.trim() || !functionId || !perRunOk || !dailyOk}>
              {busy ? 'Creating…' : 'Create agent (starts paused)'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── budget modal ─────────────────────────────────────────────────────────────
function BudgetModal({
  agent, onClose, onError,
}: {
  agent: AgentsDto['agents'][number];
  onClose: (changed: boolean) => void;
  onError: (e: CloudApiError | null) => void;
}) {
  const [perRunAnm, setPerRunAnm] = useState(nanmToAnmInput(agent.maxSpendPerRunNanm));
  const [dailyAnm, setDailyAnm] = useState(nanmToAnmInput(agent.dailySpendCapNanm));
  const [caps, setCaps] = useState<string[]>(agent.capabilities);
  const [busy, setBusy] = useState(false);
  const [localErr, setLocalErr] = useState('');

  let perRunOk = true; let dailyOk = true; let perRunNanm = '0'; let dailyNanm = '0';
  try { perRunNanm = perRunAnm.trim() ? anmToNanm(perRunAnm.trim()) : '0'; } catch { perRunOk = false; }
  try { dailyNanm = dailyAnm.trim() ? anmToNanm(dailyAnm.trim()) : '0'; } catch { dailyOk = false; }

  const toggleCap = (key: string) => setCaps((c) => (c.includes(key) ? c.filter((k) => k !== key) : [...c, key]));

  const save = useCallback(async () => {
    setBusy(true);
    setLocalErr('');
    try {
      await api(`/api/cloud/v1/agents/${encodeURIComponent(agent.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ maxSpendPerRunNanm: perRunNanm, dailySpendCapNanm: dailyNanm, capabilities: caps }),
      });
      onClose(true);
    } catch (e: any) {
      setLocalErr(e?.message ?? 'save failed');
      onError(e as CloudApiError);
    } finally {
      setBusy(false);
    }
  }, [agent.id, perRunNanm, dailyNanm, caps, onClose, onError]);

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(3,4,7,0.62)', backdropFilter: 'blur(3px)', display: 'grid', placeItems: 'center', padding: 18 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(false); }}>
      <div className="panel" role="dialog" aria-modal="true" style={{ width: 'min(460px, 96vw)', maxHeight: '92vh', overflowY: 'auto' }}>
        <h3 style={{ margin: '0 0 4px', fontSize: 16 }}>Budget &amp; capabilities — {agent.name}</h3>
        <p className="muted" style={{ fontSize: 12.5, margin: '0 0 12px' }}>
          Spent today: <b>{fmtAnm(agent.spentTodayNanm)} ANM</b>. Caps are enforced atomically on every spend.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <div>
            <label style={labelStyle}>Max spend / run (ANM)</label>
            <input style={{ ...inputStyle, borderColor: perRunOk ? undefined : 'var(--bad)' }} value={perRunAnm}
              onChange={(e) => setPerRunAnm(e.target.value)} inputMode="decimal" />
          </div>
          <div>
            <label style={labelStyle}>Daily cap (ANM)</label>
            <input style={{ ...inputStyle, borderColor: dailyOk ? undefined : 'var(--bad)' }} value={dailyAnm}
              onChange={(e) => setDailyAnm(e.target.value)} inputMode="decimal" />
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <label style={labelStyle}>Capabilities</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {CAPABILITY_INFO.map((c) => (
              <button key={c.key} className="chip" onClick={() => toggleCap(c.key)} title={c.blurb}
                style={caps.includes(c.key) ? { color: 'var(--text)', borderColor: 'var(--accent)', background: 'rgba(108,92,255,0.1)' } : undefined}>
                {caps.includes(c.key) ? '✓ ' : ''}{c.label}
              </button>
            ))}
          </div>
        </div>
        {localErr && <div style={{ color: 'var(--bad)', fontSize: 12.5, marginTop: 10 }}>{localErr}</div>}
        <div style={{ display: 'flex', gap: 10, marginTop: 16, justifyContent: 'flex-end' }}>
          <button className="btn ghost" onClick={() => onClose(false)} disabled={busy}>Cancel</button>
          <button className="btn primary" onClick={save} disabled={busy || !perRunOk || !dailyOk}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
