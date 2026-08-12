'use client';

import { useCallback, useEffect, useState } from 'react';
import { connectAndLogin, hasWallet } from '@/components/wallet';

// The Workers dashboard: list/create/edit autonomous Workers, run history, trigger tokens
// and the kill switches. Workers are FREE for every account — there is no plan gating and
// no upsell anywhere on this page. Everything is enforced server-side (/api/mkt/v1/workers/*
// + lib/workers.ts free-tier limits); this UI just reflects it.

const API = '/api/mkt/v1';

interface ApiErr extends Error { status?: number; code?: string; details?: any }

async function api(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(`${API}${path}`, {
    credentials: 'include',
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers as any) },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = new Error(j?.error?.message || `request failed (${res.status})`) as ApiErr;
    e.status = res.status;
    e.code = j?.error?.code;
    e.details = j?.error?.details;
    throw e;
  }
  return j;
}

interface WorkerDto {
  id: string; name: string; purpose: string | null; status: string; statusReason: string | null;
  model: string; tools: string[]; webhookUrl: string | null; anmName: string | null;
  scheduleKind: string; intervalMinutes: number | null; nextRunAt: string | null; lastRunAt: string | null;
  maxRunSeconds: number; failureThreshold: number; consecutiveFailures: number; runsTotal: number;
  workspaceId: string | null; hasTriggerToken: boolean; createdAt: string;
  lastRun?: { status: string; finishedAt: string | null; durationMs: number | null; error: string | null } | null;
}

interface RunDto {
  id: string; status: string; trigger: string; attempts: number; scheduledFor: string | null;
  startedAt: string | null; finishedAt: string | null; durationMs: number | null;
  tokensIn: number | null; tokensOut: number | null; error: string | null;
  output?: string | null; logs?: Array<{ t: string; msg: string }>;
}

interface Meters { workers: { used: number; limit: number }; executions: { used: number; limit: number; period: string } }
interface FreeLimits { minIntervalMinutes: number; maxWorkersPerAccount: number }
interface ListData { workers: WorkerDto[]; meters: Meters; limits: FreeLimits; enginePaused: boolean }

const WORKER_COLOR: Record<string, string> = {
  ACTIVE: 'var(--good)', PAUSED: 'var(--warn)', RUNNING: 'var(--accent)',
  FAILED: 'var(--bad)', DISABLED: 'var(--bad)', PLAN_LIMIT: 'var(--text-faint)', // legacy rows only
};
const RUN_COLOR: Record<string, string> = {
  SUCCESS: 'var(--good)', DONE: 'var(--good)', FAILED: 'var(--bad)', RUNNING: 'var(--accent)',
  PENDING: 'var(--text-faint)', CANCELLED: 'var(--text-faint)', TIMEOUT: 'var(--bad)',
};

function Chip({ status, map }: { status?: string | null; map: Record<string, string> }) {
  if (!status) return null;
  const c = map[status] ?? 'var(--text-dim)';
  return (
    <span className="pill" style={{ color: c, borderColor: c, fontWeight: 600, fontSize: 11 }}>
      {status.replace(/_/g, ' ').toLowerCase()}
    </span>
  );
}

function fmtWhen(s: string | null | undefined): string {
  if (!s) return '—';
  try { return new Date(s).toLocaleString(); } catch { return String(s); }
}

