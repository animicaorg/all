import { fetchStoreApps, countStoreApps } from '@/lib/storefront';
import { APP_CATEGORIES } from '@/lib/appStore';
import AppCard, { APP_CATEGORY_LABEL } from '@/components/AppCard';

export const dynamic = 'force-dynamic';

export default async function AppStoreHome({
  searchParams,
}: {
  searchParams: { search?: string; category?: string; type?: string };
}) {
  const search = searchParams.search?.trim() ?? '';
  const category = searchParams.category?.trim() ?? '';
  const type = searchParams.type?.trim() ?? '';
  const filtered = !!(search || category || type);

  const [top, fresh, results, total] = await Promise.all([
    filtered ? Promise.resolve([]) : fetchStoreApps({ sort: 'top', take: 12 }),
    filtered ? Promise.resolve([]) : fetchStoreApps({ sort: 'new', take: 8 }),
    filtered ? fetchStoreApps({ q: search, category, type, sort: 'top', take: 60 }) : Promise.resolve([]),
    countStoreApps(),
  ]);

  return (
    <>
      <section className="hero">
        <div className="wrap">
          <h1>The Animica <span className="grad">App Store</span></h1>
          <p className="sub">
            Apps, games, tools, and AI agents — published by developers, signed and verified, priced in
            ANM. Install and update through the Animica Wallet, which checks every binary against its
            on-chain license before it touches your device.
          </p>
          <form className="search" action="/marketplace/apps" method="get">
            <span style={{ opacity: 0.6 }}>🔎</span>
            <input name="search" defaultValue={search} placeholder="Search apps, games, tools…" />
            <button className="btn primary" type="submit">Search</button>
          </form>
          <div className="chips">
            <a className={`chip ${!category ? 'active' : ''}`} href="/marketplace/apps">All</a>
            {APP_CATEGORIES.map((c) => (
              <a key={c} className={`chip ${category === c ? 'active' : ''}`} href={`/marketplace/apps?category=${c}`}>
                {APP_CATEGORY_LABEL[c] ?? c}
              </a>
            ))}
          </div>
          <div className="kpi" style={{ marginTop: 18 }}>
            <div className="k"><b>{total}</b><span>published apps</span></div>
            <div className="k"><b>ANM</b><span>native pricing</span></div>
            <div className="k"><b>70/30</b><span>developer / store</span></div>
            <div className="k"><b>on-device</b><span>verified installs</span></div>
          </div>
        </div>
      </section>

      <div className="wrap">
        {filtered ? (
          <section className="section">
            <h2>
              {results.length} result{results.length === 1 ? '' : 's'}
              {category ? ` in ${APP_CATEGORY_LABEL[category] ?? category}` : ''}
              {search ? ` for “${search}”` : ''}
            </h2>
            <div className="sub">Ranked by rating.</div>
            {results.length ? (
              <div className="grid">{results.map((a) => <AppCard key={a.slug} a={a} />)}</div>
            ) : (
              <div className="empty">
                No apps here yet. <a href="/dev" className="mono">Publish the first one →</a>
              </div>
            )}
          </section>
        ) : (
          <>
            <section className="section">
              <h2>Top apps</h2>
              <div className="sub">Most-loved on the network.</div>
              {top.length ? (
                <div className="grid">{top.map((a) => <AppCard key={a.slug} a={a} />)}</div>
              ) : (
                <div className="empty">
                  The App Store is warming up. <a href="/dev" className="mono">Become a developer →</a>
                </div>
              )}
            </section>
            {fresh.length > 0 && (
              <section className="section">
                <h2>New releases</h2>
                <div className="sub">Just published by developers.</div>
                <div className="grid">{fresh.map((a) => <AppCard key={a.slug} a={a} />)}</div>
              </section>
            )}
          </>
        )}
      </div>
    </>
  );
}
