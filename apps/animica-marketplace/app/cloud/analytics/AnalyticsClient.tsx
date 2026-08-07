'use client';

// /cloud/analytics client: executions over time, success rate, latency and revenue — all from
// GET /api/cloud/v1/me/analytics (real day-bucketed CloudExecution aggregates), rendered as
// self-contained inline-SVG charts (components/cloud/Charts).

import { useCallback, useEffect, useState } from 'react';
import { fmtAnm } from '@/app/dev/ui';
import SeriesChart, { type ChartPoint } from '@/components/cloud/Charts';
import { api, type CloudApiError, ApiErrBox, fmtMs, fmtInt } from '@/components/cloud/ui';

interface DayRow {
  date: string;
  executions: number;
  succeeded: number;
  failed: number;
  revenueNanm: string;
  avgDurationMs: number;
}

interface FnRow {
  functionId: string;
  slug: string;
  name?: string;
  executions: number;
  succeeded: number;
  failed: number;
  revenueNanm: string;
  avgDurationMs: number;
}

interface AnalyticsDto {
  days: number;
  series: DayRow[];
  totals: {
    executions: number;
    succeeded: number;
    failed: number;
    revenueNanm: string;
    activeCallers?: number;
    avgDurationMs?: number;
  };
  functions: FnRow[];
}

const RANGES = [7, 30, 90];

function anmNumber(nanm: string): number {
  // Chart y-values only (display math, not money math): nANM -> ANM as a float.
  try { return Number(BigInt(nanm)) / 1e9; } catch { return 0; }
}

export default function AnalyticsClient({ premium, planKey }: { premium: boolean; planKey: string }) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<AnalyticsDto | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<CloudApiError | null>(null);

  const load = useCallback(async (d: number) => {
    setLoading(true);
    setError(null);
    try {
      const j = await api(`/api/cloud/v1/me/analytics?days=${d}`);
      const dto: AnalyticsDto = j?.series ? j : j?.analytics ?? j;
      setData(dto);
    } catch (e) {
      setError(e as CloudApiError);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(days); }, [days, load]);

  const series = data?.series ?? [];
  const execPts: ChartPoint[] = series.map((r) => ({ x: r.date, y: r.executions }));
  const successPts: ChartPoint[] = series.map((r) => ({
    x: r.date,
    y: r.succeeded + r.failed > 0 ? (r.succeeded / (r.succeeded + r.failed)) * 100 : 0,
  }));
  const latencyPts: ChartPoint[] = series.map((r) => ({ x: r.date, y: r.avgDurationMs }));
  const revenuePts: ChartPoint[] = series.map((r) => ({ x: r.date, y: anmNumber(r.revenueNanm) }));

  const t = data?.totals;
  const successRate = t && t.succeeded + t.failed > 0
    ? `${((t.succeeded / (t.succeeded + t.failed)) * 100).toFixed(1)}%`
    : '—';

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26, letterSpacing: '-0.03em' }}>Analytics</h1>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
            Live execution metrics across all your functions.
            {premium ? '' : ` Advanced analytics (longer retention, per-caller breakdowns) comes with Pro — you're on ${planKey}.`}
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 6 }}>
          {RANGES.map((r) => (
            <button key={r} className="chip" onClick={() => setDays(r)}
              style={days === r ? { color: 'var(--text)', borderColor: 'var(--accent)', background: 'rgba(108,92,255,0.1)' } : undefined}>
              {r}d
            </button>
          ))}
        </div>
      </div>

      <ApiErrBox error={error} />

      {loading ? (
        <div className="empty" style={{ marginTop: 24 }}>Loading analytics…</div>
      ) : !data ? (
        error ? null : <div className="empty" style={{ marginTop: 24 }}>Analytics unavailable.</div>
      ) : data.totals.executions === 0 ? (
        <div className="empty" style={{ marginTop: 24 }}>
          <div style={{ fontSize: 34, marginBottom: 8 }}>📈</div>
          <p style={{ margin: 0, color: 'var(--text-dim)' }}>
            No executions in the last {days} days. Deploy a function and share its endpoint —
            every call lands here in real time.
          </p>
          <a className="btn primary" href="/cloud/functions/new" style={{ marginTop: 12 }}>Deploy a function →</a>
        </div>
      ) : (
        <>
          <div className="cl-kpis" style={{ marginTop: 18 }}>
            <div className="cl-kpi"><b>{fmtInt(t!.executions)}</b><span>Executions ({days}d)</span></div>
            <div className="cl-kpi"><b>{successRate}</b><span>Success rate</span></div>
            <div className="cl-kpi"><b>{fmtAnm(t!.revenueNanm)}</b><span>Net revenue (ANM)</span></div>
            {t!.avgDurationMs != null && <div className="cl-kpi"><b>{fmtMs(t!.avgDurationMs)}</b><span>Avg duration</span></div>}
            {t!.activeCallers != null && <div className="cl-kpi"><b>{fmtInt(t!.activeCallers)}</b><span>Active callers</span></div>}
          </div>

          <div className="cl-grid2" style={{ marginTop: 16 }}>
            <SeriesChart title="Executions per day" kind="bar" color="var(--accent)" data={execPts}
              emptyLabel="No executions in this period." />
            <SeriesChart title="Success rate (%)" kind="line" color="var(--good)" data={successPts} yMax={100}
              fmtY={(v) => `${Math.round(v)}%`} fmtTip={(v) => `${v.toFixed(1)}%`}
              emptyLabel="No completed executions in this period." />
            <SeriesChart title="Average duration" kind="line" color="#a99bff" data={latencyPts}
              fmtY={(v) => fmtMs(v)} fmtTip={(v) => fmtMs(v)}
              emptyLabel="No timing data in this period." />
            <SeriesChart title="Net revenue (ANM)" kind="bar" color="var(--accent-2)" data={revenuePts}
              fmtY={(v) => (v >= 1 ? v.toFixed(1) : v.toFixed(3))} fmtTip={(v) => `${v.toFixed(4)} ANM`}
              emptyLabel="No revenue in this period." />
          </div>

          <div className="panel" style={{ marginTop: 16 }}>
            <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>By function ({days}d)</h3>
            {data.functions.length === 0 ? (
              <div className="empty" style={{ padding: '22px 12px', fontSize: 13 }}>No per-function activity in this period.</div>
            ) : (
              <div className="cl-scroll">
                <table className="cl-table" style={{ minWidth: 680 }}>
                  <thead>
                    <tr>
                      <th>Function</th>
                      <th style={{ textAlign: 'right' }}>Executions</th>
                      <th style={{ textAlign: 'right' }}>Succeeded</th>
                      <th style={{ textAlign: 'right' }}>Failed</th>
                      <th style={{ textAlign: 'right' }}>Avg duration</th>
                      <th style={{ textAlign: 'right' }}>Net revenue</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.functions.map((f) => (
                      <tr key={f.functionId}>
                        <td>
                          <a className="mono" style={{ fontSize: 12.5, textDecoration: 'underline' }} href={`/cloud/functions/${f.functionId}`}>
                            {f.slug}
                          </a>
                        </td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>{fmtInt(f.executions)}</td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right', color: 'var(--good)' }}>{fmtInt(f.succeeded)}</td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right', color: f.failed > 0 ? 'var(--bad)' : undefined }}>{fmtInt(f.failed)}</td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>{fmtMs(f.avgDurationMs)}</td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>{fmtAnm(f.revenueNanm)} ANM</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
