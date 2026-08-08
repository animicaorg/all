import type { Metadata } from 'next';
import { Prisma } from '@prisma/client';
import { prisma } from '@/lib/db';
import TryFunctions, { type TryFn } from '@/components/cloud-public/TryFunctions';
import { compact, fmtAnm, shortAddr } from '@/components/cloud-public/fmt';

// /functions — the public directory of every published, public Python Cloud function: its real
// endpoint, its real price terms, its real run count, plus a live "try it" box that invokes the
// selected function against the production execution endpoint. Server-rendered and crawlable;
// ?fn=owner/slug pre-selects a function in the try box (each row's "Try it" link).

export const dynamic = 'force-dynamic';

const BASE = 'https://animica.dev';
const PAGE_SIZE = 60;

export const metadata: Metadata = {
  title: 'Public functions — Animica Python Cloud',
  description:
    'Every public Python function deployed on Animica, callable over plain HTTPS. Deployments are anchored on-chain and executed off-chain in a hardened container; each call is metered in ANM and pays its developer.',
  alternates: { canonical: `${BASE}/functions` },
  openGraph: {
    // Next replaces the parent's whole `openGraph` object when a page declares its
    // own, so the layout's default image does NOT survive here — it has to be named.
    images: ['/og.png'],
    title: 'Animica public functions',
    description: 'Deployed Python functions with public HTTPS endpoints, metered in ANM.',
    url: `${BASE}/functions`,
    siteName: 'Animica',
    type: 'website',
  },
};

interface Params {
  q?: string;
  fn?: string;
  page?: string;
}

