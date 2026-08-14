import type { Metadata } from 'next';
import { Prisma } from '@prisma/client';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';
import CloudAppCard, { type CloudAppCardData } from '@/components/cloud-public/CloudAppCard';
import { CLOUD_CATEGORIES, compact } from '@/components/cloud-public/fmt';

// The Animica Python Cloud app marketplace (spec §14). Fully server-rendered so every filter
// combination is a crawlable URL and works without JavaScript. Popularity comes from the real
// execution/install counters (schema-blessed caches recomputable from CloudExecution /
// CloudAppPurchase); the platform KPIs are live COUNT(*) queries.

export const dynamic = 'force-dynamic';

const BASE = 'https://animica.dev';
const PAGE_SIZE = 24;

export const metadata: Metadata = {
  title: 'App marketplace — Animica Python Cloud',
  description:
    'Browse Python apps deployed on Animica: AI tools, agents, automations, data services and more. Every deployment is anchored on-chain and executed off-chain in a hardened container; usage settles in ANM.',
  alternates: { canonical: `${BASE}/apps` },
  openGraph: {
    title: 'Animica app marketplace',
    description: 'Python apps deployed on Animica — anchored on-chain, metered in ANM.',
    url: `${BASE}/apps`,
    siteName: 'Animica',
    type: 'website',
  },
};

const PRICE_FILTERS = [
  { key: 'free', label: 'Free' },
  { key: 'paid', label: 'Paid' },
  { key: 'payperuse', label: 'Pay-per-use' },
] as const;

interface Params {
  q?: string;
  cat?: string;
  price?: string;
  sort?: string;
  page?: string;
}

function qs(base: Params, next: Partial<Record<keyof Params, string | undefined>>): string {
  const merged: Record<string, string | undefined> = { ...base, ...next };
  if (!('page' in next)) delete merged.page; // any filter change resets pagination
  const sp = new URLSearchParams();
  for (const k of ['q', 'cat', 'price', 'sort', 'page']) {
    const v = merged[k];
    if (v) sp.set(k, v);
  }
  const s = sp.toString();
  return s ? `/apps?${s}` : '/apps';
}

