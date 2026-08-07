import { prisma } from '@/lib/db';
import { formatAnm } from '@/lib/nanm';
import { runtime } from '@/lib/cloud/config';
import { cloudSession, cloudAccount, ownerSegment } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import { CloudStatusPill, CopyButton } from '@/components/cloud/ui';
import { timeAgo } from '@/components/cloud/format';

export const dynamic = 'force-dynamic';

// /cloud/functions — every function with its LIVE status, version, execution count and revenue.
// Counts and revenue come from CloudExecution aggregates, not the cached counter columns.
export default async function CloudFunctionsPage() {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const me = sess.accountId;

  const [account, fns] = await Promise.all([
    cloudAccount(me),
    prisma.cloudFunction.findMany({
      where: { ownerId: me },
      orderBy: { updatedAt: 'desc' },
      include: {
        app: { select: { slug: true, name: true } },
        deployments: { orderBy: { createdAt: 'desc' }, take: 1, select: { status: true, anchorTxid: true, createdAt: true } },
      },
    }),
  ]);
  if (!account) return <CloudGate />;

  const stats = await prisma.cloudExecution.groupBy({
    by: ['functionId'],
    where: { developerAccountId: me },
    _count: { _all: true },
    _sum: { developerNanm: true },
  });
  const statByFn = new Map(stats.map((s) => [s.functionId, s]));
  const seg = ownerSegment(account);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26, letterSpacing: '-0.03em' }}>Functions</h1>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
            Deployed Python, callable at <code className="inline">/api/cloud/v1/fn/{seg}/&lt;slug&gt;</code>.
            Execution counts and revenue below are live ledger-backed aggregates.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <a className="btn primary" href="/cloud/functions/new">+ New function</a>
      </div>

      {fns.length === 0 ? (
        <div className="empty" style={{ marginTop: 24 }}>
          <div style={{ fontSize: 34, marginBottom: 8 }}>🐍</div>
          <p style={{ margin: '0 0 6px', color: 'var(--text-dim)' }}>
            No functions yet. Write <code className="inline">def main(request, ctx)</code>, deploy it, and
            share your endpoint — you earn ANM on every call.
          </p>
          <a className="btn primary" href="/cloud/functions/new" style={{ marginTop: 10 }}>Open the editor →</a>
        </div>
      ) : (
        <div className="cl-scroll" style={{ marginTop: 20, border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--bg-card)' }}>
          <table className="cl-table" style={{ minWidth: 760 }}>
            <thead>
              <tr>
                <th>Function</th>
                <th>Status</th>
                <th>Last deploy</th>
                <th>Version</th>
                <th style={{ textAlign: 'right' }}>Executions</th>
                <th style={{ textAlign: 'right' }}>Revenue (net)</th>
                <th>Price/call</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {fns.map((f) => {
                const st = statByFn.get(f.id);
                const lastDep = f.deployments[0];
                return (
                  <tr key={f.id} className="rowlink">
                    <td>
                      <a href={`/cloud/functions/${f.id}`} style={{ display: 'block' }}>
                        <span className="mono" style={{ fontSize: 13 }}>{seg}/{f.slug}</span>
                        <div className="muted" style={{ fontSize: 11.5 }}>{f.name}{f.app ? ` · app: ${f.app.name}` : ''}</div>
                      </a>
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                        <CloudStatusPill status={f.status} />
                        {f.suspendedAt && <CloudStatusPill status="SUSPENDED" title={f.suspendedReason ?? undefined} />}
                      </div>
                    </td>
                    <td>
                      {lastDep ? (
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                          <CloudStatusPill status={lastDep.status} />
                          <span className="muted" style={{ fontSize: 11.5 }}>{timeAgo(lastDep.createdAt.toISOString())}</span>
                        </div>
                      ) : (
                        <span className="muted" style={{ fontSize: 12 }}>never deployed</span>
                      )}
                    </td>
                    <td className="mono" style={{ fontSize: 12.5 }}>v{f.currentVersion}</td>
                    <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>{(st?._count._all ?? 0).toLocaleString()}</td>
                    <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>{formatAnm(st?._sum.developerNanm ?? 0n)} ANM</td>
                    <td className="mono" style={{ fontSize: 12.5 }}>
                      {f.perCallNanm > 0n ? `+${formatAnm(f.perCallNanm)}` : 'metered'}
                    </td>
                    <td>
                      <CopyButton text={`${runtime.publicBase}/api/cloud/v1/fn/${seg}/${f.slug}`} label="URL" small />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
