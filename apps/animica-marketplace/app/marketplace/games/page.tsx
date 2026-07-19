import { fetchGames, countGames, type GameSort } from '@/lib/storefront';
import AppCard from '@/components/AppCard';

export const dynamic = 'force-dynamic';

// Dedicated Game Lab discovery surface. Lists published, playable web games (DIGITAL_GOOD / GAMES
// with a self-contained play bundle) ranked by trending / top / new, with play-count + top-score
// badges on the cards. Additive to the App Store; reuses the storefront reader + the shared AppCard.
//
// HONESTY: play counts, unique players and top scores are CLIENT-REPORTED and UNTRUSTED. They power
// cosmetic, "for fun" leaderboards ONLY and are never tied to ANM or any on-chain reward.

const SORTS: { value: GameSort; label: string; sub: string }[] = [
  { value: 'trending', label: 'Trending', sub: 'Most played this week' },
  { value: 'top', label: 'Most played', sub: 'By all-time play count' },
  { value: 'new', label: 'New', sub: 'Freshly published' },
];

export default async function GamesHome({
  searchParams,
}: {
  searchParams: { sort?: string; search?: string };
}) {
  const search = searchParams.search?.trim() ?? '';
  const sortParam = (searchParams.sort ?? '').toLowerCase();
  const sort: GameSort = (SORTS.find((s) => s.value === sortParam)?.value ?? 'trending') as GameSort;

  const [games, total] = await Promise.all([
    fetchGames({ sort, take: 60, q: search || undefined }),
    countGames(),
  ]);

  const active = SORTS.find((s) => s.value === sort)!;
  const qs = (s: GameSort) => `/marketplace/games?sort=${s}${search ? `&search=${encodeURIComponent(search)}` : ''}`;

  return (
    <>
      <section className="hero">
        <div className="wrap">
          <h1>Animica <span className="grad">Games</span></h1>
          <p className="sub">
            Play web games from the Animica Game Lab — right in your browser, no install. Every game
            runs in a locked sandbox. Beat the high scores on the for-fun leaderboards.
          </p>
          <form className="search" action="/marketplace/games" method="get">
            <span style={{ opacity: 0.6 }}>🔎</span>
            <input name="search" defaultValue={search} placeholder="Search games…" />
            {sort !== 'trending' && <input type="hidden" name="sort" value={sort} />}
            <button className="btn primary" type="submit">Search</button>
          </form>
          <div className="chips">
            {SORTS.map((s) => (
              <a key={s.value} className={`chip ${sort === s.value ? 'active' : ''}`} href={qs(s.value)}>
                {s.label}
              </a>
            ))}
          </div>
          <div className="kpi" style={{ marginTop: 18 }}>
            <div className="k"><b>{total}</b><span>playable games</span></div>
            <div className="k"><b>▶ instant</b><span>no install, sandboxed</span></div>
            <div className="k"><b>for fun</b><span>leaderboards, no ANM</span></div>
          </div>
        </div>
      </section>

      <div className="wrap">
        <section className="section">
          <h2>{search ? `Results for “${search}”` : active.label}</h2>
          <div className="sub">
            {search ? `${games.length} game${games.length === 1 ? '' : 's'}` : active.sub}
            {' · '}Play-count + top-score badges are player-reported and just for fun.
          </div>
          {games.length ? (
            <div className="grid">{games.map((a) => <AppCard key={a.slug} a={a} />)}</div>
          ) : (
            <div className="empty">
              {search
                ? <>No games match “{search}”. <a href="/marketplace/games" className="mono">See all games →</a></>
                : <>No games published yet. Make one in the <a href="https://animica.io" className="mono">Game Lab</a> and publish it here.</>}
            </div>
          )}
        </section>

        <p className="muted" style={{ fontSize: 12.5, maxWidth: 720, paddingBottom: 24 }}>
          Leaderboards, play counts and top scores are reported by the game running in your browser —
          they are cosmetic and cannot be verified, so they are never tied to ANM payouts or any
          on-chain reward. Submissions are rate-limited; there is no anti-cheat beyond that.
        </p>
      </div>
    </>
  );
}
