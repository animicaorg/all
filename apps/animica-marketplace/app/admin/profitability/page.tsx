'use client';
import { useCallback, useEffect, useState } from 'react';
import {
  AdminStyles,
  ErrBox,
  StatusPill,
  TokenGate,
  useAdmin,
  readErr,
  fmtAnmFull,
  fmtBps,
  fmtDate,
  fmtDateTime,
  fmtInt,
  fmtUsd,
  shortAddr,
  usdEq,
  type AdminFetch,
} from '../cloud/kit';

// /admin/profitability — the money dashboard (§74-§84, §90-§95).
//
// Every figure comes from /api/cloud/v1/admin/finance, which computes from authoritative rows
// (CloudExecution, CloudAppPurchase, BillingPayment, PlanSubscription) — never cached counters.
// Every figure is drillable: execution-derived numbers link into the /admin/cloud executions
// browser with the matching filters; payment/purchase/subscription numbers open an inline
// drill-down of the underlying rows. §80 is enforced in the copy: gross transaction volume is
// explicitly NOT revenue. When something cannot be computed (no price feed, no revenue), the
// dashboard says so — it never renders a fabricated zero.

const API = '/api/cloud/v1/admin';
const RANGES = ['today', '24h', '7d', '30d', 'mtd', '90d', 'all'] as const;
type RangeKey = (typeof RANGES)[number];

type Drill = { kind: 'payments' | 'purchases' | 'subscriptions' | 'charges' | 'credits'; title: string } | null;

function execHref(range: RangeKey, params: Record<string, string> = {}): string {
  const p = new URLSearchParams({ tab: 'executions', range, ...params });
  return `/admin/cloud?${p.toString()}`;
}

function Kpi({
  value,
  label,
  sub,
  href,
  onClick,
  color,
}: {
  value: string;
  label: string;
  sub?: string | null;
  href?: string;
  onClick?: () => void;
  color?: string;
}) {
  const inner = (
    <>
      <span className="v" style={color ? { color } : undefined}>{value}</span>
      <span className="l">{label}</span>
      {sub ? <span className="s">{sub}</span> : null}
    </>
  );
  if (href) {
    return (
      <a className="cadm-kpi" href={href} title="Open the underlying rows">
        {inner}
      </a>
    );
  }
  if (onClick) {
    return (
      <button className="cadm-kpi" onClick={onClick} title="Open the underlying rows">
        {inner}
      </button>
    );
  }
  return <div className="cadm-kpi">{inner}</div>;
}

function SectionH({ title, sub }: { title: string; sub: string }) {
  return (
    <div style={{ margin: '30px 0 12px' }}>
      <h2 style={{ fontSize: 20, letterSpacing: '-0.02em', margin: 0 }}>{title}</h2>
      <p className="muted" style={{ fontSize: 13, margin: '3px 0 0' }}>{sub}</p>
    </div>
  );
}

