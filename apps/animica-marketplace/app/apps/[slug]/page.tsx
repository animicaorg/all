import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { cache } from 'react';
import { prisma } from '@/lib/db';
import { formatAnm, nanmToAnm } from '@/lib/nanm';
import { activePolicy } from '@/lib/cloud/pricing';
import Md from '@/components/cloud-public/Md';
import ShareButton from '@/components/cloud-public/ShareButton';
import RunFunction from '@/components/cloud-public/RunFunction';
import { capabilityInfo } from '@/components/cloud-public/caps';
import {
  categoryLabel, compact, fmtAnmExact, fmtDate, priceLabel, shortAddr, shortHash,
} from '@/components/cloud-public/fmt';

// /apps/[slug] — the shareable, indexable page for one published CloudApp (spec §15).
// Server-rendered from live DB rows: usage numbers are authoritative aggregates over
// CloudExecution, ratings come only from real CloudReview rows, and the deployment panel
// shows the actual on-chain anchor of the current version.

export const dynamic = 'force-dynamic';

const BASE = 'https://animica.dev';

const getApp = cache(async (slug: string) => {
  const s = decodeURIComponent(slug).trim().toLowerCase();
  const app = await prisma.cloudApp.findUnique({
    where: { slug: s },
    include: {
      owner: {
        select: {
          id: true, handle: true, displayName: true, address: true, avatarUrl: true,
          createdAt: true,
          foundingDev: { select: { status: true, seq: true } },
        },
      },
      functions: {
        where: { status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null },
        orderBy: { createdAt: 'asc' },
        select: {
          id: true, slug: true, name: true, description: true, runtime: true, entrypoint: true,
          timeoutMs: true, memoryMb: true, capabilities: true, perCallNanm: true,
          requiresAuth: true, currentVersion: true, execCount: true,
        },
      },
    },
  });
  if (!app) return null;
  if (app.status !== 'PUBLISHED' || app.visibility === 'PRIVATE' || app.suspendedAt) return null;
  return app;
});

export async function generateMetadata({ params }: { params: { slug: string } }): Promise<Metadata> {
  const app = await getApp(params.slug).catch(() => null);
  if (!app) return { title: 'App not found — Animica', robots: { index: false } };
  const url = `${BASE}/apps/${app.slug}`;
  const desc =
    (app.tagline || app.description || `${app.name} — a Python app deployed on Animica.`).slice(0, 300);
  const images = app.bannerUrl ? [app.bannerUrl] : app.iconUrl ? [app.iconUrl] : undefined;
  return {
    title: `${app.name} — Animica app`,
    description: desc,
    alternates: { canonical: url },
    ...(app.visibility === 'UNLISTED' ? { robots: { index: false, follow: false } } : {}),
    openGraph: {
    // Next replaces the parent's whole `openGraph` object when a page declares its
    // own, so the layout's default image does NOT survive here — it has to be named.
    images: ['/og.png'],
      title: app.name,
      description: desc,
      url,
      siteName: 'Animica',
      type: 'website',
      ...(images ? { images } : {}),
    },
    twitter: {
      card: images ? 'summary_large_image' : 'summary',
      title: app.name,
      description: desc,
      ...(images ? { images } : {}),
    },
  };
}

