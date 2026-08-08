import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';
import { cache } from 'react';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';
import CloudAppCard, { type CloudAppCardData } from '@/components/cloud-public/CloudAppCard';
import { compact, fmtDate, shortAddr } from '@/components/cloud-public/fmt';

// /developers/[handle] — the public, indexable developer profile (spec §37). The param is a
// claimed handle (canonical) or a bech32m address (redirected to the handle when one exists).
// Everything shown is a live aggregate: published apps, public functions, executions served.
// The Founding Developer badge renders ONLY for a genuinely ACCEPTED FoundingDeveloper row.

export const dynamic = 'force-dynamic';

const BASE = 'https://animica.dev';

const getDeveloper = cache(async (raw: string) => {
  const key = decodeURIComponent(raw).trim();
  return prisma.account.findFirst({
    where: { OR: [{ handle: key.toLowerCase() }, { address: key }] },
    select: {
      id: true, handle: true, displayName: true, bio: true, websiteUrl: true, avatarUrl: true,
      address: true, createdAt: true, isAgent: true,
      foundingDev: { select: { status: true, seq: true, acceptedAt: true } },
    },
  });
});

export async function generateMetadata({ params }: { params: { handle: string } }): Promise<Metadata> {
  const dev = await getDeveloper(params.handle).catch(() => null);
  if (!dev) return { title: 'Developer not found — Animica', robots: { index: false } };
  const key = dev.handle ?? dev.address;
  const name = dev.displayName || (dev.handle ? `@${dev.handle}` : shortAddr(dev.address));
  const url = `${BASE}/developers/${encodeURIComponent(key)}`;
  const desc = (dev.bio || `${name} builds on Animica Python Cloud — Python apps and functions, paid in ANM.`).slice(0, 300);
  return {
    title: `${name} — Animica developer`,
    description: desc,
    alternates: { canonical: url },
    openGraph: {
    // Next replaces the parent's whole `openGraph` object when a page declares its
    // own, so the layout's default image does NOT survive here — it has to be named.
    images: ['/og.png'],
      title: `${name} — Animica developer`,
      description: desc,
      url,
      siteName: 'Animica',
      type: 'profile',
      ...(dev.avatarUrl ? { images: [dev.avatarUrl] } : {}),
    },
    twitter: { card: 'summary', title: `${name} — Animica developer`, description: desc },
  };
}

