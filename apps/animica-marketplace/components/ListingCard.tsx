import { formatAnm } from '@/lib/nanm';

const TYPE_ICON: Record<string, string> = {
  RAG_ASSISTANT: '📚', AGENT: '🤖', WORKFLOW: '⚙️', KNOWLEDGE_AI: '🧠', MEDIA: '🎨',
};
const TYPE_LABEL: Record<string, string> = {
  RAG_ASSISTANT: 'RAG', AGENT: 'Agent', WORKFLOW: 'Workflow', KNOWLEDGE_AI: 'Knowledge', MEDIA: 'Media',
};

export interface CardListing {
  slug: string; name: string; tagline?: string | null; type: string; category: string;
  verified?: boolean; usersCount?: number; usageCount?: number; ratingSum?: number; ratingCount?: number;
  mediaKind?: string | null;
  owner?: { address: string; displayName?: string | null; isAgent?: boolean };
  prices?: { model: string; amountNanm: string | bigint; perCallNanm: string | bigint; periodDays?: number }[];
}

function priceLabel(prices?: CardListing['prices']): { text: string; sub?: string } {
  if (!prices || !prices.length) return { text: 'Free' };
  const has = (m: string) => prices.find((p) => p.model === m);
  if (has('FREE') && prices.length === 1) return { text: 'Free' };
  const sub = has('SUBSCRIPTION');
  if (sub) return { text: `${formatAnm(BigInt(sub.amountNanm))}`, sub: `ANM/${sub.periodDays ?? 30}d` };
  const once = has('ONE_TIME');
  if (once) return { text: `${formatAnm(BigInt(once.amountNanm))}`, sub: 'ANM' };
  const use = has('USAGE');
  if (use) return { text: `${formatAnm(BigInt(use.perCallNanm))}`, sub: 'ANM/call' };
  return { text: 'Free' };
}

export default function ListingCard({ l }: { l: CardListing }) {
  const avg = l.ratingCount ? (l.ratingSum! / l.ratingCount).toFixed(1) : null;
  const p = priceLabel(l.prices);
  return (
    <a className="card" href={`/marketplace/${l.slug}`}>
      <div className="top">
        <div className="ico">{TYPE_ICON[l.type] ?? '✨'}</div>
        <div style={{ minWidth: 0 }}>
          <h3>{l.name}</h3>
          <div className="by">
            by {l.owner?.displayName || (l.owner?.address ? l.owner.address.slice(0, 10) + '…' : 'anon')}
            {l.owner?.isAgent ? ' · agent' : ''}
          </div>
        </div>
        <div className="price-tag" style={{ textAlign: 'right' }}>
          {p.text} {p.sub ? <small>{p.sub}</small> : null}
        </div>
      </div>
      <p>{l.tagline || 'An AI capability on the Animica network.'}</p>
      <div className="meta">
        <span className="badge type">{TYPE_LABEL[l.type] ?? l.type}{l.mediaKind ? ` · ${l.mediaKind}` : ''}</span>
        {l.verified ? <span className="badge verified">✓ Verified</span> : null}
        <span>{l.category}</span>
        <span style={{ marginLeft: 'auto' }}>
          {avg ? `★ ${avg}` : 'new'} · {l.usageCount ?? 0} uses
        </span>
      </div>
    </a>
  );
}