function fmtDur(ms: number | null | undefined): string {
  if (ms == null) return '—';
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function num(n: number): string { return n.toLocaleString('en-US'); }

export default function WorkersClient() {
  const [gate, setGate] = useState<'loading' | 'out' | 'in'>('loading');
  const [data, setData] = useState<ListData | null>(null);
  const [workspaces, setWorkspaces] = useState<Array<{ id: string; name: string }>>([]);
  const [err, setErr] = useState('');
  const [busyId, setBusyId] = useState('');
  const [connectBusy, setConnectBusy] = useState(false);
  const [editing, setEditing] = useState<WorkerDto | 'new' | null>(null);
  const [runsFor, setRunsFor] = useState<WorkerDto | null>(null);
  const [tokenBox, setTokenBox] = useState<{ workerId: string; token: string } | null>(null);

  const load = useCallback(async () => {
    setErr('');
    try {
      const j = await api('/workers');
      setData(j);
      setGate('in');
    } catch (e: any) {
      if (e?.status === 401 || e?.status === 403) setGate('out');
      else { setErr(e?.message ?? 'failed to load'); setGate((g) => (g === 'in' ? 'in' : 'out')); }
    }
  }, []);

  useEffect(() => {
    load();
    api('/workspaces').then((j) => setWorkspaces(j.workspaces ?? [])).catch(() => {});
  }, [load]);

  const connect = useCallback(async () => {
    setErr(''); setConnectBusy(true);
    try {
      await connectAndLogin();
      await load();
    } catch (e: any) {
      setErr(e?.message ?? 'connect failed');
    } finally { setConnectBusy(false); }
  }, [load]);

  const action = useCallback(async (w: WorkerDto, act: string) => {
    setBusyId(w.id); setErr('');
    try {
      const j = await api(`/workers/${encodeURIComponent(w.id)}/actions`, {
        method: 'POST', body: JSON.stringify({ action: act }),
      });
      if (act === 'regen_trigger_token' && j.triggerToken) setTokenBox({ workerId: w.id, token: j.triggerToken });
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally { setBusyId(''); }
  }, [load]);

  const remove = useCallback(async (w: WorkerDto) => {
    if (!window.confirm(`Delete Worker “${w.name}”? Its configuration and run history are removed permanently.`)) return;
    setBusyId(w.id); setErr('');
    try {
      await api(`/workers/${encodeURIComponent(w.id)}`, { method: 'DELETE' });
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally { setBusyId(''); }
  }, [load]);

  const stop = useCallback((w: WorkerDto) => {
    if (!window.confirm(`Emergency Stop “${w.name}”? The Worker is disabled and all pending runs are cancelled. You can re-enable it later.`)) return;
    action(w, 'stop');
  }, [action]);

  if (gate === 'loading') {
    return <div className="wrap" style={{ paddingTop: 60 }}><div className="empty">Loading Workers…</div></div>;
  }

  // ── signed-out: explain Workers, offer sign-in — no upsell, Workers are free ─
  if (gate === 'out') {
    const installed = hasWallet();
    return (
      <div className="wrap workers-page" style={{ paddingTop: 44, paddingBottom: 60 }}>
        <Marketing />
        <div className="panel" style={{ maxWidth: 520, margin: '26px auto 0', textAlign: 'left' }}>
          <h3 style={{ fontSize: 16, margin: '0 0 8px' }}>Sign in to manage your Workers</h3>
          {installed ? (
            <button className="btn primary" onClick={connect} disabled={connectBusy}>
              {connectBusy ? 'Waiting for wallet…' : 'Connect wallet & sign in'}
            </button>
          ) : (
            <>
              <p className="muted" style={{ margin: '0 0 12px', fontSize: 13.5 }}>
                No Animica wallet detected. Install the <a className="inline" href="/browser">browser extension</a>{' '}
                or open this page in the mobile wallet&apos;s dapp browser, then retry.
              </p>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <a className="btn primary" href="/browser">Get the extension</a>
                <button className="btn" onClick={load}>I&apos;ve installed it — retry</button>
              </div>
            </>
          )}
          {err && <div className="workers-err">{err}</div>}
        </div>
        <WorkersStyle />
      </div>
    );
  }

  const d = data!;

  // ── the dashboard ──────────────────────────────────────────────────────────
  return (
    <div className="wrap workers-page" style={{ paddingTop: 36, paddingBottom: 60 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 30, letterSpacing: '-0.03em', margin: 0 }}>Workers</h1>
        <div className="muted">A normal agent responds when you ask. A Worker works on schedule. Free for every account.</div>
        <div style={{ marginLeft: 'auto' }}>
          <button className="btn primary" onClick={() => { setEditing('new'); setErr(''); }}>
            + New Worker
          </button>
        </div>
      </div>
      <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>
        {num(d.meters.workers.used)} of {num(d.meters.workers.limit)} Workers on this account
        {' · '}{num(d.meters.executions.used)} execution{d.meters.executions.used === 1 ? '' : 's'} in {d.meters.executions.period}
      </div>

      {d.enginePaused && (
        <div className="workers-warnbox" style={{ marginTop: 14 }}>
          The Worker engine is paused by the operator for maintenance. Schedules and manual runs will
          resume automatically — nothing is lost.
        </div>
      )}
      {err && <div className="workers-err" style={{ marginTop: 14 }}>{err}</div>}

      {tokenBox && <TriggerTokenBox box={tokenBox} onClose={() => setTokenBox(null)} />}

      {editing && (
        <WorkerForm
          worker={editing === 'new' ? null : editing}
          minInterval={d.limits.minIntervalMinutes}
          workspaces={workspaces}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }}
        />
      )}

      {d.workers.length === 0 ? (
        !editing && (
          <div className="panel" style={{ marginTop: 24, textAlign: 'center', padding: '34px 20px' }}>
            <div style={{ fontSize: 34 }}>🤖</div>
            <h3 style={{ fontSize: 18, margin: '10px 0 6px' }}>Create your first Worker</h3>
            <p className="muted" style={{ fontSize: 14, maxWidth: 460, margin: '0 auto 16px', lineHeight: 1.5 }}>
              Give the AI a task and a schedule — it runs automatically, posts results to your
              webhook or publishes to your .anm site, and keeps a full run history.
            </p>
            <button className="btn primary" onClick={() => { setEditing('new'); setErr(''); }}>
              + New Worker
            </button>
          </div>
        )
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 20 }}>
          {d.workers.map((w) => (
            <WorkerCard
              key={w.id}
              w={w}
              busy={busyId === w.id}
              onAction={action}
              onEdit={() => setEditing(w)}
              onRuns={() => setRunsFor(runsFor?.id === w.id ? null : w)}
              runsOpen={runsFor?.id === w.id}
              onDelete={() => remove(w)}
              onStop={() => stop(w)}
            />
          ))}
        </div>
      )}

      {runsFor && <RunsDrawer worker={runsFor} onClose={() => setRunsFor(null)} />}

      <WorkersStyle />
    </div>
  );
}

