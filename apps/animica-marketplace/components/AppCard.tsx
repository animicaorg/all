import { formatAnm } from '@/lib/nanm';

// Store-app card for the /marketplace/apps grid. Mirrors ListingCard's markup/classes so apps
// sit in the same visual system as AI listings, but links into the app-detail route and renders
// store-specific chrome (category, verified publisher, price).

export const APP_CATEGORY_LABEL: Record<string, string> = {
  GAMES: 'Games', AI_AGENTS: 'AI Agents', TOOLS: 'Tools', DEV_APPS: 'Dev Apps', MOBILE: 'Mobile', WEB: 'Web',
};
export const APP_CATEGORY_ICON: Record<string, string> = {
  GAMES: '🎮', AI_AGENTS: '🤖', TOOLS: '🧰', DEV_APPS: '🛠️', MOBILE: '📱', WEB: '🌐',
};
const TYPE_LABEL: Record<string, string> = { APP: 'App', DIGITAL_GOOD: 'Digital good' };

export interface AppCardData {
  slug: string;
  name: string;
  tagline?: string | null;
  type: string;
  category?: string | null; // AppCategory
  verified?: boolean;
  usersCount?: number;
  avgRating?: number;
  ratingCount?: number;
  iconUrl?: string | null;
  coverUrl?: string | null;
  publisher?: { address?: string; displayName?: string | null; anm?: string | null };
  prices?: { model: string; amountNanm: string | bigint; perCallNanm?: string | bigint; periodDays?: number | null }[];
  // Game Lab social layer (GAMES only; undefined elsewhere). Cosmetic, player-reported counts —
  // never tied to ANM. See lib/gameStats.ts.
  plays?: number;
  players?: number;
  topScore?: number | null;
}

// Compact count for badges: 1_234 -> "1.2k", 2_500_000 -> "2.5M".
export function compactCount(n: number): string {
  if (!Number.isFinite(n)) return '0';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1).replace(/\.0$/, '')}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(abs >= 10_000 ? 0 : 1).replace(/\.0$/, '')}k`;
  return String(Math.round(n));
}

export function appPriceLabel(prices?: AppCardData['prices']): { text: string; sub?: string } {
  if (!prices || !prices.length) return { text: 'Free' };
  const has = (m: string) => prices.find((p) => p.model === m);
  if (has('FREE') && prices.length === 1) return { text: 'Free' };
  const sub = has('SUBSCRIPTION');
  if (sub) return { text: `${formatAnm(BigInt(sub.amountNanm))}`, sub: `ANM/${sub.periodDays ?? 30}d` };
  const once = has('ONE_TIME');
  if (once) return { text: `${formatAnm(BigInt(once.amountNanm))}`, sub: 'ANM' };
  const use = has('USAGE');
  if (use && use.perCallNanm != null) return { text: `${formatAnm(BigInt(use.perCallNanm))}`, sub: 'ANM/call' };
  return { text: 'Free' };
}

export default function AppCard({ a }: { a: AppCardData }) {
  const avg = a.ratingCount ? (a.avgRating ?? 0).toFixed(1) : null;
  const p = appPriceLabel(a.prices);
  const cat = a.category ?? '';
  const publisher = a.publisher?.anm || a.publisher?.displayName
    || (a.publisher?.address ? a.publisher.address.slice(0, 10) + '…' : 'anon');
  return (
    <a className="card" href={`/marketplace/apps/${a.slug}`}>
      <div className="top">
        <div className="ico app-ico">
          {a.iconUrl ? <img src={a.iconUrl} alt="" /> : (APP_CATEGORY_ICON[cat] ?? '📦')}
        </div>
        <div style={{ minWidth: 0 }}>
          <h3>{a.name}</h3>
          <div className="by">by {publisher}{a.publisher?.anm ? ' · verified' : ''}</div>
        </div>
        <div className="price-tag" style={{ textAlign: 'right' }}>
          {p.text} {p.sub ? <small>{p.sub}</small> : null}
        </div>
      </div>
      <p>{a.tagline || 'An app on the Animica network.'}</p>
      <div className="meta">
        <span className="badge type">{APP_CATEGORY_LABEL[cat] ?? TYPE_LABEL[a.type] ?? 'App'}</span>
        {a.verified ? <span className="badge verified">✓ Verified</span> : null}
        {a.plays ? <span className="badge plays" title={`${a.plays.toLocaleString()} plays`}>▶ {compactCount(a.plays)}</span> : null}
        {a.topScore != null ? <span className="badge score" title={`Top score ${a.topScore.toLocaleString()}`}>★ {compactCount(a.topScore)}</span> : null}
        {cat === 'GAMES' ? null : a.type === 'DIGITAL_GOOD' ? <span>{TYPE_LABEL.DIGITAL_GOOD}</span> : null}
        <span style={{ marginLeft: 'auto' }}>
          {cat === 'GAMES'
            ? (a.players ? `${compactCount(a.players)} player${a.players === 1 ? '' : 's'}` : (avg ? `★ ${avg}` : 'new'))
            : `${avg ? `★ ${avg}` : 'new'} · ${(a.usersCount ?? 0).toLocaleString()} installs`}
        </span>
      </div>
    </a>
  );
}