// ── drill-down drawer (payments / purchases / subscriptions / charges / credits) ──
function DrillDrawer({
  drill,
  range,
  adminFetch,
  usdMicros,
  onClose,
}: {
  drill: Drill;
  range: RangeKey;
  adminFetch: AdminFetch;
  usdMicros: string | null;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!drill) return;
    setLoading(true);
    setError('');
    setRows([]);
    (async () => {
      try {
        const r = await adminFetch(`${API}/finance/drill?kind=${drill.kind}&range=${range}&take=100`);
        if (!r.ok) throw new Error(await readErr(r));
        const d = await r.json();
        setRows(d.rows ?? []);
        setTotal(d.total ?? 0);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, [drill, range, adminFetch]);

  if (!drill) return null;
  return (
    <div className="panel" style={{ marginTop: 16, borderColor: 'var(--accent)' }}>
      <div className="cadm-inline" style={{ justifyContent: 'space-between' }}>
        <b>{drill.title} — underlying rows ({fmtInt(total)} in range)</b>
        <button className="btn ghost" onClick={onClose}>Close</button>
      </div>
      {error && <ErrBox text={error} />}
      {loading ? (
        <div className="muted" style={{ marginTop: 10 }}>Loading…</div>
      ) : rows.length === 0 ? (
        <div className="empty" style={{ marginTop: 12 }}>No rows in this range.</div>
      ) : (
        <div className="cadm-scroll" style={{ marginTop: 10 }}>
          <table className="cadm-table" style={{ minWidth: 760 }}>
            {drill.kind === 'payments' && (
              <>
                <thead><tr><th>when</th><th>account</th><th>kind</th><th>plan</th><th style={{ textAlign: 'right' }}>amount</th><th>status</th><th>capture</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>{fmtDateTime(r.occurredAt)}</td>
                      <td className="mono" style={{ fontSize: 12 }}>{shortAddr(r.account?.address)}</td>
                      <td>{r.kind}</td>
                      <td>{r.planKey ?? '—'}</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmtUsd(r.amountCents)}</td>
                      <td><StatusPill status={r.status} /></td>
                      <td className="mono" style={{ fontSize: 11.5 }}>{r.paypalCaptureId}</td>
                    </tr>
                  ))}
                </tbody>
              </>
            )}
            {drill.kind === 'purchases' && (
              <>
                <thead><tr><th>when</th><th>app</th><th>buyer</th><th>kind</th><th style={{ textAlign: 'right' }}>amount ANM</th><th style={{ textAlign: 'right' }}>fee ANM</th><th>status</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>{fmtDateTime(r.createdAt)}</td>
                      <td>{r.app?.name ?? r.appId}</td>
                      <td className="mono" style={{ fontSize: 12 }}>{shortAddr(r.account?.address)}</td>
                      <td>{r.kind}</td>
                      <td style={{ textAlign: 'right' }}>{fmtAnmFull(r.amountNanm)}</td>
                      <td style={{ textAlign: 'right' }}>{fmtAnmFull(r.platformFeeNanm)}</td>
                      <td><StatusPill status={r.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </>
            )}
            {drill.kind === 'subscriptions' && (
              <>
                <thead><tr><th>account</th><th>plan</th><th>status</th><th style={{ textAlign: 'right' }}>$/mo</th><th>created</th><th>period end</th><th>canceled</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td className="mono" style={{ fontSize: 12 }}>{shortAddr(r.account?.address)}</td>
                      <td>{r.planKey}</td>
                      <td><StatusPill status={r.status} /></td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmtUsd(r.priceUsdCents)}</td>
                      <td>{fmtDate(r.createdAt)}</td>
                      <td>{fmtDate(r.currentPeriodEnd)}</td>
                      <td>{fmtDate(r.canceledAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </>
            )}
            {drill.kind === 'charges' && (
              <>
                <thead><tr><th>account</th><th>period</th><th>feature</th><th style={{ textAlign: 'right' }}>units</th><th style={{ textAlign: 'right' }}>amount</th><th>asset</th><th>status</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td className="mono" style={{ fontSize: 12 }}>{shortAddr(r.account?.address)}</td>
                      <td>{r.period}</td>
                      <td>{r.feature}</td>
                      <td style={{ textAlign: 'right' }}>{fmtInt(r.units)}</td>
                      <td style={{ textAlign: 'right' }}>{r.asset === 'USD' ? fmtUsd(r.amountCents) : `${fmtAnmFull(r.amountNanm)} ANM`}</td>
                      <td>{r.asset}</td>
                      <td><StatusPill status={r.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </>
            )}
            {drill.kind === 'credits' && (
              <>
                <thead><tr><th>when</th><th>account</th><th>source</th><th style={{ textAlign: 'right' }}>granted ANM</th><th style={{ textAlign: 'right' }}>used ANM</th><th>expires</th><th>revoked</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>{fmtDateTime(r.createdAt)}</td>
                      <td className="mono" style={{ fontSize: 12 }}>{shortAddr(r.account?.address)}</td>
                      <td>{r.source}</td>
                      <td style={{ textAlign: 'right' }}>{fmtAnmFull(r.grantedNanm)}</td>
                      <td style={{ textAlign: 'right' }}>{fmtAnmFull(r.usedNanm)}</td>
                      <td>{fmtDate(r.expiresAt)}</td>
                      <td>{fmtDate(r.revokedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </>
            )}
          </table>
        </div>
      )}
      {usdMicros == null && drill.kind === 'purchases' && (
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>ANM amounts shown without USD equivalents — no fresh price reference.</p>
      )}
    </div>
  );
}

// ── Dashboard tab ────────────────────────────────────────────────────────────
function DashboardTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [range, setRange] = useState<RangeKey>('30d');
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [drill, setDrill] = useState<Drill>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/finance?range=${range}`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) {
      setError(e.message);
      setData(null);
    }
  }, [adminFetch, range]);
  useEffect(() => {
    load();
  }, [load]);

  const usdMicros: string | null = data?.anmUsd?.usdMicros ?? null;
  const eq = (nanm: any) => usdEq(nanm, usdMicros);
  const rev = data?.revenue;
  const cogs = data?.cogs;
  const profit = data?.profit;
  const unit = data?.unit;
  const usd = data?.usd;
  const free = data?.freeTier;
  const freeMtd = data?.freeTierMtd;
  const fnl = data?.funnel;
  const products: any[] = data?.products ?? [];
  const alerts = data?.alerts;
  const recon: any[] = data?.reconciliation ?? [];

  return (
    <>
      <div className="chips" style={{ marginTop: 18 }}>
        {RANGES.map((k) => (
          <button key={k} className={`chip${range === k ? ' active' : ''}`} onClick={() => setRange(k)}>
            {k}
          </button>
        ))}
      </div>
      {error && <ErrBox text={error} />}
      {!data && !error && <div className="empty" style={{ marginTop: 16 }}>Loading finance data…</div>}
      {data && (
        <>
          <p className="muted" style={{ fontSize: 12.5, margin: '4px 0 0' }}>
            {data.range.label} · pricing policy v{data.policy.version} · platform fee {fmtBps(data.policy.platformFeeBps)} ·{' '}
            {data.anmUsd
              ? `ANM/USD reference ${(Number(data.anmUsd.usdMicros) / 1_000_000).toFixed(8)} (${data.anmUsd.source}, ${fmtDateTime(data.anmUsd.observedAt)})`
              : 'USD equivalents unavailable — no fresh ANM/USD reference (nothing is invented)'}
          </p>

          {/* alerts + reconciliation status — prominent, above the numbers */}
          {alerts?.openCount > 0 && (
            <div className="cadm-warnbox">
              <b>{alerts.openCount} unresolved finance alert{alerts.openCount === 1 ? '' : 's'}</b> — see the Alerts tab.{' '}
              {alerts.rows.slice(0, 3).map((a: any) => (
                <span key={a.id} style={{ display: 'block', marginTop: 3 }}>
                  [{a.severity}] {a.title}
                </span>
              ))}
            </div>
          )}
          {recon.length > 0 ? (
            recon.some((r) => !r.ok) ? (
              <div className="cadm-err" style={{ marginTop: 10 }}>
                <b>Reconciliation MISMATCH:</b>{' '}
                {recon.filter((r) => !r.ok).map((r) => `${r.scope} (${r.day}, Δ ${r.deltaAbs})`).join(' · ')} — investigate before
                trusting these figures.
              </div>
            ) : (
              <div className="cadm-okbox">
                Reconciliation clean: {recon.map((r) => `${r.scope} ✓ ${r.day}`).join(' · ')}
              </div>
            )
          ) : (
            <div className="cadm-warnbox">No reconciliation reports yet — the animica-cloud-reconcile worker has not run.</div>
          )}

          <SectionH
            title="Revenue"
            sub="Animica platform revenue = fees Animica actually earned. Gross transaction volume is total customer spend flowing through the platform — it is NOT company revenue."
          />
          <div className="cadm-kpis">
            <Kpi
              value={`${fmtAnmFull(rev.platformRevenueNanm)} ANM`}
              label="Animica platform revenue"
              sub={eq(rev.platformRevenueNanm) ?? 'execution + marketplace fees'}
              color="var(--good)"
              href={execHref(range, { priced: '1' })}
            />
            <Kpi
              value={`${fmtAnmFull(rev.grossVolumeNanm)} ANM`}
              label="Gross transaction volume (not revenue)"
              sub={eq(rev.grossVolumeNanm) ?? 'total customer spend'}
              href={execHref(range, { priced: '1' })}
            />
            <Kpi
              value={`${fmtAnmFull(rev.executionRevenueNanm)} ANM`}
              label="Execution revenue (fees)"
              sub={`${fmtInt(rev.pricedExecutions)} priced of ${fmtInt(rev.executions)} executions`}
              href={execHref(range, { priced: '1' })}
            />
            <Kpi
              value={`${fmtAnmFull(rev.marketplaceRevenueNanm)} ANM`}
              label="Marketplace revenue (fees)"
              sub={`${fmtInt(rev.purchases)} purchases`}
              onClick={() => setDrill({ kind: 'purchases', title: 'Marketplace purchases' })}
            />
            <Kpi
              value={`${fmtAnmFull(rev.aiRevenueNanm)} ANM`}
              label="AI revenue (fees on AI executions)"
              sub={`${fmtInt(rev.aiExecutions)} AI-consuming executions`}
              href={execHref(range, { ai: '1' })}
            />
            <Kpi
              value={`${fmtAnmFull(rev.developerPayoutsNanm)} ANM`}
              label="Developer payouts"
              sub="paid out, not Animica revenue"
              href={execHref(range, { priced: '1' })}
            />
            <Kpi
              value={`${fmtAnmFull(rev.providerPayoutsNanm)} ANM`}
              label="Provider payouts"
              sub="fleet compute share"
              href={execHref(range, { lane: 'fleet' })}
            />
          </div>

          <SectionH title="Costs (COGS)" sub="What serving actually cost Animica, from per-execution cost accounting. Never shown to customers." />
          <div className="cadm-kpis">
            <Kpi value={`${fmtAnmFull(cogs.totalNanm)} ANM`} label="Total COGS" sub={eq(cogs.totalNanm)} color="var(--warn)" href={execHref(range)} />
            <Kpi value={`${fmtAnmFull(cogs.computeNanm)} ANM`} label="Compute COGS" sub="CPU + memory" href={execHref(range)} />
            <Kpi value={`${fmtAnmFull(cogs.aiNanm)} ANM`} label="AI COGS" sub="inference tokens" href={execHref(range, { ai: '1' })} />
            <Kpi value={`${fmtAnmFull(cogs.infraNanm)} ANM`} label="Infra allocation" sub="per-call fixed + egress" href={execHref(range)} />
            <Kpi
              value={`${fmtAnmFull(cogs.promoNanm)} ANM`}
              label="Promo / credit cost"
              sub="credit-funded execution absorbed by the platform"
              onClick={() => setDrill({ kind: 'credits', title: 'Promotional credit grants' })}
            />
            <Kpi
              value={`${fmtAnmFull(cogs.freeTierNanm)} ANM`}
              label="Free-tier cost"
              sub={`${fmtInt(cogs.freeTierExecutions)} free executions in range`}
              href={execHref(range, { free: '1' })}
            />
          </div>

          <SectionH title="Profitability" sub="Gross profit = platform revenue − ALL COGS (incl. free tier). Contribution = revenue-generating work only (free-tier COGS is acquisition spend)." />
          <div className="cadm-kpis">
            <Kpi
              value={`${fmtAnmFull(profit.grossProfitNanm)} ANM`}
              label="Gross profit"
              sub={eq(profit.grossProfitNanm)}
              color={BigInt(profit.grossProfitNanm) >= 0n ? 'var(--good)' : 'var(--bad)'}
              href={execHref(range)}
            />
            <Kpi
              value={profit.grossMarginBps == null ? 'n/a' : fmtBps(profit.grossMarginBps)}
              label="Gross margin"
              sub={profit.grossMarginBps == null ? 'no platform revenue in range' : `target ${fmtBps(data.policy.targetMarginBps)}`}
              color={profit.grossMarginBps != null && profit.grossMarginBps < data.policy.targetMarginBps ? 'var(--warn)' : undefined}
            />
            <Kpi
              value={`${fmtAnmFull(profit.contributionNanm)} ANM`}
              label="Contribution profit"
              sub={eq(profit.contributionNanm)}
              href={execHref(range, { priced: '1' })}
            />
            <Kpi
              value={profit.contributionMarginBps == null ? 'n/a' : fmtBps(profit.contributionMarginBps)}
              label="Contribution margin"
              sub={profit.contributionMarginBps == null ? 'no platform revenue in range' : undefined}
            />
            <Kpi
              value={fmtInt(profit.negativeMarginExecutions)}
              label="Money-losing executions"
              sub="priced executions with negative contribution"
              color={Number(profit.negativeMarginExecutions) > 0 ? 'var(--bad)' : 'var(--good)'}
              href={execHref(range, { negative: '1' })}
            />
          </div>

          <SectionH title="Free tier — this month" sub="The real cost of free usage (§78), always month-to-date regardless of the selected range." />
          <div className="cadm-kpis">
            <Kpi
              value={`${fmtAnmFull(freeMtd.costNanm)} ANM`}
              label="Free-tier cost this month"
              sub={eq(freeMtd.costNanm) ?? `${fmtInt(freeMtd.executions)} executions`}
              color="var(--warn)"
              href={execHref('mtd', { free: '1' })}
            />
            <Kpi
              value={freeMtd.costPerFreeUserNanm == null ? 'n/a' : `${fmtAnmFull(freeMtd.costPerFreeUserNanm)} ANM`}
              label="Cost per free user"
              sub={
                freeMtd.costPerFreeUserNanm == null
                  ? 'no attributable free users this month'
                  : `${fmtInt(freeMtd.freeUsers)} signed-in free users · ${fmtInt(freeMtd.anonymousExecutions)} anonymous executions`
              }
            />
            <Kpi
              value={fnl.freeToPaidConversionBps == null ? 'n/a' : fmtBps(fnl.freeToPaidConversionBps)}
              label="Free → paid conversion"
              sub={`${fmtInt(fnl.paidAccounts)} paid of ${fmtInt(fnl.registered)} registered`}
              onClick={() => setDrill({ kind: 'subscriptions', title: 'Paid subscriptions' })}
            />
          </div>

          <SectionH title="Unit economics" sub="Per-execution and per-user economics for the selected range." />
          <div className="cadm-kpis">
            <Kpi value={`${fmtAnmFull(unit.revenuePerExecutionNanm)} ANM`} label="Revenue / priced execution" sub={eq(unit.revenuePerExecutionNanm)} href={execHref(range, { priced: '1' })} />
            <Kpi value={`${fmtAnmFull(unit.costPerExecutionNanm)} ANM`} label="Cost / execution" sub="all executions incl. free" href={execHref(range)} />
            <Kpi
              value={`${fmtAnmFull(unit.profitPerExecutionNanm)} ANM`}
              label="Profit / priced execution"
              color={BigInt(unit.profitPerExecutionNanm) >= 0n ? 'var(--good)' : 'var(--bad)'}
            />
            <Kpi value={`${fmtAnmFull(unit.avgPricePerExecutionNanm)} ANM`} label="Avg customer price / execution" sub={eq(unit.avgPricePerExecutionNanm)} />
            <Kpi value={`${fmtAnmFull(unit.revenuePerPayingCallerNanm)} ANM`} label="Revenue / paying user" sub={`${fmtInt(unit.payingCallers)} paying callers`} />
            <Kpi value={`${fmtAnmFull(unit.avgDeveloperRevenueNanm)} ANM`} label="Avg developer revenue" sub={`${fmtInt(unit.developersEarning)} developers earned`} />
            <Kpi
              value={unit.takeRateBps == null ? 'n/a' : fmtBps(unit.takeRateBps)}
              label="Realized take rate"
              sub={unit.takeRateBps == null ? 'no volume in range' : 'platform revenue / gross volume'}
            />
          </div>

          <SectionH title="USD business" sub={`Verified PayPal captures + subscription price snapshots. MRR basis: ${usd.mrrBasis}.`} />
          <div className="cadm-kpis">
            <Kpi value={fmtUsd(usd.collectedCents)} label="Collected in range" sub={`${fmtInt(usd.paymentsCount)} captures`} color="var(--good)" onClick={() => setDrill({ kind: 'payments', title: 'Verified USD payments' })} />
            <Kpi value={fmtUsd(usd.refundedCents)} label="Refunded in range" onClick={() => setDrill({ kind: 'payments', title: 'Verified USD payments' })} />
            <Kpi value={fmtUsd(usd.mrrCents)} label="MRR" sub={`${fmtInt(usd.paidSubscribers)} paid subscribers`} onClick={() => setDrill({ kind: 'subscriptions', title: 'Paid subscriptions' })} />
            <Kpi value={fmtUsd(usd.arrCents)} label="ARR" sub="MRR × 12" />
            <Kpi value={fmtUsd(usd.arpuCents)} label="ARPU" sub={`across ${fmtInt(usd.totalAccounts)} accounts`} />
            <Kpi value={fmtUsd(usd.arppuCents)} label="ARPPU" sub="per paying subscriber" />
            <Kpi value={fmtUsd(usd.newMrrCents)} label="New MRR" color="var(--good)" onClick={() => setDrill({ kind: 'subscriptions', title: 'Paid subscriptions' })} />
            <Kpi value={fmtUsd(usd.expansionMrrCents)} label="Expansion MRR" color="var(--good)" onClick={() => setDrill({ kind: 'subscriptions', title: 'Paid subscriptions' })} />
            <Kpi value={fmtUsd(usd.contractionMrrCents)} label="Contraction MRR" color="var(--warn)" onClick={() => setDrill({ kind: 'subscriptions', title: 'Paid subscriptions' })} />
            <Kpi value={fmtUsd(usd.churnedMrrCents)} label="Churned MRR" color="var(--bad)" onClick={() => setDrill({ kind: 'subscriptions', title: 'Paid subscriptions' })} />
          </div>

          <SectionH title="Product profitability" sub="Per product line. AI and Compute overlap the app/function/agent lines (flagged) — do not sum overlapping rows. USD lines have no per-line COGS allocation, so margin is reported as not available rather than invented." />
          <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="cadm-scroll">
              <table className="cadm-table" style={{ minWidth: 900 }}>
                <thead>
                  <tr>
                    <th>product</th>
                    <th style={{ textAlign: 'right' }}>revenue</th>
                    <th style={{ textAlign: 'right' }}>gross</th>
                    <th style={{ textAlign: 'right' }}>COGS</th>
                    <th style={{ textAlign: 'right' }}>profit</th>
                    <th style={{ textAlign: 'right' }}>margin</th>
                    <th style={{ textAlign: 'right' }}>users</th>
                    <th style={{ textAlign: 'right' }}>devs</th>
                    <th style={{ textAlign: 'right' }}>execs/pmts</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => (
                    <tr key={p.key}>
                      <td>
                        <b>{p.name}</b>
                        {p.overlaps && <span className="pill" style={{ marginLeft: 6, fontSize: 10 }}>overlaps</span>}
                        <div className="muted" style={{ fontSize: 11, whiteSpace: 'normal', maxWidth: 340 }}>{p.note}</div>
                      </td>
                      {p.currency === 'ANM' ? (
                        <>
                          <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmtAnmFull(p.revenueNanm)} ANM</td>
                          <td style={{ textAlign: 'right' }} className="muted">{fmtAnmFull(p.grossNanm)} ANM</td>
                          <td style={{ textAlign: 'right' }}>{fmtAnmFull(p.cogsNanm)} ANM</td>
                          <td style={{ textAlign: 'right', color: BigInt(p.profitNanm) < 0n ? 'var(--bad)' : undefined }}>{fmtAnmFull(p.profitNanm)} ANM</td>
                        </>
                      ) : (
                        <>
                          <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmtUsd(p.revenueCents)}</td>
                          <td style={{ textAlign: 'right' }} className="muted">—</td>
                          <td style={{ textAlign: 'right' }} className="muted">not allocated</td>
                          <td style={{ textAlign: 'right' }} className="muted">—</td>
                        </>
                      )}
                      <td style={{ textAlign: 'right' }}>{p.marginBps == null ? <span className="muted">n/a</span> : fmtBps(p.marginBps)}</td>
                      <td style={{ textAlign: 'right' }}>{fmtInt(p.users)}</td>
                      <td style={{ textAlign: 'right' }}>{fmtInt(p.developers)}</td>
                      <td style={{ textAlign: 'right' }}>{fmtInt(p.executions)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <SectionH title="Funnel" sub="Computed only from data the platform actually records. Visitors are not tracked; LTV/CAC are omitted entirely — there is no acquisition-cost data to compute them from." />
          <div className="panel">
            {(() => {
              const stages: Array<[string, number | null, string]> = [
                ['Visitors', null, 'not tracked — no analytics source'],
                ['Registered accounts', fnl.registered, ''],
                ['Developers (own ≥1 function)', fnl.developers, ''],
                ['Deployed (≥1 version created)', fnl.deployed, ''],
                ['Active in range (functions executed)', fnl.activeDevelopers, ''],
                ['Paid accounts', fnl.paidAccounts, ''],
                ['Revenue-generating developers', fnl.revenueGenerating, ''],
              ];
              const max = Math.max(1, ...stages.map(([, v]) => v ?? 0));
              return stages.map(([label, v, note]) => (
                <div className="cadm-funnel-row" key={label}>
                  <span className="cadm-funnel-label">{label}</span>
                  <div className="cadm-funnel-track">
                    {v != null && <div className="cadm-bar" style={{ width: `${v > 0 ? Math.max(2, (v / max) * 100) : 0}%` }} />}
                  </div>
                  <b className="cadm-funnel-count">{v == null ? 'n/a' : fmtInt(v)}</b>
                  {note && <span className="muted" style={{ fontSize: 11.5 }}>{note}</span>}
                </div>
              ));
            })()}
          </div>

          <DrillDrawer drill={drill} range={range} adminFetch={adminFetch} usdMicros={usdMicros} onClose={() => setDrill(null)} />
        </>
      )}
    </>
  );
}

// ── Pricing tab ──────────────────────────────────────────────────────────────
const PRICE_GROUPS: Array<{ title: string; fields: Array<[string, string]> }> = [
  {
    title: 'Customer prices (nANM per unit)',
    fields: [
      ['baseCallNanm', 'base per call'],
      ['cpuMsNanm', 'per CPU-ms'],
      ['memMbMsNanm', 'per MB-ms'],
      ['aiTokenInNanm', 'per AI input token'],
      ['aiTokenOutNanm', 'per AI output token'],
      ['egressKbNanm', 'per egress KB'],
      ['gpuMsNanm', 'per GPU-ms'],
    ],
  },
  {
    title: 'Splits (basis points)',
    fields: [
      ['platformFeeBps', 'platform fee bps'],
      ['providerShareBps', 'provider share bps'],
    ],
  },
  {
    title: 'Internal unit costs (nANM — never customer-visible)',
    fields: [
      ['costCpuMsNanm', 'cost per CPU-ms'],
      ['costMemMbMsNanm', 'cost per MB-ms'],
      ['costAiTokenNanm', 'cost per AI token'],
      ['costEgressKbNanm', 'cost per egress KB'],
      ['costPerCallNanm', 'fixed infra per call'],
    ],
  },
  {
    title: 'Margin protection',
    fields: [['targetMarginBps', 'target margin bps']],
  },
  {
    title: 'Free tier',
    fields: [
      ['freeExecutionsPerDay', 'free executions / day'],
      ['freeExecutionsPerMonth', 'free executions / month'],
      ['freeAiTokensPerDay', 'free AI tokens / day'],
      ['freeTierMonthlyCeilingNanm', 'free tier monthly ceiling (nANM)'],
    ],
  },
  {
    title: 'ANM/USD risk',
    fields: [['anmUsdFloorMicros', 'ANM price floor (micro-USD)']],
  },
];

function PricingTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [form, setForm] = useState<Record<string, string>>({});
  const [enforce, setEnforce] = useState(true);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<any>(null); // 409 diff+warnings awaiting confirm
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/pricing`);
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setData(d);
      const f: Record<string, string> = {};
      for (const g of PRICE_GROUPS) for (const [k] of g.fields) f[k] = String(d.active[k] ?? '');
      setForm(f);
      setEnforce(Boolean(d.active.enforceMinMargin));
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch]);
  useEffect(() => {
    load();
  }, [load]);

  async function submit(confirm: boolean) {
    setBusy(true);
    setMsg(null);
    try {
      const changes: Record<string, unknown> = {};
      for (const g of PRICE_GROUPS) {
        for (const [k] of g.fields) {
          if (String(data.active[k] ?? '') !== form[k]) changes[k] = form[k];
        }
      }
      if (Boolean(data.active.enforceMinMargin) !== enforce) changes.enforceMinMargin = enforce;
      const r = await adminFetch(`${API}/pricing`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ changes, reason, confirm }),
      });
      const d = await r.json();
      if (r.status === 409 && d.requiresConfirm) {
        setPending(d);
        return;
      }
      if (!r.ok) throw new Error(d?.error?.message ?? `HTTP ${r.status}`);
      setPending(null);
      setMsg({ ok: true, text: `Policy v${d.policy.version} is now active (${d.diff.length} field(s) changed).` });
      setReason('');
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrBox text={error} />;
  if (!data) return <div className="empty" style={{ marginTop: 16 }}>Loading pricing policy…</div>;

  return (
    <>
      <p className="muted" style={{ fontSize: 13, marginTop: 16 }}>
        Active: <b>policy v{data.active.version}</b>
        {data.active.id == null && ' (env bootstrap — no DB row active yet; applying changes creates v1)'} · applying changes creates a{' '}
        <b>new version</b> and flips it active — historical rows are never rewritten, and executions keep the policy version that
        priced them.
      </p>
      {PRICE_GROUPS.map((g) => (
        <div className="panel" style={{ marginTop: 14 }} key={g.title}>
          <div className="cadm-sub-h" style={{ marginTop: 0 }}>{g.title}</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 10 }}>
            {g.fields.map(([k, label]) => (
              <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-dim)' }}>
                {label}
                <input
                  className="cadm-input mono"
                  inputMode="numeric"
                  value={form[k] ?? ''}
                  onChange={(e) => setForm({ ...form, [k]: e.target.value.trim() })}
                />
              </label>
            ))}
            {g.title === 'Margin protection' && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-dim)', minHeight: 40 }}>
                <input type="checkbox" checked={enforce} onChange={(e) => setEnforce(e.target.checked)} style={{ width: 18, height: 18 }} />
                enforce minimum margin (raise prices to the floor)
              </label>
            )}
          </div>
        </div>
      ))}
      <div className="panel" style={{ marginTop: 14 }}>
        <div className="cadm-inline">
          <input
            className="cadm-input"
            style={{ flex: 1, minWidth: 220 }}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="reason for this pricing change (required — recorded on every PricingChange row)"
          />
          <button className="btn primary" disabled={busy || !reason.trim()} onClick={() => submit(false)}>
            {busy ? 'Submitting…' : 'Review & apply'}
          </button>
        </div>
        {pending && (
          <div className="cadm-warnbox">
            <b>Confirmation required — financially significant change.</b>
            {pending.warnings.map((w: string, i: number) => (
              <div key={i} style={{ marginTop: 6 }}>⚠ {w}</div>
            ))}
            <div className="cadm-sub-h">Diff</div>
            {pending.diff.map((d: any) => (
              <div key={d.field} className="mono" style={{ fontSize: 12 }}>
                {d.field}: {d.oldValue} → <b>{d.newValue}</b>
              </div>
            ))}
            <button className="btn" style={{ marginTop: 10, borderColor: 'var(--warn)', color: 'var(--warn)' }} disabled={busy} onClick={() => submit(true)}>
              {busy ? 'Applying…' : 'Confirm and apply'}
            </button>
          </div>
        )}
        {msg && <div style={{ marginTop: 8, fontSize: 13, color: msg.ok ? 'var(--good)' : 'var(--bad)' }}>{msg.text}</div>}
      </div>

      <div className="cadm-sub-h">Version history</div>
      <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 700 }}>
            <thead><tr><th>v</th><th>active</th><th>base call</th><th>fee bps</th><th>provider bps</th><th>target margin</th><th>created by</th><th>created</th><th>note</th></tr></thead>
            <tbody>
              {(data.history ?? []).length === 0 ? (
                <tr><td colSpan={9} className="muted" style={{ padding: 20, textAlign: 'center' }}>No DB policy versions yet — the env bootstrap policy is in effect.</td></tr>
              ) : (
                data.history.map((h: any) => (
                  <tr key={h.id}>
                    <td><b>v{h.version}</b></td>
                    <td>{h.active ? <span style={{ color: 'var(--good)' }}>●</span> : ''}</td>
                    <td className="mono">{h.baseCallNanm}</td>
                    <td>{h.platformFeeBps}</td>
                    <td>{h.providerShareBps}</td>
                    <td>{h.targetMarginBps}</td>
                    <td className="mono" style={{ fontSize: 11.5 }}>{shortAddr(h.createdBy)}</td>
                    <td>{fmtDateTime(h.createdAt)}</td>
                    <td className="muted cadm-ellipsis" style={{ maxWidth: 200 }}>{h.note}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="cadm-sub-h">Recent changes (audit)</div>
      <div className="panel">
        {(data.changes ?? []).length === 0 ? (
          <div className="muted" style={{ fontSize: 13 }}>No pricing changes recorded.</div>
        ) : (
          data.changes.map((c: any) => (
            <div key={c.id} className="cadm-event">
              <span className="muted" style={{ flex: 'none', width: 150 }}>{fmtDateTime(c.createdAt)}</span>
              <span className="mono" style={{ fontSize: 12 }}>{c.field}: {c.oldValue} → <b>{c.newValue}</b></span>
              <span className="muted cadm-ellipsis">{c.reason} · by {shortAddr(c.actor)}</span>
            </div>
          ))
        )}
      </div>
    </>
  );
}

// ── Simulator tab ────────────────────────────────────────────────────────────
const SIM_FIELDS: Array<[string, string, string]> = [
  ['monthlyUsers', 'Monthly users', '1000'],
  ['paidConversionBps', 'Paid conversion (bps)', '500'],
  ['executionsPerUser', 'Executions / user / month', '100'],
  ['avgCpuMs', 'Avg CPU ms / execution', '500'],
  ['avgMemoryMb', 'Avg memory MB', '256'],
  ['aiTokensPerExecution', 'AI tokens / execution', '0'],
  ['avgEgressBytes', 'Avg egress bytes', '4096'],
  ['fleetShareBps', 'Fleet share (bps)', '0'],
  ['platformFeeBps', 'Platform fee bps (override)', ''],
  ['providerShareBps', 'Provider share bps (override)', ''],
  ['anmUsdMicros', 'ANM price (micro-USD, optional)', ''],
];

function SimulatorTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [form, setForm] = useState<Record<string, string>>(Object.fromEntries(SIM_FIELDS.map(([k, , d]) => [k, d])));
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError('');
    try {
      const body: Record<string, unknown> = {};
      for (const [k] of SIM_FIELDS) if (form[k] !== '') body[k] = form[k].match(/^\d+$/) ? Number(form[k]) : form[k];
      const r = await adminFetch(`${API}/simulator`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await readErr(r));
      setResult(await r.json());
    } catch (e: any) {
      setError(e.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const m = result?.monthly;
  return (
    <>
      <p className="muted" style={{ fontSize: 13, marginTop: 16 }}>
        Scenario modelling through the <b>live pricing engine</b> — the same quote/cost/split code that prices real executions.
        Outputs are <b>ESTIMATES</b>, clearly not actuals.
      </p>
      <div className="panel" style={{ marginTop: 12 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 }}>
          {SIM_FIELDS.map(([k, label]) => (
            <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-dim)' }}>
              {label}
              <input className="cadm-input mono" inputMode="numeric" value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value.trim() })} />
            </label>
          ))}
        </div>
        <button className="btn primary" style={{ marginTop: 12 }} disabled={busy} onClick={run}>
          {busy ? 'Simulating…' : 'Run simulation'}
        </button>
        {error && <ErrBox text={error} />}
      </div>
      {result && (
        <>
          <div className="cadm-warnbox">ESTIMATES ONLY — modelled from your inputs and pricing policy v{result.inputs.policyVersion}. Actuals live on the Dashboard tab.</div>
          <div className="cadm-kpis" style={{ marginTop: 12 }}>
            <Kpi value={fmtInt(m.executions)} label="Executions / month (est.)" />
            <Kpi value={`${fmtAnmFull(m.grossVolumeNanm)} ANM`} label="Gross volume (est., not revenue)" sub={m.usd ? fmtUsd(m.usd.grossVolumeCents) : 'no USD price given'} />
            <Kpi value={`${fmtAnmFull(m.platformRevenueNanm)} ANM`} label="Platform revenue (est.)" sub={m.usd ? fmtUsd(m.usd.platformRevenueCents) : null} color="var(--good)" />
            <Kpi value={`${fmtAnmFull(m.cogsNanm)} ANM`} label="COGS (est.)" sub={m.usd ? fmtUsd(m.usd.cogsCents) : null} color="var(--warn)" />
            <Kpi value={`${fmtAnmFull(m.grossProfitNanm)} ANM`} label="Gross profit (est.)" sub={m.usd ? fmtUsd(m.usd.grossProfitCents) : null} color={BigInt(m.grossProfitNanm) >= 0n ? 'var(--good)' : 'var(--bad)'} />
            <Kpi value={m.grossMarginBps == null ? 'n/a' : fmtBps(m.grossMarginBps)} label="Gross margin (est.)" />
            <Kpi value={`${fmtAnmFull(m.developerPayoutsNanm)} ANM`} label="Developer payouts (est.)" />
            <Kpi value={`${fmtAnmFull(m.providerPayoutsNanm)} ANM`} label="Provider payouts (est.)" sub={m.usd ? fmtUsd(m.usd.providerPayoutsCents) : null} />
            <Kpi value={`${fmtAnmFull(m.anmRequiredNanm)} ANM`} label="ANM required (est.)" sub="customer spend the economy must supply" />
            <Kpi value={fmtUsd(m.subscriptionMrrCents)} label="Subscription MRR (est.)" sub={`${fmtInt(m.paidUsers)} paid users, mix ${JSON.stringify(result.inputs.subscriptionMix)}`} />
          </div>
          <div className="panel" style={{ marginTop: 12 }}>
            <div className="cadm-sub-h" style={{ marginTop: 0 }}>Per execution (est.)</div>
            <div className="cadm-facts">
              <div className="cadm-fact"><span className="k">price</span><span className="mono">{fmtAnmFull(result.perExecution.priceNanm)} ANM{result.perExecution.raisedByMarginFloor ? ' (raised by margin floor)' : ''}</span></div>
              <div className="cadm-fact"><span className="k">COGS</span><span className="mono">{fmtAnmFull(result.perExecution.cogsNanm)} ANM</span></div>
              <div className="cadm-fact"><span className="k">platform fee</span><span className="mono">{fmtAnmFull(result.perExecution.platformFeeNanm)} ANM</span></div>
              <div className="cadm-fact"><span className="k">developer (local)</span><span className="mono">{fmtAnmFull(result.perExecution.developerNanm_local)} ANM</span></div>
              <div className="cadm-fact"><span className="k">developer (fleet)</span><span className="mono">{fmtAnmFull(result.perExecution.developerNanm_fleet)} ANM</span></div>
              <div className="cadm-fact"><span className="k">provider (fleet)</span><span className="mono">{fmtAnmFull(result.perExecution.providerNanm_fleet)} ANM</span></div>
            </div>
          </div>
        </>
      )}
    </>
  );
}

