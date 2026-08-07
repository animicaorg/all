import { prisma } from '@/lib/db';
import { periodKeyFor } from '@/lib/planConfig';
import { resolvePlan, getUsage, USAGE } from '@/lib/cloud/entitlements';
import { cloudSession, cloudAccount, ownerSegment } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import { CopyButton } from '@/components/cloud/ui';

export const dynamic = 'force-dynamic';

function fmtLimit(v: number): string {
  return v === -1 ? 'unlimited*' : v.toLocaleString();
}

function meterPct(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.min(100, Math.round((used / limit) * 100));
}

// /cloud/settings — identity, plan entitlements with LIVE usage meters, accrued overages,
// retention/limits, and links to billing + keys. Read-only where no safe write path exists.
export default async function CloudSettingsPage() {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const me = sess.accountId;
  const now = new Date();
  const period = periodKeyFor(now);

  const [account, plan, usedExec, usedCompute, usedAi, usedDeploys, charges, counts] = await Promise.all([
    cloudAccount(me),
    resolvePlan(me),
    getUsage(me, USAGE.executions, now),
    getUsage(me, USAGE.computeUnits, now),
    getUsage(me, USAGE.aiUnits, now),
    getUsage(me, USAGE.deploys, now),
    prisma.usageCharge.findMany({ where: { accountId: me, period }, orderBy: { feature: 'asc' } }),
    Promise.all([
      prisma.cloudFunction.count({ where: { ownerId: me } }),
      prisma.cloudApp.count({ where: { ownerId: me } }),
      prisma.cloudAgent.count({ where: { ownerId: me } }),
      prisma.cloudSecret.count({ where: { ownerId: me } }),
      prisma.cloudSchedule.count({ where: { ownerId: me } }),
    ]),
  ]);
  if (!account) return <CloudGate />;
  const [fnCount, appCount, agentCount, secretCount, scheduleCount] = counts;
  const L = plan.limits;

  const meters: { label: string; used: number; limit: number }[] = [
    { label: 'Executions this month', used: usedExec, limit: L.monthly_executions },
    { label: 'Compute units (CPU-s) this month', used: usedCompute, limit: L.monthly_compute_units },
    { label: 'AI units (1k tokens) this month', used: usedAi, limit: L.monthly_ai_units },
    { label: 'Functions', used: fnCount, limit: L.max_functions },
    { label: 'Apps', used: appCount, limit: L.max_apps },
    { label: 'Agents', used: agentCount, limit: L.max_agents },
    { label: 'Secrets', used: secretCount, limit: L.max_secrets },
    { label: 'Schedules', used: scheduleCount, limit: L.max_schedules },
  ];

  const totalOverageCents = charges.reduce((a, c) => a + c.amountCents, 0);

  return (
    <div>
      <h1 style={{ margin: 0, fontSize: 26, letterSpacing: '-0.03em' }}>Settings</h1>
      <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
        Identity, plan entitlements and live usage for period <span className="mono">{period}</span> (UTC months).
      </p>

      <div className="cl-grid2" style={{ marginTop: 18 }}>
        {/* identity */}
        <div className="panel">
          <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>Identity</h3>
          <div style={{ fontSize: 13.5, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div>
              <div className="muted" style={{ fontSize: 12 }}>Wallet address (your account key)</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 3 }}>
                <span className="mono" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{account.address}</span>
                <CopyButton text={account.address} small />
              </div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 12 }}>Developer handle (public endpoint owner segment)</div>
              <div style={{ marginTop: 3 }}>
                {account.handle ? (
                  <span className="mono">@{account.handle}</span>
                ) : (
                  <span className="muted">
                    none — endpoints use your address. Claim a handle in the{' '}
                    <a href="/dev" style={{ textDecoration: 'underline' }}>Developer Center</a>.
                  </span>
                )}
              </div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 12 }}>Endpoint base</div>
              <code className="inline" style={{ fontSize: 12, overflowWrap: 'anywhere', marginTop: 3, display: 'inline-block' }}>
                /api/cloud/v1/fn/{ownerSegment(account)}/&lt;slug&gt;
              </code>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 12 }}>Member since</div>
              <div style={{ marginTop: 3 }}>{account.createdAt.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}</div>
            </div>
          </div>
        </div>

        {/* plan */}
        <div className="panel">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>Plan</h3>
            <span className="pill" style={{ textTransform: 'capitalize', color: 'var(--accent-2)', borderColor: 'var(--accent-2)', fontWeight: 700 }}>
              {plan.key}
            </span>
            <span className="muted" style={{ fontSize: 12 }}>
              {plan.source === 'subscription' ? 'active subscription'
                : plan.source === 'founding' ? 'Founding Developer grant' : 'no subscription'}
            </span>
          </div>

          {plan.subscription && (
            <p className="muted" style={{ fontSize: 12.5, margin: '0 0 10px' }}>
              Subscription status <b>{plan.subscription.status}</b>
              {plan.subscription.currentPeriodEnd && <> · current period ends {new Date(plan.subscription.currentPeriodEnd).toLocaleDateString()}</>}
              {plan.blockNewPaidResources && (
                <span style={{ color: 'var(--warn)' }}> · payment past due — existing resources keep running, new ones are blocked</span>
              )}
            </p>
          )}
          {plan.founding && (
            <p className="muted" style={{ fontSize: 12.5, margin: '0 0 10px' }}>
              Founding Developer #{plan.founding.seq}
              {plan.founding.feeBps >= 0
                ? <> — marketplace fee {plan.founding.feeBps / 100}%{plan.founding.feeUntil && <> until {new Date(plan.founding.feeUntil).toLocaleDateString()}</>}</>
                : <> — reduced-fee window has ended</>}
            </p>
          )}

          <table style={{ fontSize: 13, borderSpacing: 0, width: '100%' }}>
            <tbody>
              {[
                ['Concurrency', fmtLimit(L.max_concurrency)],
                ['API rate limit', `${fmtLimit(L.api_rate_limit)} req/min`],
                ['Log retention', `${L.log_retention_days} days`],
                ['Schedule floor', `${L.min_schedule_minutes} min`],
                ['Marketplace publishing', L.marketplace_publishing ? 'yes' : 'no'],
                ['Private deployments', L.private_deployments ? 'yes' : 'no'],
                ['Overages', L.overage_allowed ? 'metered past allowance' : 'blocked at allowance'],
                ['Support', L.support],
              ].map(([k, v], i) => (
                <tr key={i}>
                  <td className="muted" style={{ padding: '3px 14px 3px 0', fontSize: 12.5, whiteSpace: 'nowrap' }}>{k}</td>
                  <td style={{ padding: '3px 0' }}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ fontSize: 11, margin: '8px 0 0' }}>
            *unlimited = no plan cap; platform safety ceilings and metering still apply.
          </p>
          <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
            <a className="btn primary" href="/pricing">Change plan</a>
            <a className="btn ghost" href="/settings/billing">Billing settings</a>
            <a className="btn ghost" href="/dev/keys">API keys</a>
          </div>
        </div>
      </div>

      {/* usage meters */}
      <div className="panel" style={{ marginTop: 16 }}>
        <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>Usage vs entitlements</h3>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 14 }}>
          {meters.map((m) => (
            <div key={m.label}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 5 }}>
                <span className="muted">{m.label}</span>
                <span className="mono">
                  {m.used.toLocaleString()} / {fmtLimit(m.limit)}
                </span>
              </div>
              <div className="cl-meter">
                <div style={{
                  width: `${m.limit === -1 ? 0 : meterPct(m.used, m.limit)}%`,
                  background: m.limit !== -1 && m.used >= m.limit
                    ? 'var(--bad)'
                    : m.limit !== -1 && meterPct(m.used, m.limit) >= 80
                      ? 'var(--warn)'
                      : undefined,
                }} />
              </div>
            </div>
          ))}
        </div>
        <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>
          {usedDeploys.toLocaleString()} deployment{usedDeploys === 1 ? '' : 's'} recorded this period.
          Monthly counters reset at the start of each UTC month.
        </p>
      </div>

      {/* accrued overages */}
      {charges.length > 0 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Accrued overages · {period}</h3>
          <div className="cl-scroll">
            <table className="cl-table" style={{ minWidth: 480 }}>
              <thead><tr><th>Meter</th><th style={{ textAlign: 'right' }}>Units</th><th style={{ textAlign: 'right' }}>Unit price</th><th style={{ textAlign: 'right' }}>Amount</th><th>Status</th></tr></thead>
              <tbody>
                {charges.map((c) => (
                  <tr key={c.id}>
                    <td className="mono" style={{ fontSize: 12.5 }}>{c.feature}</td>
                    <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>{c.units.toString()}</td>
                    <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>${(c.unitPriceCents / 100).toFixed(2)}/1k</td>
                    <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>${(c.amountCents / 100).toFixed(2)}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{c.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
            Total accrued this period: <b>${(totalOverageCents / 100).toFixed(2)}</b> — billed through your
            subscription&apos;s payment method at period end.
          </p>
        </div>
      )}
    </div>
  );
}
