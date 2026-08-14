import type { Metadata } from 'next';
import { prisma } from '@/lib/db';
import { compact, fmtDate, shortAddr } from '@/components/cloud-public/fmt';

// /developers — public directory of every claimed developer handle. Each row links to the
// indexable profile at /developers/{handle}. Counts are live filtered relation counts over
// published, public apps/functions — nothing invented.

export const dynamic = 'force-dynamic';

const BASE = 'https://animica.dev';

export const metadata: Metadata = {
  title: 'Developers — Animica Python Cloud',
  description:
    'The developers building on Animica Python Cloud. Browse public profiles, their published apps and functions, and start earning ANM with your own Python code.',
  alternates: { canonical: `${BASE}/developers` },
  openGraph: {
    title: 'Animica developers',
    description: 'The developers building and earning on Animica Python Cloud.',
    url: `${BASE}/developers`,
    siteName: 'Animica',
    type: 'website',
  },
};

export default async function DevelopersPage() {
  let rows: Array<{
    handle: string | null;
    displayName: string | null;
    bio: string | null;
    avatarUrl: string | null;
    address: string;
    createdAt: Date;
    foundingDev: { status: string; seq: number | null } | null;
    _count: { cloudApps: number; cloudFunctions: number };
  }> = [];
  let dbOk = true;
  try {
    rows = await prisma.account.findMany({
      where: { handle: { not: null } },
      select: {
        handle: true, displayName: true, bio: true, avatarUrl: true, address: true, createdAt: true,
        foundingDev: { select: { status: true, seq: true } },
        _count: {
          select: {
            cloudApps: { where: { status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null } },
            cloudFunctions: { where: { status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null } },
          },
        },
      },
      orderBy: { createdAt: 'asc' },
      take: 500,
    });
  } catch {
    dbOk = false;
  }

  // Builders with something published first, then the rest — both real.
  const sorted = rows
    .slice()
    .sort((a, b) => (b._count.cloudApps + b._count.cloudFunctions) - (a._count.cloudApps + a._count.cloudFunctions));

  return (
    <main>
      <header className="hero" style={{ paddingBottom: 20 }}>
        <div className="wrap">
          <h1 style={{ fontSize: 44 }}>
            <span className="grad">Developers</span> on Animica
          </h1>
          <p className="sub">
            The people (and agents) shipping Python to Animica. Every profile lists their published
            apps and public functions — and every call they serve pays them in ANM.
          </p>
        </div>
      </header>
      <section className="section" style={{ paddingTop: 0 }}>
        <div className="wrap">
          {!dbOk ? (
            <div className="empty">The directory is temporarily unavailable. Please try again in a moment.</div>
          ) : sorted.length === 0 ? (
            <div className="empty">
              <div style={{ fontSize: 17, color: 'var(--text)', marginBottom: 6 }}>No developer handles claimed yet.</div>
              Claim yours from the developer console and your public profile appears here.
              <div style={{ marginTop: 16 }}>
                <a className="btn primary" href="/cloud">Open the developer console</a>
              </div>
            </div>
          ) : (
            <div className="grid">
              {sorted.map((d) => {
                const founding = d.foundingDev?.status === 'ACCEPTED' ? d.foundingDev : null;
                return (
                  <a className="card" key={d.handle} href={`/developers/${encodeURIComponent(d.handle!)}`}>
                    <div className="top">
                      <div className="ico app-ico" aria-hidden="true">
                        {d.avatarUrl ? <img src={d.avatarUrl} alt="" /> : '👤'}
                      </div>
                      <div style={{ minWidth: 0 }}>
                        <h3>{d.displayName || d.handle}</h3>
                        <div className="by">@{d.handle} · {shortAddr(d.address)}</div>
                      </div>
                    </div>
                    <p>{d.bio || `Building on Animica since ${fmtDate(d.createdAt)}.`}</p>
                    <div className="meta">
                      {founding ? (
                        <span className="badge founding">Founding{founding.seq ? ` #${founding.seq}` : ''}</span>
                      ) : null}
                      <span style={{ marginLeft: 'auto' }}>
                        {compact(d._count.cloudApps)} app{d._count.cloudApps === 1 ? '' : 's'} ·{' '}
                        {compact(d._count.cloudFunctions)} function{d._count.cloudFunctions === 1 ? '' : 's'}
                      </span>
                    </div>
                  </a>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