export default async function AppsPage({ searchParams }: { searchParams?: Params }) {
  const p: Params = {
    q: typeof searchParams?.q === 'string' ? searchParams.q.trim().slice(0, 120) : undefined,
    cat: CLOUD_CATEGORIES.some((c) => c.value === searchParams?.cat) ? searchParams?.cat : undefined,
    price: PRICE_FILTERS.some((f) => f.key === searchParams?.price) ? searchParams?.price : undefined,
    sort: searchParams?.sort === 'popular' ? 'popular' : undefined, // default: newest
    page: searchParams?.page,
  };
  const page = Math.max(1, Math.min(500, Number(p.page) || 1));

  const where: Prisma.CloudAppWhereInput = {
    status: 'PUBLISHED',
    visibility: 'PUBLIC',
    suspendedAt: null,
    ...(p.cat ? { category: p.cat as any } : {}),
    ...(p.price === 'free'
      ? { pricingModel: 'FREE' as const }
      : p.price === 'paid'
        ? { pricingModel: { in: ['ONE_TIME', 'SUBSCRIPTION'] as any } }
        : p.price === 'payperuse'
          ? { pricingModel: 'PAY_PER_USE' as const }
          : {}),
    ...(p.q
      ? {
          OR: [
            { name: { contains: p.q, mode: 'insensitive' } },
            { tagline: { contains: p.q, mode: 'insensitive' } },
            { description: { contains: p.q, mode: 'insensitive' } },
            { tags: { has: p.q.toLowerCase() } },
          ],
        }
      : {}),
  };

  const orderBy: Prisma.CloudAppOrderByWithRelationInput[] =
    p.sort === 'popular'
      ? [{ execCount: 'desc' }, { installCount: 'desc' }, { publishedAt: 'desc' }]
      : [{ publishedAt: 'desc' }];

  let rows: CloudAppCardData[] = [];
  let total = 0;
  let kpi: { apps: number; developers: number; executions: number } | null = null;
  let dbOk = true;
  try {
    const publicWhere: Prisma.CloudAppWhereInput = { status: 'PUBLISHED', visibility: 'PUBLIC', suspendedAt: null };
    const [found, count, appsTotal, devGroups, execTotal] = await Promise.all([
      prisma.cloudApp.findMany({
        where,
        orderBy,
        skip: (page - 1) * PAGE_SIZE,
        take: PAGE_SIZE,
        select: {
          slug: true, name: true, tagline: true, category: true, iconEmoji: true, iconUrl: true,
          pricingModel: true, priceNanm: true, execCount: true, installCount: true,
          ratingSum: true, ratingCount: true,
          owner: { select: { handle: true, displayName: true, address: true } },
        },
      }),
      prisma.cloudApp.count({ where }),
      prisma.cloudApp.count({ where: publicWhere }),
      prisma.cloudApp.groupBy({ by: ['ownerId'], where: publicWhere }),
      prisma.cloudExecution.count(),
    ]);
    rows = jsonSafe(found) as unknown as CloudAppCardData[];
    total = count;
    kpi = { apps: appsTotal, developers: devGroups.length, executions: execTotal };
  } catch {
    dbOk = false;
  }

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = Boolean(p.q || p.cat || p.price);

  return (
    <main>
      <header className="hero" style={{ paddingBottom: 10 }}>
        <div className="wrap">
          <h1 style={{ fontSize: 44 }}>
            The <span className="grad">Python app</span> marketplace
          </h1>
          <p className="sub">
            Apps written in Python, deployed to Animica. Deployments are anchored on-chain (source
            hash + artifact hash + DA blob id inside a signed DEPLOY tx) and executed off-chain in a
            hardened container — every run is metered and settles in ANM, straight to the developer.
          </p>
          {kpi ? (
            <div className="kpi" style={{ marginBottom: 20 }}>
              <div className="k"><b>{compact(kpi.apps)}</b><span>published app{kpi.apps === 1 ? '' : 's'}</span></div>
              <div className="k"><b>{compact(kpi.developers)}</b><span>developer{kpi.developers === 1 ? '' : 's'}</span></div>
              <div className="k"><b>{compact(kpi.executions)}</b><span>executions served</span></div>
            </div>
          ) : null}
          <form className="search" action="/apps" method="get" role="search">
            <input
              type="search"
              name="q"
              defaultValue={p.q ?? ''}
              placeholder="Search apps…"
              aria-label="Search apps"
            />
            {p.cat ? <input type="hidden" name="cat" value={p.cat} /> : null}
            {p.price ? <input type="hidden" name="price" value={p.price} /> : null}
            {p.sort ? <input type="hidden" name="sort" value={p.sort} /> : null}
            <button className="btn primary" type="submit" style={{ minHeight: 44 }}>Search</button>
          </form>
          <div className="chips" role="navigation" aria-label="Filters">
            <a className={'chip' + (!p.price && !p.cat ? ' active' : '')} href={qs(p, { price: undefined, cat: undefined })}>All</a>
            {PRICE_FILTERS.map((f) => (
              <a key={f.key} className={'chip' + (p.price === f.key ? ' active' : '')} href={qs(p, { price: p.price === f.key ? undefined : f.key })}>
                {f.label}
              </a>
            ))}
            {CLOUD_CATEGORIES.map((c) => (
              <a key={c.value} className={'chip' + (p.cat === c.value ? ' active' : '')} href={qs(p, { cat: p.cat === c.value ? undefined : c.value })}>
                {c.label}
              </a>
            ))}
          </div>
        </div>
      </header>

      <section className="section" style={{ paddingTop: 10 }}>
        <div className="wrap">
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap', marginBottom: 16 }}>
            <h2 style={{ margin: 0 }}>
              {filtered ? `${total.toLocaleString()} result${total === 1 ? '' : 's'}` : 'Browse apps'}
            </h2>
            <span className="muted" style={{ fontSize: 13 }}>sort:</span>
            <a className={'pill' + (p.sort !== 'popular' ? '' : '')} style={p.sort !== 'popular' ? { color: 'var(--text)', borderColor: 'var(--accent)' } : undefined} href={qs(p, { sort: undefined })}>Newest</a>
            <a className="pill" style={p.sort === 'popular' ? { color: 'var(--text)', borderColor: 'var(--accent)' } : undefined} href={qs(p, { sort: 'popular' })}>Popular</a>
          </div>

          {!dbOk ? (
            <div className="empty">The catalog is temporarily unavailable. Please try again in a moment.</div>
          ) : rows.length === 0 ? (
            filtered ? (
              <div className="empty">
                No apps match{p.q ? <> “{p.q}”</> : null}{p.cat ? <> in {CLOUD_CATEGORIES.find((c) => c.value === p.cat)?.label}</> : null}.
                <div style={{ marginTop: 14 }}>
                  <a className="btn ghost" href="/apps">Clear filters</a>
                </div>
              </div>
            ) : (
              <div className="empty">
                <div style={{ fontSize: 17, color: 'var(--text)', marginBottom: 6 }}>The catalog is open — nothing is published yet.</div>
                Write a Python function, deploy it to Animica, publish it as an app, and it appears here.
                <div style={{ marginTop: 16, display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
                  <a className="btn primary" href="/cloud">Open the developer console</a>
                  <a className="btn ghost" href="/docs">Read the docs</a>
                </div>
              </div>
            )
          ) : (
            <>
              <div className="grid">
                {rows.map((a) => (
                  <CloudAppCard key={a.slug} a={a} />
                ))}
              </div>
              {pages > 1 ? (
                <nav className="pager" aria-label="Pagination">
                  {page > 1 ? (
                    <a className="btn ghost" href={qs(p, { page: String(page - 1) })} rel="prev">← Prev</a>
                  ) : null}
                  <span className="muted" style={{ fontSize: 13 }}>page {page} of {pages}</span>
                  {page < pages ? (
                    <a className="btn ghost" href={qs(p, { page: String(page + 1) })} rel="next">Next →</a>
                  ) : null}
                </nav>
              ) : null}
            </>
          )}
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="panel" style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 320px' }}>
              <h2 style={{ margin: '0 0 4px' }}>Ship your own app</h2>
              <p className="muted" style={{ margin: 0, fontSize: 14 }}>
                Deploy a Python function, publish it here, and earn ANM on every call — the platform
                fee and your share are settled per execution on an append-only ledger.
              </p>
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <a className="btn primary" style={{ minHeight: 44 }} href="/cloud">Start deploying</a>
              <a className="btn ghost" style={{ minHeight: 44 }} href="/functions">Browse functions</a>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
