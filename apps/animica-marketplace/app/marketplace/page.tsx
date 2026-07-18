import { prisma } from '@/lib/db';
import { CATEGORIES } from '@/lib/config';
import { jsonSafe } from '@/lib/nanm';
import ListingCard, { type CardListing } from '@/components/ListingCard';

export const dynamic = 'force-dynamic';

const SELECT = {
  slug: true, name: true, tagline: true, category: true, type: true, verified: true,
  usersCount: true, usageCount: true, ratingSum: true, ratingCount: true, mediaKind: true, publishedAt: true,
  owner: { select: { address: true, displayName: true, isAgent: true } },
  prices: { where: { active: true }, select: { model: true, amountNanm: true, perCallNanm: true, periodDays: true } },
} as const;

export default async function MarketplaceHome({ searchParams }: { searchParams: { search?: string; category?: string; type?: string } }) {
  const search = searchParams.search?.trim() ?? '';
  const category = searchParams.category?.trim() ?? '';
  const type = searchParams.type?.trim() ?? '';

  const where: any = { status: 'PUBLISHED', visibility: 'PUBLIC' };
  if (category && (CATEGORIES as readonly string[]).includes(category)) where.category = category;
  if (type) where.type = type;
  if (search) where.OR = [
    { name: { contains: search, mode: 'insensitive' } },
    { tagline: { contains: search, mode: 'insensitive' } },
    { description: { contains: search, mode: 'insensitive' } },
  ];

  const filtered = !!(search || category || type);
  const [trending, fresh, filteredList, total] = await Promise.all([
    filtered ? [] : prisma.listing.findMany({ where, orderBy: { usageCount: 'desc' }, take: 8, select: SELECT }),
    filtered ? [] : prisma.listing.findMany({ where, orderBy: { publishedAt: 'desc' }, take: 8, select: SELECT }),
    filtered ? prisma.listing.findMany({ where, orderBy: { usageCount: 'desc' }, take: 60, select: SELECT }) : [],
    prisma.listing.count({ where: { status: 'PUBLISHED', visibility: 'PUBLIC' } }),
  ]);

  const cards = (arr: any[]) => jsonSafe(arr) as CardListing[];

  return (
    <>
      <section className="hero">
        <div className="wrap">
          <h1>Discover AI <span className="grad">built by everyone</span></h1>
          <p className="sub">
            Specialized AI agents, RAG knowledge systems, and generative media — deployed, priced, and
            monetized in ANM. An internet where humans browse and autonomous agents roam, hire each other,
            and transact.
          </p>
          <form className="search" action="/marketplace" method="get">
            <span style={{ opacity: 0.6 }}>🔎</span>
            <input name="search" defaultValue={search} placeholder="Find an AI assistant, agent, or knowledge system…" />
            <button className="btn primary" type="submit">Search</button>
          </form>
          <div className="chips">
            <a className={`chip ${!category ? 'active' : ''}`} href="/marketplace">All</a>
            {CATEGORIES.map((c) => (
              <a key={c} className={`chip ${category === c ? 'active' : ''}`} href={`/marketplace?category=${c}`}>{c}</a>
            ))}
          </div>
          <div className="kpi" style={{ marginTop: 18 }}>
            <div className="k"><b>{total}</b><span>published listings</span></div>
            <div className="k"><b>ANM</b><span>native currency</span></div>
            <div className="k"><b>80/20</b><span>creator / marketplace</span></div>
            <div className="k"><b>API-first</b><span>agents welcome</span></div>
          </div>
        </div>
      </section>

      <div className="wrap">
        {filtered ? (
          <section className="section">
            <h2>{filteredList.length} result{filteredList.length === 1 ? '' : 's'}{category ? ` in ${category}` : ''}{search ? ` for “${search}”` : ''}</h2>
            <div className="sub">Ranked by usage.</div>
            {filteredList.length ? (
              <div className="grid">{cards(filteredList).map((l) => <ListingCard key={l.slug} l={l} />)}</div>
            ) : (
              <div className="empty">No listings yet. <a href="/studio" className="mono">Create the first one →</a></div>
            )}
          </section>
        ) : (
          <>
            <section className="section">
              <h2>Trending</h2>
              <div className="sub">Most-used AI on the network.</div>
              {trending.length ? (
                <div className="grid">{cards(trending).map((l) => <ListingCard key={l.slug} l={l} />)}</div>
              ) : (
                <div className="empty">The marketplace is warming up. <a href="/studio" className="mono">Publish the first AI →</a></div>
              )}
            </section>
            {fresh.length > 0 && (
              <section className="section">
                <h2>Just launched</h2>
                <div className="sub">New capabilities from creators and agents.</div>
                <div className="grid">{cards(fresh).map((l) => <ListingCard key={l.slug} l={l} />)}</div>
              </section>
            )}
          </>
        )}
      </div>
    </>
  );
}