// ── Reconciliation tab ───────────────────────────────────────────────────────
function ReconcileTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [day, setDay] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/reconcile`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch]);
  useEffect(() => {
    load();
  }, [load]);

  async function rerun() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await adminFetch(`${API}/reconcile`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(day ? { day } : {}),
      });
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setMsg({ ok: true, text: `FinanceDaily ${d.result.day} recomputed: ${d.result.executions} executions, platform rev ${fmtAnmFull(d.result.platformRevNanm)} ANM, USD ${fmtUsd(d.result.usdRevenueCents)}.` });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrBox text={error} />;
  if (!data) return <div className="empty" style={{ marginTop: 16 }}>Loading reconciliation…</div>;

  return (
    <>
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="cadm-inline">
          <input className="cadm-input mono" placeholder="YYYY-MM-DD (default: yesterday)" value={day} onChange={(e) => setDay(e.target.value.trim())} style={{ width: 220 }} />
          <button className="btn" disabled={busy} onClick={rerun}>{busy ? 'Recomputing…' : 'Recompute FinanceDaily'}</button>
          <span className="muted" style={{ fontSize: 12 }}>Cache refresh from authoritative rows — moves no money. Verification scopes run in the reconcile timer worker.</span>
        </div>
        {msg && <div style={{ marginTop: 8, fontSize: 13, color: msg.ok ? 'var(--good)' : 'var(--bad)' }}>{msg.text}</div>}
      </div>

      <div className="cadm-sub-h">Reconciliation reports {data.mismatched > 0 && <span style={{ color: 'var(--bad)' }}> — {data.mismatched} MISMATCHED</span>}</div>
      <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 720 }}>
            <thead><tr><th>day</th><th>scope</th><th>ok</th><th style={{ textAlign: 'right' }}>expected</th><th style={{ textAlign: 'right' }}>observed</th><th style={{ textAlign: 'right' }}>|Δ|</th><th>ran</th></tr></thead>
            <tbody>
              {data.reports.length === 0 ? (
                <tr><td colSpan={7} className="muted" style={{ padding: 20, textAlign: 'center' }}>No reports yet — enable CLOUD_RECONCILE_ENABLED=1 and let the timer run.</td></tr>
              ) : (
                data.reports.map((r: any) => (
                  <tr key={r.id}>
                    <td className="mono">{r.day}</td>
                    <td>{r.scope}</td>
                    <td style={{ color: r.ok ? 'var(--good)' : 'var(--bad)', fontWeight: 700 }}>{r.ok ? 'OK' : 'MISMATCH'}</td>
                    <td className="mono" style={{ textAlign: 'right' }}>{r.expected}</td>
                    <td className="mono" style={{ textAlign: 'right' }}>{r.observed}</td>
                    <td className="mono" style={{ textAlign: 'right', color: r.deltaAbs !== '0' ? 'var(--bad)' : undefined }}>{r.deltaAbs}</td>
                    <td>{fmtDateTime(r.createdAt)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="cadm-sub-h">FinanceDaily (cache — recomputable, verified by the worker)</div>
      <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="cadm-scroll">
          <table className="cadm-table" style={{ minWidth: 860 }}>
            <thead><tr><th>day</th><th style={{ textAlign: 'right' }}>execs</th><th style={{ textAlign: 'right' }}>free</th><th style={{ textAlign: 'right' }}>gross ANM</th><th style={{ textAlign: 'right' }}>platform rev ANM</th><th style={{ textAlign: 'right' }}>COGS ANM</th><th style={{ textAlign: 'right' }}>USD</th><th style={{ textAlign: 'right' }}>MRR</th><th style={{ textAlign: 'right' }}>devs</th></tr></thead>
            <tbody>
              {data.daily.length === 0 ? (
                <tr><td colSpan={9} className="muted" style={{ padding: 20, textAlign: 'center' }}>No daily rollups yet.</td></tr>
              ) : (
                data.daily.map((d: any) => (
                  <tr key={d.day}>
                    <td className="mono">{d.day}</td>
                    <td style={{ textAlign: 'right' }}>{fmtInt(d.executions)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtInt(d.freeExecutions)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtAnmFull(d.grossVolumeNanm)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtAnmFull(d.platformRevNanm)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtAnmFull(d.cogsNanm)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtUsd(d.usdRevenueCents)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtUsd(d.mrrCents)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtInt(d.activeDevelopers)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

// ── Alerts tab ───────────────────────────────────────────────────────────────
function AlertsTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [state, setState] = useState<'open' | 'resolved'>('open');
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/alerts?state=${state}`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch, state]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(id: string, action: 'resolve' | 'reopen') {
    const reason = window.prompt(`Reason to ${action} this alert (recorded in the audit log):`) ?? '';
    if (action === 'resolve' && !reason.trim()) return;
    setBusy(id);
    try {
      const r = await adminFetch(`${API}/alerts`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id, action, reason }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const sevColor = (s: string) => (s === 'critical' ? 'var(--bad)' : s === 'warn' ? 'var(--warn)' : 'var(--accent-2)');
  return (
    <>
      <div className="chips" style={{ marginTop: 16 }}>
        <button className={`chip${state === 'open' ? ' active' : ''}`} onClick={() => setState('open')}>Open{data ? ` (${data.openCount})` : ''}</button>
        <button className={`chip${state === 'resolved' ? ' active' : ''}`} onClick={() => setState('resolved')}>Resolved</button>
      </div>
      {error && <ErrBox text={error} />}
      {!data ? (
        <div className="empty" style={{ marginTop: 16 }}>Loading alerts…</div>
      ) : data.rows.length === 0 ? (
        <div className="empty" style={{ marginTop: 16 }}>{state === 'open' ? 'No open alerts. Quiet is good.' : 'No resolved alerts.'}</div>
      ) : (
        data.rows.map((a: any) => (
          <div key={a.id} className="panel" style={{ marginTop: 10, borderColor: state === 'open' ? sevColor(a.severity) : undefined }}>
            <div className="cadm-inline" style={{ justifyContent: 'space-between' }}>
              <div style={{ minWidth: 0 }}>
                <span className="pill" style={{ color: sevColor(a.severity), borderColor: sevColor(a.severity), fontWeight: 700, fontSize: 11 }}>{a.severity}</span>{' '}
                <b>{a.title}</b>
                <div className="muted" style={{ fontSize: 12, marginTop: 3 }}>
                  {a.kind} · {fmtDateTime(a.createdAt)} {a.subject ? `· subject ${a.subject}` : ''}
                  {a.resolvedAt ? ` · resolved ${fmtDateTime(a.resolvedAt)} by ${shortAddr(a.resolvedBy)}` : ''}
                </div>
              </div>
              <button className="btn ghost" disabled={busy === a.id} onClick={() => act(a.id, a.resolvedAt ? 'reopen' : 'resolve')}>
                {busy === a.id ? '…' : a.resolvedAt ? 'Reopen' : 'Resolve'}
              </button>
            </div>
            {a.detail && a.detail !== '{}' && (
              <pre className="mono" style={{ fontSize: 11.5, color: 'var(--text-dim)', overflowX: 'auto', margin: '8px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{a.detail}</pre>
            )}
          </div>
        ))
      )}
    </>
  );
}

// ── Founding tab ─────────────────────────────────────────────────────────────
function FoundingTab({ adminFetch }: { adminFetch: AdminFetch }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/founding`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) {
      setError(e.message);
    }
  }, [adminFetch]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(id: string, action: 'accept' | 'reject' | 'revoke') {
    const needsReason = action !== 'accept';
    const reason = window.prompt(`Reason to ${action} (recorded in the audit log)${needsReason ? ' — required' : ''}:`) ?? '';
    if (needsReason && !reason.trim()) return;
    if (action === 'accept' && !window.confirm(`Accept this application? Grants a seat, ${data.seats.benefits.proMonths} months of Pro, ${data.seats.benefits.feeBps / 100}% fee for ${data.seats.benefits.feeMonths} months, and real execution credits.`)) return;
    setBusy(id);
    setError('');
    try {
      const r = await adminFetch(`${API}/founding`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id, action, reason }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  if (error && !data) return <ErrBox text={error} />;
  if (!data) return <div className="empty" style={{ marginTop: 16 }}>Loading founding program…</div>;

  return (
    <>
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="kpi">
          <div className="k"><b>{data.seats.accepted} / {data.seats.cap}</b><span>seats taken</span></div>
          <div className="k"><b style={{ color: 'var(--accent-2)' }}>{data.seats.remaining}</b><span>remaining</span></div>
          <div className="k"><b style={{ color: 'var(--warn)' }}>{data.seats.applied}</b><span>awaiting review</span></div>
          <div className="k"><b>{fmtAnmFull(data.seats.benefits.creditsNanm)} ANM</b><span>credits per seat</span></div>
        </div>
      </div>
      {error && <ErrBox text={error} />}
      {data.rows.length === 0 ? (
        <div className="empty" style={{ marginTop: 16 }}>No applications yet.</div>
      ) : (
        <div className="panel" style={{ marginTop: 14, padding: 0, overflow: 'hidden' }}>
          <div className="cadm-scroll">
            <table className="cadm-table" style={{ minWidth: 860 }}>
              <thead><tr><th>applied</th><th>account</th><th>handle / pitch</th><th>status</th><th>seat</th><th>pro until</th><th>fee</th><th>actions</th></tr></thead>
              <tbody>
                {data.rows.map((r: any) => (
                  <tr key={r.id}>
                    <td>{fmtDate(r.createdAt)}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{shortAddr(r.account?.address)}</td>
                    <td style={{ whiteSpace: 'normal', maxWidth: 280 }}>
                      <b>{r.handle || r.account?.handle || '—'}</b>
                      <div className="muted" style={{ fontSize: 12 }}>{(r.pitch || '').slice(0, 140)}</div>
                    </td>
                    <td><StatusPill status={r.status} /></td>
                    <td>{r.seq ? `#${r.seq}` : '—'}</td>
                    <td>{fmtDate(r.proUntil)}</td>
                    <td>{r.status === 'ACCEPTED' ? `${r.feeBps / 100}% until ${fmtDate(r.feeUntil)}` : '—'}</td>
                    <td>
                      <span className="cadm-inline">
                        {r.status === 'APPLIED' && (
                          <>
                            <button className="btn" style={{ color: 'var(--good)', borderColor: 'var(--good)' }} disabled={busy === r.id || data.seats.remaining <= 0} onClick={() => act(r.id, 'accept')}>Accept</button>
                            <button className="btn ghost" disabled={busy === r.id} onClick={() => act(r.id, 'reject')}>Reject</button>
                          </>
                        )}
                        {r.status === 'ACCEPTED' && (
                          <button className="btn ghost" style={{ color: 'var(--bad)' }} disabled={busy === r.id} onClick={() => act(r.id, 'revoke')}>Revoke</button>
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

// ── page ─────────────────────────────────────────────────────────────────────
const TABS = [
  ['dashboard', 'Dashboard'],
  ['pricing', 'Pricing'],
  ['simulator', 'Simulator'],
  ['reconcile', 'Reconciliation'],
  ['alerts', 'Alerts'],
  ['founding', 'Founding devs'],
] as const;
type Tab = (typeof TABS)[number][0];

export default function ProfitabilityPage() {
  const { token, saveToken, adminFetch } = useAdmin();
  const [tab, setTab] = useState<Tab>('dashboard');

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('tab');
    if (t && TABS.some(([k]) => k === t)) setTab(t as Tab);
  }, []);

  return (
    <div className="wrap" style={{ paddingTop: 34, paddingBottom: 60 }}>
      <h1 style={{ fontSize: 30, letterSpacing: '-0.03em', margin: 0 }}>Python Cloud — Profitability</h1>
      <p className="muted" style={{ margin: '4px 0 0', fontSize: 14 }}>
        Revenue, costs, margins, unit economics and product profitability — computed live from settled executions, verified
        payments and subscription history. Operations live in <a href="/admin/cloud" style={{ color: 'var(--accent-2)' }}>/admin/cloud</a>.
      </p>

      <TokenGate token={token} saveToken={saveToken} />

      <div className="chips" style={{ marginTop: 20 }}>
        {TABS.map(([k, label]) => (
          <button key={k} className={`chip${tab === k ? ' active' : ''}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'dashboard' && <DashboardTab adminFetch={adminFetch} />}
      {tab === 'pricing' && <PricingTab adminFetch={adminFetch} />}
      {tab === 'simulator' && <SimulatorTab adminFetch={adminFetch} />}
      {tab === 'reconcile' && <ReconcileTab adminFetch={adminFetch} />}
      {tab === 'alerts' && <AlertsTab adminFetch={adminFetch} />}
      {tab === 'founding' && <FoundingTab adminFetch={adminFetch} />}

      <AdminStyles />
    </div>
  );
}