export default async function AppPage({ params }: { params: { slug: string } }) {
  const app = await getApp(params.slug);
  if (!app) notFound();

  const since30 = new Date(Date.now() - 30 * 24 * 3600 * 1000);
  const [execTotal, execOk, exec30d, durAgg, reviewAgg, reviews, users, ownerAppCount, policy] =
    await Promise.all([
      prisma.cloudExecution.count({ where: { appId: app.id } }),
      prisma.cloudExecution.count({ where: { appId: app.id, status: 'SUCCEEDED' } }),
      prisma.cloudExecution.count({ where: { appId: app.id, createdAt: { gte: since30 } } }),
      prisma.cloudExecution.aggregate({
        where: { appId: app.id, status: 'SUCCEEDED' },
        _avg: { durationMs: true },
      }),
      prisma.cloudReview.aggregate({
        where: { appId: app.id, hidden: false },
        _avg: { rating: true },
        _count: { _all: true },
      }),
      prisma.cloudReview.findMany({
        where: { appId: app.id, hidden: false },
        orderBy: { createdAt: 'desc' },
        take: 12,
        include: { account: { select: { handle: true, displayName: true, address: true } } },
      }),
      prisma.cloudAppPurchase.count({ where: { appId: app.id, status: 'ACTIVE' } }),
      prisma.cloudApp.count({
        where: { ownerId: app.ownerId, status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null },
      }),
      activePolicy(),
    ]);

  const primaryFn = app.functions[0] ?? null;
  const deployment = primaryFn
    ? await prisma.cloudDeployment.findFirst({
        where: { functionId: primaryFn.id, status: 'ACTIVE' },
        orderBy: [{ activatedAt: 'desc' }, { createdAt: 'desc' }],
        select: {
          anchorTxid: true, anchorHeight: true, anchorConfirms: true, daBlobId: true,
          deployerAddress: true, activatedAt: true,
          version: { select: { version: true, sourceSha3: true, artifactSha3: true, sizeBytes: true } },
        },
      })
    : null;

  const ownerKey = app.owner.handle ?? app.owner.address;
  const ownerName = app.owner.displayName || app.owner.handle || shortAddr(app.owner.address);
  const founding = app.owner.foundingDev?.status === 'ACCEPTED' ? app.owner.foundingDev : null;
  const url = `${BASE}/apps/${app.slug}`;
  const price = priceLabel(app.pricingModel, app.priceNanm);
  const capabilities = Array.from(
    new Set([...app.capabilities, ...app.functions.flatMap((f) => f.capabilities)]),
  );
  const ratingCount = reviewAgg._count._all;
  const ratingAvg = ratingCount > 0 ? Number((reviewAgg._avg.rating ?? 0).toFixed(2)) : null;
  const endpointPath = primaryFn
    ? `/api/cloud/v1/fn/${encodeURIComponent(ownerKey)}/${encodeURIComponent(primaryFn.slug)}`
    : null;
  const fnPriced =
    (primaryFn?.perCallNanm ?? 0n) > 0n ||
    app.pricingModel === 'ONE_TIME' ||
    app.pricingModel === 'SUBSCRIPTION';

  // schema.org SoftwareApplication. aggregateRating ONLY when real review rows exist; the
  // offer price is the app's real current terms (base metered call price for pay-per-use).
  const offerAnm =
    app.pricingModel === 'FREE'
      ? '0'
      : app.pricingModel === 'PAY_PER_USE'
        ? nanmToAnm(policy.baseCallNanm)
        : nanmToAnm(app.priceNanm);
  const ld: Record<string, unknown> = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: app.name,
    description: (app.tagline || app.description || app.name).slice(0, 500),
    url,
    applicationCategory: categoryLabel(app.category),
    operatingSystem: 'Any (HTTP API)',
    author: {
      '@type': 'Person',
      name: ownerName,
      url: `${BASE}/developers/${encodeURIComponent(ownerKey)}`,
    },
    offers: {
      '@type': 'Offer',
      price: offerAnm,
      priceCurrency: 'ANM',
      availability: 'https://schema.org/InStock',
      ...(app.pricingModel === 'PAY_PER_USE' ? { description: 'Metered pay-per-use; price shown is the base fee per call in ANM.' } : {}),
    },
    ...(app.publishedAt ? { datePublished: app.publishedAt.toISOString() } : {}),
    ...(app.iconUrl ? { image: app.iconUrl } : {}),
    ...(ratingAvg != null
      ? { aggregateRating: { '@type': 'AggregateRating', ratingValue: ratingAvg, ratingCount, bestRating: 5, worstRating: 1 } }
      : {}),
  };

  const curl = endpointPath
    ? [
        `curl -X POST ${BASE}${endpointPath} \\`,
        `  -H 'content-type: application/json' \\`,
        ...(primaryFn!.requiresAuth || fnPriced ? [`  -H 'authorization: Bearer YOUR_API_KEY' \\`] : []),
        `  -d '{}'`,
      ].join('\n')
    : null;

  return (
    <main>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(ld).replace(/</g, '\\u003c') }}
      />
      <div className="wrap">
        <nav className="crumbs" aria-label="Breadcrumb">
          <a href="/apps">Apps</a>
          <span>/</span>
          <a href={`/apps?cat=${app.category}`}>{categoryLabel(app.category)}</a>
          <span>/</span>
          <span style={{ color: 'var(--text-dim)' }}>{app.name}</span>
        </nav>

        <header className="section" style={{ paddingBottom: 16 }}>
          <div className="detail-head">
            <div className="app-hero-ico" aria-hidden="true">
              {app.iconUrl ? <img src={app.iconUrl} alt="" /> : app.iconEmoji || '🐍'}
            </div>
            <div style={{ flex: '1 1 260px', minWidth: 0 }}>
              <h1 style={{ margin: '0 0 4px', fontSize: 30, letterSpacing: '-0.02em' }}>{app.name}</h1>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                <a href={`/developers/${encodeURIComponent(ownerKey)}`} style={{ color: 'var(--accent-2)', fontSize: 14 }}>
                  by {ownerName}
                </a>
                {founding ? <span className="badge founding">Founding Developer{founding.seq ? ` #${founding.seq}` : ''}</span> : null}
                <span className="badge type">{categoryLabel(app.category)}</span>
                {app.pricingModel === 'FREE' ? <span className="badge free">Free</span> : null}
              </div>
              {app.tagline ? <p className="muted" style={{ margin: '10px 0 0', fontSize: 15.5, lineHeight: 1.5 }}>{app.tagline}</p> : null}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-end' }}>
              <div className="price-tag" style={{ fontSize: 18 }}>
                {price.text} {price.sub ? <small>{price.sub}</small> : null}
              </div>
              <ShareButton url={url} title={`${app.name} — Animica app`} text={app.tagline || app.name} />
            </div>
          </div>
          <div className="kpi" style={{ marginTop: 22 }}>
            <div className="k"><b>{compact(execTotal)}</b><span>total runs</span></div>
            <div className="k"><b>{compact(exec30d)}</b><span>runs, last 30 days</span></div>
            <div className="k">
              <b>{execTotal > 0 ? `${Math.round((execOk / execTotal) * 100)}%` : '—'}</b>
              <span>success rate</span>
            </div>
            <div className="k">
              <b>{durAgg._avg.durationMs != null ? `${Math.round(durAgg._avg.durationMs)}ms` : '—'}</b>
              <span>avg duration</span>
            </div>
            {users > 0 ? <div className="k"><b>{compact(users)}</b><span>active user{users === 1 ? '' : 's'}</span></div> : null}
            {ratingAvg != null ? <div className="k"><b>★ {ratingAvg.toFixed(1)}</b><span>{ratingCount} review{ratingCount === 1 ? '' : 's'}</span></div> : null}
          </div>
        </header>

        {app.bannerUrl ? (
          <section style={{ marginBottom: 26 }}>
            <img
              src={app.bannerUrl}
              alt={`${app.name} screenshot`}
              style={{ width: '100%', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}
            />
          </section>
        ) : null}

        <div className="side-grid" style={{ marginBottom: 40 }}>
          <div style={{ display: 'grid', gap: 22, minWidth: 0 }}>
            {app.description ? (
              <section className="panel">
                <h2 style={{ margin: '0 0 10px', fontSize: 20 }}>About</h2>
                <p className="muted" style={{ margin: 0, fontSize: 14.5, lineHeight: 1.7, whiteSpace: 'pre-line' }}>
                  {app.description}
                </p>
              </section>
            ) : null}

            {primaryFn && endpointPath ? (
              <section className="panel" id="run">
                <h2 style={{ margin: '0 0 4px', fontSize: 20 }}>Launch</h2>
                <p className="muted" style={{ margin: '0 0 14px', fontSize: 13.5 }}>
                  Runs <span className="mono">{ownerKey}/{primaryFn.slug}</span> live in the hardened
                  sandbox. {capabilities.some((c) => capabilityInfo(c).sensitive)
                    ? 'Sensitive capabilities listed below only ever act with your explicit, revocable authorization.'
                    : ''}
                </p>
                <RunFunction
                  endpoint={endpointPath}
                  requiresAuth={primaryFn.requiresAuth}
                  priced={fnPriced}
                  buttonLabel="Run this app"
                />
              </section>
            ) : (
              <section className="panel">
                <h2 style={{ margin: '0 0 8px', fontSize: 20 }}>Launch</h2>
                <div className="empty">This app has no public function endpoint right now.</div>
              </section>
            )}

            {app.docsMd ? (
              <section className="panel" id="docs">
                <h2 style={{ margin: '0 0 6px', fontSize: 20 }}>Documentation</h2>
                <Md src={app.docsMd} />
              </section>
            ) : null}

            {curl && endpointPath ? (
              <section className="panel" id="api">
                <h2 style={{ margin: '0 0 6px', fontSize: 20 }}>API</h2>
                <p className="muted" style={{ margin: '0 0 12px', fontSize: 13.5 }}>
                  Call it from anywhere — the endpoint speaks plain JSON over HTTPS and returns the
                  execution&apos;s request id and metered cost in <span className="mono">X-Animica-*</span> headers.
                </p>
                <pre className="codebox">{curl}</pre>
                {app.functions.length > 1 ? (
                  <div style={{ marginTop: 14 }}>
                    <div className="muted" style={{ fontSize: 12.5, marginBottom: 6 }}>All endpoints in this app:</div>
                    {app.functions.map((f) => (
                      <div key={f.id} className="mono" style={{ fontSize: 12.5, padding: '4px 0', wordBreak: 'break-all' }}>
                        POST {BASE}/api/cloud/v1/fn/{ownerKey}/{f.slug}
                      </div>
                    ))}
                  </div>
                ) : null}
              </section>
            ) : null}

            <section className="panel" id="reviews">
              <h2 style={{ margin: '0 0 6px', fontSize: 20 }}>
                Reviews{ratingCount > 0 ? ` · ★ ${ratingAvg!.toFixed(1)} (${ratingCount})` : ''}
              </h2>
              {reviews.length === 0 ? (
                <div className="empty">No reviews yet. Run the app, then leave the first one from your account.</div>
              ) : (
                reviews.map((r) => (
                  <div className="review" key={r.id}>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span className="stars" aria-label={`${r.rating} out of 5`}>
                        {'★'.repeat(Math.max(1, Math.min(5, r.rating)))}{'☆'.repeat(5 - Math.max(1, Math.min(5, r.rating)))}
                      </span>
                      <b style={{ fontSize: 13.5 }}>
                        {r.account.displayName || r.account.handle || shortAddr(r.account.address)}
                      </b>
                      <span className="muted" style={{ fontSize: 12 }}>{fmtDate(r.createdAt)}</span>
                    </div>
                    {r.body ? <p>{r.body}</p> : null}
                  </div>
                ))
              )}
            </section>
          </div>

          <aside style={{ display: 'grid', gap: 22, minWidth: 0 }}>
            <section className="panel">
              <h2 style={{ margin: '0 0 10px', fontSize: 16 }}>Pricing</h2>
              {app.pricingModel === 'FREE' ? (
                <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
                  Free to use. Anonymous calls draw from the shared free tier; signed-in usage from
                  your account allowances.
                </p>
              ) : app.pricingModel === 'PAY_PER_USE' ? (
                <>
                  <p className="muted" style={{ margin: '0 0 10px', fontSize: 13.5 }}>
                    Metered per execution at the current platform rates:
                  </p>
                  <dl className="kv">
                    <dt>Base per call</dt><dd>{fmtAnmExact(policy.baseCallNanm)} ANM</dd>
                    <dt>CPU</dt><dd>{fmtAnmExact(policy.cpuMsNanm)} ANM / ms</dd>
                    <dt>Memory</dt><dd>{fmtAnmExact(policy.memMbMsNanm)} ANM / MB·ms</dd>
                    <dt>AI tokens in</dt><dd>{fmtAnmExact(policy.aiTokenInNanm)} ANM / token</dd>
                    <dt>AI tokens out</dt><dd>{fmtAnmExact(policy.aiTokenOutNanm)} ANM / token</dd>
                    <dt>Egress</dt><dd>{fmtAnmExact(policy.egressKbNanm)} ANM / KB</dd>
                  </dl>
                  {(primaryFn?.perCallNanm ?? 0n) > 0n ? (
                    <p className="muted" style={{ margin: '10px 0 0', fontSize: 13 }}>
                      Plus the developer&apos;s surcharge of {formatAnm(primaryFn!.perCallNanm)} ANM per call.
                    </p>
                  ) : null}
                </>
              ) : app.pricingModel === 'ONE_TIME' ? (
                <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
                  One-time purchase of <b style={{ color: 'var(--text)' }}>{formatAnm(app.priceNanm)} ANM</b>,
                  then pay-per-use at platform cost.
                </p>
              ) : (
                <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
                  Subscription: <b style={{ color: 'var(--text)' }}>{formatAnm(app.priceNanm)} ANM</b> per month.
                </p>
              )}
            </section>

            <section className="panel" id="capabilities">
              <h2 style={{ margin: '0 0 4px', fontSize: 16 }}>Capabilities</h2>
              <p className="muted" style={{ margin: '0 0 6px', fontSize: 12.5 }}>
                Exactly what this app is allowed to do. Sensitive capabilities require your explicit,
                revocable authorization before the app can use them.
              </p>
              {capabilities.length === 0 ? (
                <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                  None — pure compute. This app can only read its request and return a response.
                </p>
              ) : (
                capabilities.map((c) => {
                  const info = capabilityInfo(c);
                  return (
                    <div className="cap-item" key={c}>
                      <span className="cap-ico" aria-hidden="true">{info.icon}</span>
                      <div>
                        <b>
                          {info.label}{' '}
                          {info.sensitive ? (
                            <span className="badge auth" title="Requires your explicit grant">requires grant</span>
                          ) : null}
                        </b>
                        <p>{info.desc}</p>
                      </div>
                    </div>
                  );
                })
              )}
            </section>

            <section className="panel" id="deployment">
              <h2 style={{ margin: '0 0 4px', fontSize: 16 }}>Deployment</h2>
              <p className="muted" style={{ margin: '0 0 12px', fontSize: 12.5 }}>
                Deployments are anchored on-chain (source hash + artifact hash + DA blob id inside a
                signed DEPLOY tx) and executed off-chain in a hardened container.
              </p>
              {primaryFn ? (
                <dl className="kv">
                  <dt>Version</dt><dd>v{deployment?.version.version ?? primaryFn.currentVersion}</dd>
                  <dt>Runtime</dt><dd className="mono">{primaryFn.runtime}</dd>
                  <dt>Entrypoint</dt><dd className="mono">{primaryFn.entrypoint}()</dd>
                  <dt>Limits</dt><dd>{primaryFn.timeoutMs / 1000}s · {primaryFn.memoryMb}MB</dd>
                  {deployment?.activatedAt ? (<><dt>Activated</dt><dd>{fmtDate(deployment.activatedAt)}</dd></>) : null}
                  {deployment?.version.sourceSha3 ? (<><dt>Source sha3</dt><dd className="mono" title={deployment.version.sourceSha3}>{shortHash(deployment.version.sourceSha3, 22)}</dd></>) : null}
                  {deployment?.version.artifactSha3 ? (<><dt>Artifact sha3</dt><dd className="mono" title={deployment.version.artifactSha3}>{shortHash(deployment.version.artifactSha3, 22)}</dd></>) : null}
                  {deployment?.daBlobId ? (<><dt>DA blob</dt><dd className="mono" title={deployment.daBlobId}>{shortHash(deployment.daBlobId, 22)}</dd></>) : null}
                  {deployment?.anchorTxid ? (
                    <>
                      <dt>Anchor tx</dt><dd className="mono" title={deployment.anchorTxid}>{shortHash(deployment.anchorTxid, 22)}</dd>
                      {deployment.anchorHeight != null ? (<><dt>Block height</dt><dd>{deployment.anchorHeight.toLocaleString()} ({deployment.anchorConfirms} confirmations)</dd></>) : null}
                      {deployment.deployerAddress ? (<><dt>Signed by</dt><dd className="mono" title={deployment.deployerAddress}>{shortAddr(deployment.deployerAddress)}</dd></>) : null}
                    </>
                  ) : (
                    <>
                      <dt>Anchor</dt><dd className="muted">no confirmed on-chain anchor recorded for the active version</dd>
                    </>
                  )}
                </dl>
              ) : (
                <p className="muted" style={{ margin: 0, fontSize: 13 }}>No public function is attached to this app.</p>
              )}
            </section>

            <section className="panel">
              <h2 style={{ margin: '0 0 10px', fontSize: 16 }}>Developer</h2>
              <a
                href={`/developers/${encodeURIComponent(ownerKey)}`}
                style={{ display: 'flex', gap: 12, alignItems: 'center' }}
              >
                {app.owner.avatarUrl ? (
                  <img className="avatar" src={app.owner.avatarUrl} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />
                ) : (
                  <div className="ico" aria-hidden="true">👤</div>
                )}
                <div style={{ minWidth: 0 }}>
                  <b style={{ fontSize: 14.5 }}>{ownerName}</b>
                  <div className="muted" style={{ fontSize: 12.5 }}>
                    {ownerAppCount} published app{ownerAppCount === 1 ? '' : 's'} · joined {fmtDate(app.owner.createdAt)}
                  </div>
                </div>
              </a>
            </section>
          </aside>
        </div>
      </div>
    </main>
  );
}
