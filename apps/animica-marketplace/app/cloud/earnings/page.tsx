import { prisma } from '@/lib/db';
import { formatAnm, jsonSafe } from '@/lib/nanm';
import { config } from '@/lib/config';
import { cloudSession, cloudAccount } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import { CloudStatusPill } from '@/components/cloud/ui';
import { timeAgo } from '@/components/cloud/format';
import WithdrawPanel from './WithdrawPanel';

export const dynamic = 'force-dynamic';

// /cloud/earnings — gross revenue, platform fee, net earnings, per-function breakdown, payout
// history, and withdrawals through the existing /api/mkt/v1/withdrawals rail. Every figure is
// summed from CloudExecution / CloudAppPurchase / Withdrawal rows, never cached counters.
export default async function CloudEarningsPage() {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const me = sess.accountId;

  const [account, execAll, exec30, byFn, purchases, withdrawals] = await Promise.all([
    cloudAccount(me),
    prisma.cloudExecution.aggregate({
      where: { developerAccountId: me, billed: true },
      _count: { _all: true },
      _sum: { priceNanm: true, platformFeeNanm: true, developerNanm: true, providerNanm: true },
    }),
    prisma.cloudExecution.aggregate({
      where: { developerAccountId: me, billed: true, createdAt: { gte: new Date(Date.now() - 30 * 86_400_000) } },
      _sum: { developerNanm: true },
    }),
    prisma.cloudExecution.groupBy({
      by: ['functionId'],
      where: { developerAccountId: me, billed: true },
      _count: { _all: true },
      _sum: { priceNanm: true, platformFeeNanm: true, developerNanm: true },
      orderBy: { _sum: { developerNanm: 'desc' } },
      take: 50,
    }),
    prisma.cloudAppPurchase.aggregate({
      where: { app: { ownerId: me } },
      _count: { _all: true },
      _sum: { amountNanm: true, developerNanm: true, platformFeeNanm: true },
    }),
    prisma.withdrawal.findMany({
      where: { accountId: me },
      orderBy: { createdAt: 'desc' },
      take: 25,
    }),
  ]);
  if (!account) return <CloudGate />;

  const fnRows = byFn.length
    ? await prisma.cloudFunction.findMany({
        where: { id: { in: byFn.map((r) => r.functionId) } },
        select: { id: true, slug: true, name: true },
      })
    : [];
  const fnById = new Map(fnRows.map((f) => [f.id, f]));

  const grossExec = execAll._sum.priceNanm ?? 0n;
  const feeExec = execAll._sum.platformFeeNanm ?? 0n;
  const providerExec = execAll._sum.providerNanm ?? 0n;
  const netExec = execAll._sum.developerNanm ?? 0n;
  const grossPurch = purchases._sum.amountNanm ?? 0n;
  const netPurch = purchases._sum.developerNanm ?? 0n;
  const feePurch = purchases._sum.platformFeeNanm ?? 0n;

  return (
    <div>
      <h1 style={{ margin: 0, fontSize: 26, letterSpacing: '-0.03em' }}>Earnings</h1>
      <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
        Your share of every execution and app sale lands as a spendable ledger credit the moment it
        settles — withdraw it on-chain whenever you like.
      </p>

      <div className="cl-kpis" style={{ marginTop: 18 }}>
        <div className="cl-kpi"><b>{formatAnm(grossExec + grossPurch)}</b><span>Gross revenue (ANM)</span></div>
        <div className="cl-kpi"><b>−{formatAnm(feeExec + feePurch)}</b><span>Platform fee</span></div>
        {providerExec > 0n && <div className="cl-kpi"><b>−{formatAnm(providerExec)}</b><span>Compute providers</span></div>}
        <div className="cl-kpi"><b style={{ color: 'var(--good)' }}>{formatAnm(netExec + netPurch)}</b><span>Net earnings (ANM)</span></div>
        <div className="cl-kpi"><b>{formatAnm(exec30._sum.developerNanm ?? 0n)}</b><span>Net · last 30d</span></div>
        <div className="cl-kpi"><b>{formatAnm(account.balanceNanm)}</b><span>Withdrawable balance</span></div>
      </div>

      <div className="cl-grid2" style={{ marginTop: 16 }}>
        {/* per-function breakdown */}
        <div className="panel">
          <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Per-function breakdown</h3>
          {byFn.length === 0 ? (
            <div className="empty" style={{ padding: '26px 12px', fontSize: 13 }}>
              No billed executions yet. Deploy a function and share its endpoint — the split shows up
              here per function as calls come in.
            </div>
          ) : (
            <div className="cl-scroll">
              <table className="cl-table" style={{ minWidth: 560 }}>
                <thead>
                  <tr><th>Function</th><th style={{ textAlign: 'right' }}>Calls</th><th style={{ textAlign: 'right' }}>Gross</th><th style={{ textAlign: 'right' }}>Fee</th><th style={{ textAlign: 'right' }}>Net to you</th></tr>
                </thead>
                <tbody>
                  {byFn.map((r) => {
                    const f = fnById.get(r.functionId);
                    return (
                      <tr key={r.functionId}>
                        <td>
                          {f ? (
                            <a className="mono" style={{ fontSize: 12.5, textDecoration: 'underline' }} href={`/cloud/functions/${f.id}`}>{f.slug}</a>
                          ) : (
                            <span className="mono muted" style={{ fontSize: 12.5 }} title={r.functionId}>(deleted function)</span>
                          )}
                        </td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>{r._count._all.toLocaleString()}</td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>{formatAnm(r._sum.priceNanm ?? 0n)}</td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right' }}>−{formatAnm(r._sum.platformFeeNanm ?? 0n)}</td>
                        <td className="mono" style={{ fontSize: 12.5, textAlign: 'right', color: 'var(--good)' }}>{formatAnm(r._sum.developerNanm ?? 0n)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {purchases._count._all > 0 && (
            <p className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
              Plus {purchases._count._all} app purchase{purchases._count._all === 1 ? '' : 's'}:{' '}
              {formatAnm(grossPurch)} gross → {formatAnm(netPurch)} net to you.
            </p>
          )}
        </div>

        {/* withdraw */}
        <WithdrawPanel
          balanceNanm={account.balanceNanm.toString()}
          minWithdrawalNanm={config.minWithdrawalNanm.toString()}
          defaultAddress={account.address}
          payoutEnabled={config.payoutEnabled}
        />
      </div>

      {/* payout history */}
      <div className="panel" style={{ marginTop: 16 }}>
        <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Payout history</h3>
        {withdrawals.length === 0 ? (
          <div className="empty" style={{ padding: '24px 12px', fontSize: 13 }}>
            No withdrawals yet. Your earnings stay in your withdrawable balance until you send them on-chain.
          </div>
        ) : (
          <div className="cl-scroll">
            <table className="cl-table" style={{ minWidth: 640 }}>
              <thead>
                <tr><th>Amount</th><th>To</th><th>Status</th><th>Tx</th><th>When</th></tr>
              </thead>
              <tbody>
                {jsonSafe(withdrawals).map((w: any) => (
                  <tr key={w.id}>
                    <td className="mono" style={{ fontSize: 12.5 }}>{formatAnm(BigInt(w.amountNanm))} ANM</td>
                    <td className="mono" style={{ fontSize: 11.5, overflowWrap: 'anywhere' }}>{w.toAddress}</td>
                    <td><CloudStatusPill status={w.status} title={w.error ?? undefined} /></td>
                    <td className="mono" style={{ fontSize: 11.5, overflowWrap: 'anywhere' }}>{w.txid ?? '—'}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{timeAgo(w.createdAt)}</td>
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