export default async function DeveloperPage({ params }: { params: { handle: string } }) {
  const dev = await getDeveloper(params.handle);
  if (!dev) notFound();
  // Canonical URL is the handle; an address lookup for a handled account redirects there.
  const raw = decodeURIComponent(params.handle).trim();
  if (dev.handle && raw.toLowerCase() !== dev.handle) {
    redirect(`/developers/${encodeURIComponent(dev.handle)}`);
  }

  const since30 = new Date(Date.now() - 30 * 24 * 3600 * 1000);
  const publicWhere = { status: 'PUBLISHED' as const, visibility: 'PUBLIC' as const, suspendedAt: null };
  const [apps, fns, execTotal, exec30d] = await Promise.all([
    prisma.cloudApp.findMany({
      where: { ownerId: dev.id, ...publicWhere },
      orderBy: [{ execCount: 'desc' }, { publishedAt: 'desc' }],
      select: {
        slug: true, name: true, tagline: true, category: true, iconEmoji: true, iconUrl: true,
        pricingModel: true, priceNanm: true, execCount: true, installCount: true,
        ratingSum: true, ratingCount: true,
        owner: { select: { handle: true, displayName: true, address: true } },
      },
    }),
    prisma.cloudFunction.findMany({
      where: { ownerId: dev.id, ...publicWhere },
      orderBy: [{ execCount: 'desc' }, { createdAt: 'desc' }],
      select: {
        slug: true, name: true, description: true, requiresAuth: true, perCallNanm: true,
        execCount: true, currentVersion: true,
      },
    }),
    prisma.cloudExecution.count({ where: { developerAccountId: dev.id } }),
    prisma.cloudExecution.count({ where: { developerAccountId: dev.id, createdAt: { gte: since30 } } }),
  ]);

  const key = dev.handle ?? dev.address;
  const name = dev.displayName || (dev.handle ? `@${dev.handle}` : shortAddr(dev.address));
  const url = `${BASE}/developers/${encodeURIComponent(key)}`;
  const founding = dev.foundingDev?.status === 'ACCEPTED' ? dev.foundingDev : null;

  const ld: Record<string, unknown> = {
    '@context': 'https://schema.org',
    '@type': dev.isAgent ? 'Organization' : 'Person',
    name,
    url,
    description: (dev.bio || `Developer on Animica Python Cloud.`).slice(0, 500),
    ...(dev.avatarUrl ? { image: dev.avatarUrl } : {}),
    ...(dev.websiteUrl ? { sameAs: [dev.websiteUrl] } : {}),
  };

  return (
    <main>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(ld).replace(/</g, '\\u003c') }}
      />
      <div className="wrap">
        <nav className="crumbs" aria-label="Breadcrumb">
          <a href="/developers">Developers</a>
          <span>/</span>
          <span style={{ color: 'var(--text-dim)' }}>{name}</span>
        </nav>

        <header className="section" style={{ paddingBottom: 18 }}>
          <div className="detail-head">
            {dev.avatarUrl ? (
              <img className="avatar" src={dev.avatarUrl} alt="" />
            ) : (
              <div className="app-hero-ico" aria-hidden="true">👤</div>
            )}
            <div style={{ flex: '1 1 280px', minWidth: 0 }}>
              <h1 style={{ margin: '0 0 4px', fontSize: 30, letterSpacing: '-0.02em' }}>{name}</h1>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                {dev.handle ? <span className="pill mono">@{dev.handle}</span> : null}
                <span className="pill mono" title={dev.address}>{shortAddr(dev.address)}</span>
                {founding ? (
                  <span className="badge founding" title={founding.acceptedAt ? `Accepted ${fmtDate(founding.acceptedAt)}` : undefined}>
                    Founding Developer{founding.seq ? ` #${founding.seq}` : ''}
                  </span>
                ) : null}
                {dev.isAgent ? <span className="badge agent">Agent</span> : null}
              </div>
              {dev.bio ? <p className="muted" style={{ margin: '10px 0 0', fontSize: 14.5, lineHeight: 1.6 }}>{dev.bio}</p> : null}
              <div className="muted" style={{ marginTop: 10, fontSize: 13, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span>Joined {fmtDate(dev.createdAt)}</span>
                {dev.websiteUrl ? (
                  <a href={dev.websiteUrl} rel="noopener nofollow me" target="_blank" style={{ color: 'var(--accent-2)' }}>
                    {dev.websiteUrl.replace(/^https?:\/\//, '')}
                  </a>
                ) : null}
              </div>
            </div>
          </div>
          <div className="kpi" style={{ marginTop: 22 }}>
            <div className="k"><b>{compact(apps.length)}</b><span>published app{apps.length === 1 ? '' : 's'}</span></div>
            <div className="k"><b>{compact(fns.length)}</b><span>public function{fns.length === 1 ? '' : 's'}</span></div>
            <div className="k"><b>{compact(execTotal)}</b><span>executions served</span></div>
            <div className="k"><b>{compact(exec30d)}</b><span>last 30 days</span></div>
          </div>
        </header>

        <section className="section" style={{ paddingTop: 8 }}>
          <h2>Apps</h2>
          {apps.length === 0 ? (
            <div className="empty">No published apps yet.</div>
          ) : (
            <div className="grid" style={{ marginTop: 14 }}>
              {(jsonSafe(apps) as unknown as CloudAppCardData[]).map((a) => (
                <CloudAppCard key={a.slug} a={a} />
              ))}
            </div>
          )}
        </section>

        <section className="section">
          <h2>Public functions</h2>
          {fns.length === 0 ? (
            <div className="empty">No public functions yet.</div>
          ) : (
            <div className="grid" style={{ marginTop: 14 }}>
              {fns.map((f) => (
                <a
                  className="card fn-card"
                  key={f.slug}
                  href={`/functions?fn=${encodeURIComponent(`${key}/${f.slug}`)}#try`}
                >
                  <div className="top">
                    <div className="ico" aria-hidden="true">ƒ</div>
                    <div style={{ minWidth: 0 }}>
                      <h3>{f.name}</h3>
                      <div className="by">v{f.currentVersion} · {compact(f.execCount)} run{f.execCount === 1 ? '' : 's'}</div>
                    </div>
                  </div>
                  {f.description ? <p>{f.description}</p> : null}
                  <div className="ep">POST /api/cloud/v1/fn/{key}/{f.slug}</div>
                  <div className="meta">
                    {BigInt(f.perCallNanm) > 0n ? <span className="badge type">surcharge</span> : <span className="badge free">metered only</span>}
                    {f.requiresAuth ? <span className="badge auth">API key</span> : null}
                    <span style={{ marginLeft: 'auto' }}>Try it →</span>
                  </div>
                </a>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
