import { notFound } from 'next/navigation';
import { prisma } from '@/lib/db';
import { formatAnm, jsonSafe } from '@/lib/nanm';
import PreviewChat from '@/components/PreviewChat';
import MediaGenerate from '@/components/MediaGenerate';

export const dynamic = 'force-dynamic';

const TYPE_LABEL: Record<string, string> = {
  RAG_ASSISTANT: 'RAG Assistant', AGENT: 'AI Agent', WORKFLOW: 'Workflow', KNOWLEDGE_AI: 'Knowledge AI', MEDIA: 'Generative Media',
};

export default async function ListingDetail({ params }: { params: { slug: string } }) {
  const listing = await prisma.listing.findUnique({
    where: { slug: params.slug },
    include: {
      owner: { select: { address: true, displayName: true, isAgent: true } },
      prices: { where: { active: true } },
      sources: { select: { id: true, title: true, kind: true, status: true } },
      versions: { orderBy: { version: 'desc' }, take: 10, select: { version: true, notes: true, createdAt: true } },
      reviews: { orderBy: { createdAt: 'desc' }, take: 12, select: { rating: true, text: true, createdAt: true } },
    },
  });
  if (!listing || listing.status === 'DRAFT') notFound();
  const l = jsonSafe(listing) as any;
  const avg = l.ratingCount ? (l.ratingSum / l.ratingCount).toFixed(1) : null;

  return (
    <div className="wrap" style={{ paddingTop: 34 }}>
      <a href="/marketplace" className="muted" style={{ fontSize: 13 }}>← Marketplace</a>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 320px', gap: 28, marginTop: 16, alignItems: 'start' }} className="detail-grid">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <h1 style={{ fontSize: 34, margin: 0, letterSpacing: '-0.03em' }}>{l.name}</h1>
            {l.verified ? <span className="badge verified">✓ Animica Verified</span> : null}
            <span className="badge type">{TYPE_LABEL[l.type] ?? l.type}</span>
            {l.owner?.isAgent ? <span className="badge agent">agent-owned</span> : null}
          </div>
          <p className="muted" style={{ fontSize: 16, marginTop: 8 }}>{l.tagline}</p>
          <div className="meta" style={{ marginTop: 8, gap: 18, color: 'var(--text-faint)', fontSize: 13 }}>
            <span>by {l.owner?.displayName || l.owner?.address?.slice(0, 14) + '…'}</span>
            <span>{avg ? `★ ${avg} (${l.ratingCount})` : 'no ratings yet'}</span>
            <span>{l.usersCount} users</span>
            <span>{l.usageCount} uses</span>
            <span>v{l.version}</span>
          </div>

          <div className="panel" style={{ marginTop: 22 }}>
            <h2 style={{ marginTop: 0, fontSize: 18 }}>About</h2>
            <p className="muted" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{l.description || 'No description provided.'}</p>
          </div>

          {l.type !== 'MEDIA' ? (
            <div className="panel" style={{ marginTop: 18 }}>
              <h2 style={{ marginTop: 0, fontSize: 18 }}>Preview</h2>
              <div className="sub" style={{ color: 'var(--text-faint)', fontSize: 13, marginBottom: 12 }}>
                A few free turns. Full access requires purchase.
              </div>
              <PreviewChat slug={l.slug} />
            </div>
          ) : (
            <div className="panel" style={{ marginTop: 18 }}>
              <h2 style={{ marginTop: 0, fontSize: 18 }}>Generate {l.mediaKind === 'image' ? 'an image' : 'media'}</h2>
              <div className="sub" style={{ color: 'var(--text-faint)', fontSize: 13, marginBottom: 12 }}>
                A free low-res preview. Buy access for full-resolution, metered generation.
              </div>
              <MediaGenerate slug={l.slug} />
            </div>
          )}

          {l.sources?.length > 0 && (
            <div className="panel" style={{ marginTop: 18 }}>
              <h2 style={{ marginTop: 0, fontSize: 18 }}>Knowledge sources</h2>
              <ul className="muted" style={{ lineHeight: 1.7 }}>
                {l.sources.map((s: any) => <li key={s.id}>{s.title} <span className="pill">{s.kind}</span></li>)}
              </ul>
            </div>
          )}

          {l.reviews?.length > 0 && (
            <div className="panel" style={{ marginTop: 18 }}>
              <h2 style={{ marginTop: 0, fontSize: 18 }}>Reviews</h2>
              {l.reviews.map((r: any, i: number) => (
                <div key={i} style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
                  <div>{'★'.repeat(r.rating)}<span className="muted">{'★'.repeat(5 - r.rating)}</span></div>
                  <div className="muted" style={{ fontSize: 14 }}>{r.text}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        <aside style={{ position: 'sticky', top: 82 }}>
          <div className="panel">
            <h2 style={{ marginTop: 0, fontSize: 16 }}>Pricing</h2>
            {l.prices?.length ? l.prices.map((p: any) => (
              <div key={p.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '9px 0', borderBottom: '1px solid var(--border)' }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{({ FREE: 'Free', ONE_TIME: 'One-time', SUBSCRIPTION: 'Subscription', USAGE: 'Pay-per-use' } as any)[p.model]}</div>
                  {p.label ? <div className="muted" style={{ fontSize: 12 }}>{p.label}</div> : null}
                </div>
                <div style={{ fontWeight: 700 }}>
                  {p.model === 'FREE' ? '0 ANM'
                    : p.model === 'USAGE' ? `${formatAnm(BigInt(p.perCallNanm))} ANM/call`
                    : p.model === 'SUBSCRIPTION' ? `${formatAnm(BigInt(p.amountNanm))} ANM/${p.periodDays}d`
                    : `${formatAnm(BigInt(p.amountNanm))} ANM`}
                </div>
              </div>
            )) : <div className="muted">Free</div>}
            <a className="btn primary" style={{ width: '100%', justifyContent: 'center', marginTop: 16 }} href={`/my-ai?buy=${l.slug}`}>Get access</a>
            <div className="muted" style={{ fontSize: 12, marginTop: 10, textAlign: 'center' }}>Pays in ANM · 80% to creator</div>
          </div>

          <div className="panel" style={{ marginTop: 14 }}>
            <h2 style={{ marginTop: 0, fontSize: 15 }}>Use via API</h2>
            <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>Agents call this directly:</div>
            <pre className="mono" style={{ fontSize: 11.5, overflowX: 'auto', background: 'var(--bg-elev)', padding: 12, borderRadius: 10, border: '1px solid var(--border)' }}>
{`POST /api/mkt/v1/ai/${l.slug}/ask
Authorization: Bearer anm_mkt_...
{ "messages": [
  {"role":"user","content":"..."}
]}`}
            </pre>
          </div>
        </aside>
      </div>
    </div>
  );
}
