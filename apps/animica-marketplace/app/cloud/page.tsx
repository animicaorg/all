import { prisma } from '@/lib/db';
import { formatAnm } from '@/lib/nanm';
import { resolvePlan } from '@/lib/cloud/entitlements';
import { cloudSession, cloudAccount, ownerSegment, lastDays, dayKey } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import { CloudStatusPill } from '@/components/cloud/ui';
import { timeAgo, fmtMs } from '@/components/cloud/format';

export const dynamic = 'force-dynamic';

// /cloud — the developer dashboard. Every number on this page is a live DB query scoped to
// the signed-in developer; the denormalized counter columns are never trusted here.
export default async function CloudDashboard() {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const me = sess.accountId;

  const now = new Date();
  const d30 = new Date(now.getTime() - 30 * 86_400_000);
  const d7 = new Date(now.getTime() - 7 * 86_400_000);
  const d14 = new Date(now.getTime() - 14 * 86_400_000);

  const [
    account,
    plan,
    appCount,
    fnList,
    fnCount,
    agentCount,
    agentActive,
    secretCount,
    keyCount,
    scheduleCount,
    execTotal,
    exec30,
    earnedAll,
    earned30,
    activeCallers,
    statuses7,
    recentDeploys,
    recentExecs,
    sparkRows,
  ] = await Promise.all([
    cloudAccount(me),
    resolvePlan(me),
    prisma.cloudApp.count({ where: { ownerId: me } }),
    prisma.cloudFunction.findMany({
      where: { ownerId: me },
      orderBy: { updatedAt: 'desc' },
      take: 6,
      select: { id: true, slug: true, name: true, status: true, currentVersion: true, updatedAt: true },
    }),
    prisma.cloudFunction.count({ where: { ownerId: me } }),
    prisma.cloudAgent.count({ where: { ownerId: me } }),
    prisma.cloudAgent.count({ where: { ownerId: me, status: 'ACTIVE' } }),
    prisma.cloudSecret.count({ where: { ownerId: me } }),
    prisma.apiKey.count({ where: { accountId: me, status: 'ACTIVE' } }),
    prisma.cloudSchedule.count({ where: { ownerId: me } }),
    prisma.cloudExecution.count({ where: { developerAccountId: me } }),
    prisma.cloudExecution.count({ where: { developerAccountId: me, createdAt: { gte: d30 } } }),
    prisma.cloudExecution.aggregate({ where: { developerAccountId: me, billed: true }, _sum: { developerNanm: true } }),
    prisma.cloudExecution.aggregate({
      where: { developerAccountId: me, billed: true, createdAt: { gte: d30 } },
      _sum: { developerNanm: true },
    }),
    prisma.cloudExecution.findMany({
      where: { developerAccountId: me, createdAt: { gte: d30 }, callerAccountId: { not: null } },
      distinct: ['callerAccountId'],
      select: { callerAccountId: true },
    }),
    prisma.cloudExecution.groupBy({
      by: ['status'],
      where: { developerAccountId: me, createdAt: { gte: d7 } },
      _count: { _all: true },
    }),
    prisma.cloudDeployment.findMany({
      where: { function: { ownerId: me } },
      orderBy: { createdAt: 'desc' },
      take: 6,
      include: {
        function: { select: { id: true, slug: true, name: true } },
        version: { select: { version: true } },
      },
    }),
    prisma.cloudExecution.findMany({
      where: { developerAccountId: me },
      orderBy: { createdAt: 'desc' },
      take: 8,
      select: {
        id: true, requestId: true, status: true, durationMs: true, developerNanm: true,
        priceNanm: true, freeTier: true, createdAt: true, callerKind: true,
        function: { select: { slug: true, id: true } },
      },
    }),
    prisma.$queryRaw<{ d: Date; c: bigint }[]>`
      SELECT date_trunc('day', "createdAt") AS d, count(*)::bigint AS c
      FROM "CloudExecution"
      WHERE "developerAccountId" = ${me} AND "createdAt" >= ${d14}
      GROUP BY 1 ORDER BY 1`,
  ]);

  if (!account) return <CloudGate />;

  const netAll = earnedAll._sum.developerNanm ?? 0n;
  const net30 = earned30._sum.developerNanm ?? 0n;

  const total7 = statuses7.reduce((a, s) => a + s._count._all, 0);
  const failed7 = statuses7
    .filter((s) => s.status === 'FAILED' || s.status === 'TIMEOUT')
    .reduce((a, s) => a + s._count._all, 0);
  const errorRate = total7 > 0 ? `${((failed7 / total7) * 100).toFixed(1)}%` : '—';

  // 14-day execution sparkline (server-rendered SVG — no client JS needed).
  const byDay = new Map(sparkRows.map((r) => [dayKey(new Date(r.d)), Number(r.c)]));
  const days = lastDays(14, now);
  const series = days.map((d) => byDay.get(d) ?? 0);
  const sparkMax = Math.max(1, ...series);
  const sparkPts = series
    .map((v, i) => `${(i / (series.length - 1)) * 260},${34 - (v / sparkMax) * 30}`)
    .join(' ');

  const seg = ownerSegment(account);
  const noFunctions = fnCount === 0;

  const sections: { href: string; title: string; value: string; sub: string }[] = [
    { href: '/cloud/functions', title: 'Projects', value: `${fnCount} function${fnCount === 1 ? '' : 's'}`, sub: `${appCount} app${appCount === 1 ? '' : 's'} · ${scheduleCount} schedule${scheduleCount === 1 ? '' : 's'}` },
    { href: '/cloud/analytics', title: 'Analytics', value: `${exec30.toLocaleString()} exec / 30d`, sub: `error rate ${errorRate} (7d)` },
    { href: '/cloud/earnings', title: 'Earnings', value: `${formatAnm(account.balanceNanm)} ANM`, sub: `balance · +${formatAnm(net30)} earned 30d` },
    { href: '/dev/keys', title: 'API keys', value: `${keyCount} active`, sub: 'mint & manage in the Developer Center' },
    { href: '/cloud/secrets', title: 'Secrets', value: `${secretCount} stored`, sub: 'encrypted, injected at runtime' },
    { href: '/cloud/agents', title: 'Agents', value: `${agentActive} of ${agentCount} active`, sub: 'budgeted autonomous programs' },
    { href: '/cloud/pricing', title: 'Billing', value: plan.key, sub: plan.source === 'founding' ? 'via Founding Developer grant' : plan.source === 'subscription' ? 'active subscription' : 'free plan', },
    { href: '/cloud/settings', title: 'Settings', value: account.handle ? `@${account.handle}` : 'no handle yet', sub: 'plan usage, identity, retention' },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26, letterSpacing: '-0.03em' }}>Dashboard</h1>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
            {account.handle ? <>Deploying as <b>@{account.handle}</b></> : <>Deploying as <span className="mono">{account.address.slice(0, 16)}…</span></>}
            {' '}· plan <b style={{ textTransform: 'capitalize' }}>{plan.key}</b>
            {plan.founding && plan.founding.feeBps >= 0 && (
              <span className="pill" style={{ marginLeft: 8, color: 'var(--accent-2)', borderColor: 'var(--accent-2)', fontSize: 11 }}>
                Founding Dev #{plan.founding.seq} · {plan.founding.feeBps / 100}% fee
              </span>
            )}
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <a className="btn primary" href="/cloud/functions/new">+ New function</a>
      </div>

      {noFunctions && (
        <div className="panel" style={{ marginTop: 20, borderColor: 'var(--accent)' }}>
          <h3 style={{ margin: '0 0 6px', fontSize: 17 }}>Deploy your first Python function</h3>
          <p className="muted" style={{ margin: 0, fontSize: 14, lineHeight: 1.55 }}>
            Write a <code className="inline">main(request, ctx)</code> in the browser editor, validate it,
            and deploy. You get a public HTTPS endpoint at{' '}
            <code className="inline">/api/cloud/v1/fn/{seg}/&lt;slug&gt;</code> — and you earn ANM every
            time someone calls it. Deployments are anchored on-chain (source hash + artifact hash + DA
            blob id inside a signed DEPLOY tx) and executed off-chain in a hardened container.
          </p>
          <div style={{ marginTop: 14 }}>
            <a className="btn primary" href="/cloud/functions/new">Open the editor →</a>
          </div>
        </div>
      )}

      {/* KPI row — all live queries */}
      <div className="cl-kpis" style={{ marginTop: 20 }}>
        <div className="cl-kpi"><b>{fnCount}</b><span>Functions</span></div>
        <div className="cl-kpi"><b>{appCount}</b><span>Apps</span></div>
        <div className="cl-kpi"><b>{agentCount}</b><span>Agents</span></div>
        <div className="cl-kpi"><b>{execTotal.toLocaleString()}</b><span>Executions all-time</span></div>
        <div className="cl-kpi"><b>{formatAnm(netAll)}</b><span>ANM earned (net)</span></div>
        <div className="cl-kpi"><b>{activeCallers.length}</b><span>Active callers (30d)</span></div>
        <div className="cl-kpi"><b style={{ color: total7 > 0 && failed7 > 0 ? 'var(--warn)' : undefined }}>{errorRate}</b><span>Error rate (7d)</span></div>
      </div>

      {/* 14-day execution sparkline */}
      <div className="panel" style={{ marginTop: 16, padding: '14px 18px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span className="muted" style={{ fontSize: 12.5 }}>Executions · last 14 days</span>
          <span style={{ fontWeight: 700 }}>{series.reduce((a, b) => a + b, 0).toLocaleString()}</span>
          <span style={{ flex: 1 }} />
          <a href="/cloud/analytics" className="muted" style={{ fontSize: 12.5, textDecoration: 'underline' }}>full analytics →</a>
        </div>
        {series.some((v) => v > 0) ? (
          <svg viewBox="0 0 260 36" style={{ width: '100%', maxWidth: 560, height: 44, display: 'block', marginTop: 6 }} aria-label="executions per day, last 14 days">
            <polyline points={sparkPts} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          </svg>
        ) : (
          <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>No executions in the last 14 days.</div>
        )}
      </div>

      <div className="cl-grid2" style={{ marginTop: 16 }}>
        {/* Projects */}
        <div className="panel">
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
            <h3 style={{ margin: 0, fontSize: 15.5 }}>Projects</h3>
            <span style={{ flex: 1 }} />
            <a href="/cloud/functions" className="muted" style={{ fontSize: 12.5, textDecoration: 'underline' }}>all functions →</a>
          </div>
          {fnList.length === 0 ? (
            <div className="empty" style={{ padding: '24px 12px', fontSize: 13 }}>
              Nothing deployed yet — <a href="/cloud/functions/new" style={{ textDecoration: 'underline' }}>create your first function</a>.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {fnList.map((f) => (
                <a key={f.id} href={`/cloud/functions/${f.id}`}
                  style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 10, minHeight: 40 }}>
                  <span className="mono" style={{ fontSize: 13, overflowWrap: 'anywhere' }}>{seg}/{f.slug}</span>
                  <span className="muted" style={{ fontSize: 11.5 }}>v{f.currentVersion}</span>
                  <span style={{ flex: 1 }} />
                  <CloudStatusPill status={f.status} />
                </a>
              ))}
            </div>
          )}
        </div>

        {/* Deployments */}
        <div className="panel">
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
            <h3 style={{ margin: 0, fontSize: 15.5 }}>Deployments</h3>
          </div>
          {recentDeploys.length === 0 ? (
            <div className="empty" style={{ padding: '24px 12px', fontSize: 13 }}>No deployments yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {recentDeploys.map((d) => (
                <a key={d.id} href={`/cloud/functions/${d.function.id}`}
                  style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 10, minHeight: 40 }}>
                  <span className="mono" style={{ fontSize: 13 }}>{d.function.slug}</span>
                  <span className="muted" style={{ fontSize: 11.5 }}>v{d.version.version}</span>
                  {d.anchorTxid
                    ? <span className="pill" title={`anchor tx ${d.anchorTxid}`} style={{ fontSize: 10.5, color: 'var(--accent-2)', borderColor: 'var(--accent-2)' }}>⚓ anchored</span>
                    : <span className="pill" style={{ fontSize: 10.5 }} title="no on-chain anchor recorded for this deployment">unanchored</span>}
                  <span style={{ flex: 1 }} />
                  <span className="muted" style={{ fontSize: 11.5 }}>{timeAgo(d.createdAt.toISOString())}</span>
                  <CloudStatusPill status={d.status} />
                </a>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Recent executions */}
      <div className="panel" style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
          <h3 style={{ margin: 0, fontSize: 15.5 }}>Recent executions</h3>
          <span style={{ flex: 1 }} />
          <a href="/cloud/analytics" className="muted" style={{ fontSize: 12.5, textDecoration: 'underline' }}>analytics →</a>
        </div>
        {recentExecs.length === 0 ? (
          <div className="empty" style={{ padding: '24px 12px', fontSize: 13 }}>
            No executions yet. Once your endpoint gets its first call, it shows up here with its real cost and your share.
          </div>
        ) : (
          <div className="cl-scroll">
            <table className="cl-table">
              <thead>
                <tr><th>Function</th><th>Status</th><th>Caller</th><th>Duration</th><th>You earned</th><th>When</th></tr>
              </thead>
              <tbody>
                {recentExecs.map((e) => (
                  <tr key={e.id}>
                    <td className="mono" style={{ fontSize: 12.5 }}>
                      <a href={`/cloud/functions/${e.function.id}`} style={{ textDecoration: 'underline' }}>{e.function.slug}</a>
                    </td>
                    <td><CloudStatusPill status={e.status} /></td>
                    <td className="muted" style={{ fontSize: 12 }}>{e.freeTier ? 'free tier' : e.callerKind}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{fmtMs(e.durationMs)}</td>
                    <td className="mono" style={{ fontSize: 12.5 }}>{formatAnm(e.developerNanm)} ANM</td>
                    <td className="muted" style={{ fontSize: 12 }}>{timeAgo(e.createdAt.toISOString())}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Section shortcuts */}
      <div className="grid" style={{ marginTop: 16, gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
        {sections.map((s) => (
          <a key={s.href} href={s.href} className="card" style={{ gap: 4 }}>
            <h3 style={{ fontSize: 14 }}>{s.title}</h3>
            <div style={{ fontWeight: 700, fontSize: 15, textTransform: s.title === 'Billing' ? 'capitalize' : undefined }}>{s.value}</div>
            <p style={{ fontSize: 12.5 }}>{s.sub}</p>
          </a>
        ))}
      </div>
    </div>
  );
}