export default async function FunctionsPage({ searchParams }: { searchParams?: Params }) {
  const q = typeof searchParams?.q === 'string' ? searchParams.q.trim().slice(0, 120) : '';
  const initialKey = typeof searchParams?.fn === 'string' ? searchParams.fn.trim() : undefined;
  const page = Math.max(1, Math.min(500, Number(searchParams?.page) || 1));

  const where: Prisma.CloudFunctionWhereInput = {
    status: 'PUBLISHED',
    visibility: 'PUBLIC',
    suspendedAt: null,
    ...(q
      ? {
          OR: [
            { name: { contains: q, mode: 'insensitive' } },
            { slug: { contains: q, mode: 'insensitive' } },
            { description: { contains: q, mode: 'insensitive' } },
          ],
        }
      : {}),
  };

  let rows: Array<{
    id: string;
    slug: string;
    name: string;
    description: string;
    requiresAuth: boolean;
    perCallNanm: bigint;
    execCount: number;
    currentVersion: number;
    capabilities: string[];
    app: { slug: string; name: string } | null;
    owner: { handle: string | null; address: string; displayName: string | null };
  }> = [];
  let total = 0;
  let dbOk = true;
  try {
    const [found, count] = await Promise.all([
      prisma.cloudFunction.findMany({
        where,
        orderBy: [{ execCount: 'desc' }, { createdAt: 'desc' }],
        skip: (page - 1) * PAGE_SIZE,
        take: PAGE_SIZE,
        select: {
          id: true, slug: true, name: true, description: true, requiresAuth: true,
          perCallNanm: true, execCount: true, currentVersion: true, capabilities: true,
          app: { select: { slug: true, name: true } },
          owner: { select: { handle: true, address: true, displayName: true } },
        },
      }),
      prisma.cloudFunction.count({ where }),
    ]);
    rows = found;
    total = count;
  } catch {
    dbOk = false;
  }

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const tryFns: TryFn[] = rows.map((f) => {
    const key = `${f.owner.handle ?? f.owner.address}/${f.slug}`;
    return {
      key,
      name: f.name,
      endpoint: `/api/cloud/v1/fn/${encodeURIComponent(f.owner.handle ?? f.owner.address)}/${encodeURIComponent(f.slug)}`,
      requiresAuth: f.requiresAuth,
      priced: f.perCallNanm > 0n,
    };
  });

  return (
    <main>
      <header className="hero" style={{ paddingBottom: 20 }}>
        <div className="wrap">
          <h1 style={{ fontSize: 44 }}>
            Public <span className="grad">functions</span>
          </h1>
          <p className="sub">
            Every function here is real, deployed Python you can call over HTTPS right now.
            Deployments are anchored on-chain (source hash + artifact hash + DA blob id inside a
            signed DEPLOY tx) and executed off-chain in a hardened container. Each call is metered
            and pays the developer in ANM.
          </p>
          <form className="search" action="/functions" method="get" role="search">
            <input type="search" name="q" defaultValue={q} placeholder="Search functions…" aria-label="Search functions" />
            <button className="btn primary" type="submit" style={{ minHeight: 44 }}>Search</button>
          </form>
        </div>
      </header>

      <section className="section" style={{ paddingTop: 0 }}>
        <div className="wrap" style={{ display: 'grid', gap: 22 }}>
          {tryFns.length > 0 ? <TryFunctions functions={tryFns} initialKey={initialKey} /> : null}

          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
              <h2 style={{ margin: 0 }}>{q ? `${total.toLocaleString()} result${total === 1 ? '' : 's'}` : 'Directory'}</h2>
              {!q && dbOk ? (
                <span className="muted" style={{ fontSize: 13 }}>
                  {total.toLocaleString()} public function{total === 1 ? '' : 's'}
                </span>
              ) : null}
              {q ? <a className="pill" href="/functions">clear</a> : null}
            </div>

            {!dbOk ? (
              <div className="empty">The directory is temporarily unavailable. Please try again in a moment.</div>
            ) : rows.length === 0 ? (
              q ? (
                <div className="empty">No public functions match “{q}”.</div>
              ) : (
                <div className="empty">
                  <div style={{ fontSize: 17, color: 'var(--text)', marginBottom: 6 }}>No public functions yet.</div>
                  Deploy a Python function and make it public — it appears here with a live endpoint.
                  <div style={{ marginTop: 16, display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
                    <a className="btn primary" href="/cloud">Open the developer console</a>
                    <a className="btn ghost" href="/docs">Read the docs</a>
                  </div>
                </div>
              )
            ) : (
              <div className="grid">
                {rows.map((f) => {
                  const ownerKey = f.owner.handle ?? f.owner.address;
                  const by = f.owner.displayName || f.owner.handle || shortAddr(f.owner.address);
                  return (
                    <div className="card fn-card" key={f.id}>
                      <div className="top">
                        <div className="ico" aria-hidden="true">ƒ</div>
                        <div style={{ minWidth: 0 }}>
                          <h3>{f.name}</h3>
                          <div className="by">
                            by <a href={`/developers/${encodeURIComponent(ownerKey)}`} style={{ color: 'var(--accent-2)' }}>{by}</a>
                            {' · '}v{f.currentVersion}
                          </div>
                        </div>
                        <div className="price-tag" style={{ textAlign: 'right', fontSize: 13 }}>
                          {f.perCallNanm > 0n ? <>{fmtAnm(f.perCallNanm)} <small>ANM/call + metered</small></> : <>metered</>}
                        </div>
                      </div>
                      {f.description ? <p>{f.description}</p> : null}
                      <div className="ep">POST {BASE}/api/cloud/v1/fn/{ownerKey}/{f.slug}</div>
                      <div className="meta">
                        {f.requiresAuth ? <span className="badge auth">API key</span> : <span className="badge free">open</span>}
                        {f.capabilities.length > 0 ? (
                          <span title={f.capabilities.join(', ')}>{f.capabilities.length} capabilit{f.capabilities.length === 1 ? 'y' : 'ies'}</span>
                        ) : null}
                        {f.app ? <a href={`/apps/${encodeURIComponent(f.app.slug)}`} style={{ color: 'var(--text-dim)' }}>in {f.app.name}</a> : null}
                        <span style={{ marginLeft: 'auto' }}>{compact(f.execCount)} run{f.execCount === 1 ? '' : 's'}</span>
                      </div>
                      <div>
                        <a
                          className="btn ghost"
                          style={{ minHeight: 40, fontSize: 13 }}
                          href={`/functions?${new URLSearchParams({ ...(q ? { q } : {}), fn: `${ownerKey}/${f.slug}` }).toString()}#try`}
                        >
                          Try it →
                        </a>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {pages > 1 ? (
              <nav className="pager" aria-label="Pagination">
                {page > 1 ? (
                  <a className="btn ghost" href={`/functions?${new URLSearchParams({ ...(q ? { q } : {}), page: String(page - 1) }).toString()}`} rel="prev">← Prev</a>
                ) : null}
                <span className="muted" style={{ fontSize: 13 }}>page {page} of {pages}</span>
                {page < pages ? (
                  <a className="btn ghost" href={`/functions?${new URLSearchParams({ ...(q ? { q } : {}), page: String(page + 1) }).toString()}`} rel="next">Next →</a>
                ) : null}
              </nav>
            ) : null}
          </div>
        </div>
      </section>
    </main>
  );
}
