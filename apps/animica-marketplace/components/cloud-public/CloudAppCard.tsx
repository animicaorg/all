import { categoryLabel, compact, priceLabel, shortAddr } from './fmt';

// Marketplace card for a PUBLISHED, PUBLIC CloudApp. Server component — receives plain
// (jsonSafe'd) data. Counters shown here are the schema-blessed denormalized caches
// (CloudApp.execCount / ratingSum / ratingCount), each recomputable from CloudExecution /
// CloudReview rows; the app detail page recomputes the authoritative aggregates.

export interface CloudAppCardData {
  slug: string;
  name: string;
  tagline: string;
  category: string;
  iconEmoji: string;
  iconUrl: string | null;
  pricingModel: string;
  priceNanm: string;
  execCount: number;
  installCount: number;
  ratingSum: number;
  ratingCount: number;
  owner: { handle: string | null; displayName: string | null; address: string };
}

export default function CloudAppCard({ a }: { a: CloudAppCardData }) {
  const p = priceLabel(a.pricingModel, a.priceNanm);
  const by = a.owner.displayName || a.owner.handle || shortAddr(a.owner.address);
  const avg = a.ratingCount > 0 ? (a.ratingSum / a.ratingCount).toFixed(1) : null;
  return (
    <a className="card" href={`/apps/${encodeURIComponent(a.slug)}`}>
      <div className="top">
        <div className="ico app-ico" aria-hidden="true">
          {a.iconUrl ? <img src={a.iconUrl} alt="" /> : a.iconEmoji || '🐍'}
        </div>
        <div style={{ minWidth: 0 }}>
          <h3>{a.name}</h3>
          <div className="by">by {by}</div>
        </div>
        <div className="price-tag" style={{ textAlign: 'right' }}>
          {p.text} {p.sub ? <small>{p.sub}</small> : null}
        </div>
      </div>
      <p>{a.tagline || a.name}</p>
      <div className="meta">
        <span className="badge type">{categoryLabel(a.category)}</span>
        {avg ? (
          <span title={`${avg} average from ${a.ratingCount} review${a.ratingCount === 1 ? '' : 's'}`}>★ {avg}</span>
        ) : null}
        <span style={{ marginLeft: 'auto' }}>
          {a.execCount > 0
            ? `${compact(a.execCount)} run${a.execCount === 1 ? '' : 's'}`
            : 'new'}
          {a.installCount > 0 ? ` · ${compact(a.installCount)} user${a.installCount === 1 ? '' : 's'}` : ''}
        </span>
      </div>
    </a>
  );
}