// ── marketing (signed-out only) ──────────────────────────────────────────────
function Marketing() {
  return (
    <div style={{ textAlign: 'center', maxWidth: 760, margin: '0 auto' }}>
      <div className="pill" style={{ marginBottom: 14 }}>🤖 Animica Workers · autonomous AI on a schedule · free</div>
      <h1 className="workers-hero" style={{ letterSpacing: '-0.035em', lineHeight: 1.05, margin: 0 }}>
        A normal agent responds when you ask.{' '}
        <span style={{ background: 'linear-gradient(120deg,var(--accent-2),var(--accent))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>
          A Worker works on schedule.
        </span>
      </h1>
      <p className="muted" style={{ fontSize: 16.5, marginTop: 14, lineHeight: 1.55 }}>
        Give the Animica AI a task and a schedule. Your Worker runs it automatically — hourly, daily,
        or on demand — posts results to your webhook, publishes to your .anm site, and keeps a full
        execution history. Free for every account.
      </p>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))', marginTop: 26, textAlign: 'left' }}>
        {[
          ['⏰', 'Runs without you', 'Recurring schedules, manual runs, and external trigger URLs so your own systems (CI, cron, Zapier) can fire a Worker.'],
          ['🔌', 'Does something real', 'POST results to your HTTPS webhook or publish fresh HTML straight to a .anm name you own. No unauthorized actions, ever.'],
          ['🛡️', 'Stays on rails', 'Run time caps, failure auto-disable, per-account concurrency limits, and an Emergency Stop that drains the queue instantly.'],
        ].map(([ico, h, p]) => (
          <div className="card" key={h as string}>
            <div className="top"><div className="ico">{ico}</div><div><h3>{h}</h3></div></div>
            <p>{p}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── one worker card ──────────────────────────────────────────────────────────
function WorkerCard({
  w, busy, onAction, onEdit, onRuns, runsOpen, onDelete, onStop,
}: {
  w: WorkerDto;
  busy: boolean;
  onAction: (w: WorkerDto, action: string) => void;
  onEdit: () => void;
  onRuns: () => void;
  runsOpen: boolean;
  onDelete: () => void;
  onStop: () => void;
}) {
  const schedule = w.scheduleKind === 'interval' && w.intervalMinutes
    ? `every ${w.intervalMinutes} min`
    : 'manual';
  // PLAN_LIMIT survives only on legacy rows the runner's fixup has not swept — Start heals it.
  const canStart = w.status === 'PAUSED' || w.status === 'DISABLED' || w.status === 'FAILED' || w.status === 'PLAN_LIMIT';
  const canPause = w.status === 'ACTIVE' || w.status === 'RUNNING';
  const inert = w.status === 'DISABLED' || w.status === 'PLAN_LIMIT'; // run_now would 409
  return (
    <div className="panel" style={{ padding: 18 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: 17, margin: 0 }}>{w.name}</h3>
        <Chip status={w.status} map={WORKER_COLOR} />
        <span className="pill" style={{ fontSize: 11 }}>{schedule}</span>
        <span className="pill mono" style={{ fontSize: 11 }}>{w.model}</span>
        {w.tools.map((t) => <span key={t} className="pill mono" style={{ fontSize: 10.5, color: 'var(--accent-2)' }}>{t}</span>)}
      </div>
      {w.purpose && <p className="muted" style={{ fontSize: 13.5, margin: '8px 0 0' }}>{w.purpose}</p>}
      {w.statusReason && (
        <p style={{ fontSize: 12.5, margin: '6px 0 0', color: 'var(--text-faint)', fontStyle: 'italic' }}>{w.statusReason}</p>
      )}
      <div className="workers-row" style={{ marginTop: 10 }}>
        <span><span className="k">Last run</span>{w.lastRun ? <>{fmtWhen(w.lastRun.finishedAt)} · <Chip status={w.lastRun.status} map={RUN_COLOR} /></> : fmtWhen(w.lastRunAt)}</span>
        <span><span className="k">Next run</span>{w.status === 'ACTIVE' && w.scheduleKind === 'interval' ? fmtWhen(w.nextRunAt) : '—'}</span>
        <span><span className="k">Runs</span>{num(w.runsTotal)}</span>
        {w.consecutiveFailures > 0 && (
          <span style={{ color: 'var(--warn)' }}>
            <span className="k">Failures</span>{w.consecutiveFailures} / {w.failureThreshold} consecutive
          </span>
        )}
      </div>
      {w.lastRun?.error && (
        <div className="muted mono" style={{ fontSize: 12, marginTop: 6, color: 'var(--bad)', overflowWrap: 'anywhere' }}>
          {w.lastRun.error.slice(0, 300)}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12, alignItems: 'center' }}>
        {canPause && <button className="btn workers-sm" onClick={() => onAction(w, 'pause')} disabled={busy}>Pause</button>}
        {canStart && <button className="btn workers-sm" onClick={() => onAction(w, 'start')} disabled={busy}>Start</button>}
        {!inert && <button className="btn workers-sm" onClick={() => onAction(w, 'run_now')} disabled={busy}>Run Now</button>}
        <button className="btn workers-sm" onClick={onEdit} disabled={busy}>Edit</button>
        <button className="btn workers-sm" onClick={onRuns}>{runsOpen ? 'Hide Runs' : 'View Runs'}</button>
        <button className="btn workers-sm" onClick={() => onAction(w, 'regen_trigger_token')} disabled={busy} title={w.hasTriggerToken ? 'Regenerates (and invalidates) the existing trigger token' : 'Create a trigger URL for external systems'}>
          {w.hasTriggerToken ? 'Regen Trigger URL' : 'Trigger URL'}
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn ghost workers-sm" style={{ color: 'var(--bad)' }} onClick={onDelete} disabled={busy}>Delete</button>
        {w.status !== 'DISABLED' && w.status !== 'PLAN_LIMIT' && (
          <button className="btn workers-sm workers-stop" onClick={onStop} disabled={busy}>■ Emergency Stop</button>
        )}
      </div>
    </div>
  );
}

// ── show-once trigger token box ──────────────────────────────────────────────
function TriggerTokenBox({ box, onClose }: { box: { workerId: string; token: string }; onClose: () => void }) {
  const curl = `curl -X POST https://animica.dev/api/mkt/v1/workers/${box.workerId}/trigger \\\n  -H "Authorization: Bearer ${box.token}"`;
  return (
    <div style={{ marginTop: 14, border: '1px solid var(--good)', borderRadius: 10, padding: 14, background: 'rgba(36,209,139,0.06)' }}>
      <div style={{ color: 'var(--good)', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
        Copy this trigger token now — it is shown only once and cannot be retrieved later. Anyone
        holding it can run this Worker (rate-limited to 6 triggers per minute).
      </div>
      <code className="inline mono" style={{ fontSize: 12.5, wordBreak: 'break-all', display: 'block' }}>{box.token}</code>
      <pre className="mono" style={{ fontSize: 12, background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 8, padding: 10, marginTop: 10, overflowX: 'auto' }}>{curl}</pre>
      <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
        <button className="btn workers-sm" onClick={() => navigator.clipboard?.writeText(box.token).catch(() => {})}>Copy token</button>
        <button className="btn ghost workers-sm" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

// ── create / edit form ───────────────────────────────────────────────────────
function WorkerForm({
  worker, minInterval, workspaces, onClose, onSaved,
}: {
  worker: WorkerDto | null;
  minInterval: number;
  workspaces: Array<{ id: string; name: string }>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(worker?.name ?? '');
  const [purpose, setPurpose] = useState(worker?.purpose ?? '');
  const [taskPrompt, setTaskPrompt] = useState('');
  const [scheduleKind, setScheduleKind] = useState(worker?.scheduleKind ?? 'manual');
  const [intervalMinutes, setIntervalMinutes] = useState(String(worker?.intervalMinutes ?? Math.max(minInterval, 60)));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [model, setModel] = useState(worker?.model ?? 'kimi-k3');
  const [maxRunSeconds, setMaxRunSeconds] = useState(String(worker?.maxRunSeconds ?? 120));
  const [toolWebhook, setToolWebhook] = useState(!!worker?.tools.includes('webhook'));
  const [webhookUrl, setWebhookUrl] = useState(worker?.webhookUrl ?? '');
  const [toolAnm, setToolAnm] = useState(!!worker?.tools.includes('anm_publish'));
  const [anmName, setAnmName] = useState(worker?.anmName ?? '');
  const [workspaceId, setWorkspaceId] = useState(worker?.workspaceId ?? '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // Editing loads the full record so the prompts (not part of the list DTO) are editable.
  useEffect(() => {
    if (!worker) return;
    api(`/workers/${encodeURIComponent(worker.id)}`)
      .then((j) => {
        setTaskPrompt(j.worker?.taskPrompt ?? '');
        setSystemPrompt(j.worker?.systemPrompt ?? '');
        if (j.worker?.systemPrompt) setShowAdvanced(true);
      })
      .catch(() => {});
  }, [worker]);

  const save = async () => {
    setBusy(true); setError('');
    try {
      if (!name.trim()) throw new Error('Give your Worker a name.');
      if (!taskPrompt.trim()) throw new Error('The task prompt is what your Worker actually does — it cannot be empty.');
      const tools: string[] = [];
      if (toolWebhook) tools.push('webhook');
      if (toolAnm) tools.push('anm_publish');
      const body: Record<string, unknown> = {
        name: name.trim(),
        purpose: purpose.trim(),
        taskPrompt,
        systemPrompt: systemPrompt || undefined,
        model: model.trim() || 'kimi-k3',
        maxRunSeconds: Math.max(1, Number(maxRunSeconds) || 120),
        scheduleKind,
        intervalMinutes: scheduleKind === 'interval' ? Math.max(1, Number(intervalMinutes) || minInterval) : undefined,
        tools,
        webhookUrl: toolWebhook ? webhookUrl.trim() : undefined,
        anmName: toolAnm ? anmName.trim().replace(/\.anm$/, '') : undefined,
        workspaceId: workspaceId || undefined,
      };
      if (worker) {
        await api(`/workers/${encodeURIComponent(worker.id)}`, { method: 'PATCH', body: JSON.stringify(body) });
      } else {
        await api('/workers', { method: 'POST', body: JSON.stringify(body) });
      }
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <div className="panel" style={{ marginTop: 16, borderColor: 'var(--border-bright)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <h3 style={{ fontSize: 17, margin: 0, flex: 1 }}>{worker ? `Edit ${worker.name}` : 'New Worker'}</h3>
        <button className="btn ghost workers-sm" onClick={onClose} disabled={busy} aria-label="Close">✕</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: '0 16px' }}>
        <div className="workers-field">
          <label>Name *</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Daily market brief" />
        </div>
        <div className="workers-field">
          <label>Purpose <span className="muted">(shown on the card)</span></label>
          <input value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="Summarize ANM market activity every morning" />
        </div>
      </div>

      <div className="workers-field">
        <label>Task prompt * <span className="muted">(what the Worker does on every run)</span></label>
        <textarea rows={4} value={taskPrompt} onChange={(e) => setTaskPrompt(e.target.value)}
          placeholder="Write a 5-bullet summary of today's Animica network activity for my newsletter…" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: '0 16px' }}>
        <div className="workers-field">
          <label>Schedule</label>
          <select value={scheduleKind} onChange={(e) => setScheduleKind(e.target.value)}>
            <option value="manual">Manual — runs only when I say so</option>
            <option value="interval">Recurring — every N minutes</option>
          </select>
        </div>
        {scheduleKind === 'interval' && (
          <div className="workers-field">
            <label>Every N minutes <span className="muted">(minimum: {minInterval} min)</span></label>
            <input type="number" min={minInterval} value={intervalMinutes} onChange={(e) => setIntervalMinutes(e.target.value)} />
          </div>
        )}
      </div>

      <button className="btn ghost workers-sm" style={{ marginTop: 12 }} onClick={() => setShowAdvanced((v) => !v)}>
        {showAdvanced ? '▾ Hide advanced' : '▸ Advanced (system prompt, model, tools…)'}
      </button>

      {showAdvanced && (
        <div style={{ marginTop: 4 }}>
          <div className="workers-field">
            <label>System prompt <span className="muted">(optional persona / rules)</span></label>
            <textarea rows={3} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: '0 16px' }}>
            <div className="workers-field">
              <label>Model</label>
              <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="kimi-k3" />
            </div>
            <div className="workers-field">
              <label>Max run seconds</label>
              <input type="number" min={10} value={maxRunSeconds} onChange={(e) => setMaxRunSeconds(e.target.value)} />
            </div>
            {workspaces.length > 0 && (
              <div className="workers-field">
                <label>Workspace</label>
                <select value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)}>
                  <option value="">— personal —</option>
                  {workspaces.map((ws) => <option key={ws.id} value={ws.id}>{ws.name}</option>)}
                </select>
              </div>
            )}
          </div>

          <div className="workers-field">
            <label>Tools <span className="muted">(only actions on things YOU own)</span></label>
            <label className="workers-check">
              <input type="checkbox" checked={toolWebhook} onChange={(e) => setToolWebhook(e.target.checked)} />
              <span>POST the result to my webhook</span>
            </label>
            {toolWebhook && (
              <input style={{ marginTop: 6 }} value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)}
                placeholder="https://example.com/hooks/animica" />
            )}
            <label className="workers-check" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={toolAnm} onChange={(e) => setToolAnm(e.target.checked)} />
              <span>Publish the result to my .anm site</span>
            </label>
            {toolAnm && (
              <input style={{ marginTop: 6 }} value={anmName} onChange={(e) => setAnmName(e.target.value)}
                placeholder="mysite (a .anm name you own)" />
            )}
          </div>
        </div>
      )}

      {error && <div className="workers-err">{error}</div>}
      <div style={{ display: 'flex', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
        <button className="btn primary" onClick={save} disabled={busy}>
          {busy ? 'Saving…' : worker ? 'Save changes' : 'Create Worker'}
        </button>
        <button className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}

// ── run history drawer ───────────────────────────────────────────────────────
function RunsDrawer({ worker, onClose }: { worker: WorkerDto; onClose: () => void }) {
  const [runs, setRuns] = useState<RunDto[] | null>(null);
  const [detail, setDetail] = useState<RunDto | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    setRuns(null); setDetail(null); setErr('');
    api(`/workers/${encodeURIComponent(worker.id)}/runs?take=50`)
      .then((j) => setRuns(j.runs ?? []))
      .catch((e: any) => setErr(e?.message ?? 'failed to load runs'));
  }, [worker.id]);

  const openRun = (r: RunDto) => {
    setDetail(null);
    api(`/workers/${encodeURIComponent(worker.id)}/runs/${encodeURIComponent(r.id)}`)
      .then((j) => setDetail(j.run ?? r))
      .catch((e: any) => setErr(e?.message ?? 'failed to load run'));
  };

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <h3 style={{ fontSize: 16, margin: 0, flex: 1 }}>Runs — {worker.name}</h3>
        <button className="btn ghost workers-sm" onClick={onClose}>✕ Close</button>
      </div>
      {err && <div className="workers-err">{err}</div>}
      {runs == null ? (
        <div className="empty" style={{ marginTop: 12 }}>Loading runs…</div>
      ) : runs.length === 0 ? (
        <div className="empty" style={{ marginTop: 12 }}>No runs yet — hit Run Now or wait for the schedule.</div>
      ) : (
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table className="workers-table">
            <thead>
              <tr><th>Status</th><th>Trigger</th><th>Started</th><th>Duration</th><th>Tokens</th><th>Error</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} onClick={() => openRun(r)} style={{ cursor: 'pointer' }} title="View output & logs">
                  <td><Chip status={r.status} map={RUN_COLOR} /></td>
                  <td className="mono" style={{ fontSize: 12 }}>{r.trigger}</td>
                  <td>{fmtWhen(r.startedAt ?? r.scheduledFor)}</td>
                  <td>{fmtDur(r.durationMs)}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{r.tokensIn != null ? `${num(r.tokensIn)} → ${num(r.tokensOut ?? 0)}` : '—'}</td>
                  <td style={{ color: 'var(--bad)', fontSize: 12, maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.error ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail && (
        <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <b style={{ fontSize: 14 }}>Run <span className="mono" style={{ fontSize: 12 }}>{detail.id}</span></b>
            <Chip status={detail.status} map={RUN_COLOR} />
            <span className="muted" style={{ fontSize: 12.5 }}>
              {fmtWhen(detail.startedAt)} · {fmtDur(detail.durationMs)} · attempt {detail.attempts}
            </span>
          </div>
          {detail.error && <div className="workers-err">{detail.error}</div>}
          {detail.output != null && detail.output !== '' && (
            <>
              <div className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>Output</div>
              <pre className="workers-pre">{detail.output}</pre>
            </>
          )}
          {(detail.logs?.length ?? 0) > 0 && (
            <>
              <div className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>Logs</div>
              <pre className="workers-pre">
                {detail.logs!.map((l) => `${fmtWhen(l.t)}  ${l.msg}`).join('\n')}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function WorkersStyle() {
  return (
    <style>{`
      /* globals.css has no button reset; without this, UA styles paint button text black on dark. */
      .workers-page button { font-family: var(--font); color: var(--text); }
      .workers-page button:disabled { opacity: 0.6; cursor: default; }
      /* Tap targets: every control on this page must be comfortably tappable (>= 40px). */
      .workers-page .btn { min-height: 40px; }
      .workers-sm { font-size: 13px; padding: 8px 12px; min-height: 40px; }
      .workers-stop { border-color: rgba(255,92,114,0.5); color: var(--bad) !important; }
      .workers-hero { font-size: 44px; }
      @media (max-width: 640px) { .workers-hero { font-size: 32px; } }
      .workers-err { border: 1px solid rgba(255,92,114,0.45); background: rgba(255,92,114,0.08); color: var(--bad);
        border-radius: 10px; padding: 10px 12px; font-size: 13.5px; margin-top: 12px; overflow-wrap: anywhere; }
      .workers-warnbox { border: 1px solid rgba(255,180,84,0.45); background: rgba(255,180,84,0.07);
        border-radius: 10px; padding: 12px 14px; font-size: 13.5px; line-height: 1.5; }
      .workers-row { display: flex; gap: 8px 20px; flex-wrap: wrap; font-size: 13px; align-items: baseline; }
      .workers-row .k { color: var(--text-faint); margin-right: 5px; }
      .workers-field { display: flex; flex-direction: column; gap: 5px; margin-top: 12px; }
      .workers-field label { font-size: 12.5px; color: var(--text-faint); }
      .workers-field input, .workers-field textarea, .workers-field select {
        background: var(--bg-elev); border: 1px solid var(--border-bright); border-radius: 8px;
        color: var(--text); padding: 9px 11px; font-size: 14px; font-family: var(--font); outline: none;
        min-height: 40px; max-width: 100%;
      }
      .workers-field textarea { font-family: var(--font); resize: vertical; }
      .workers-field input:focus, .workers-field textarea:focus, .workers-field select:focus { border-color: var(--accent); }
      .workers-check { display: flex; align-items: center; gap: 8px; font-size: 13.5px; color: var(--text); cursor: pointer; min-height: 40px; }
      .workers-check input[type="checkbox"] { width: 18px; height: 18px; }
      /* Wide content (run table, curl example) scrolls inside its own container; the page never scrolls sideways. */
      .workers-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 640px; }
      .workers-table th { text-align: left; color: var(--text-faint); font-size: 12px; font-weight: 600;
        padding: 9px 12px; border-bottom: 1px solid var(--border); }
      .workers-table td { padding: 9px 12px; border-bottom: 1px solid var(--border); }
      .workers-table tbody tr:hover td { background: rgba(108,92,255,0.05); }
      .workers-pre { background: var(--bg-elev); border: 1px solid var(--border); border-radius: 8px;
        padding: 10px 12px; font-family: var(--mono); font-size: 12.5px; line-height: 1.55;
        white-space: pre-wrap; overflow-wrap: anywhere; max-height: 380px; overflow-y: auto; margin: 4px 0 0; }
    `}</style>
  );
}
