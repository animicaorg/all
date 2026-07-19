import { notFound } from 'next/navigation';
import { prisma } from '@/lib/db';
import { formatAnm, jsonSafe } from '@/lib/nanm';
import { STORE_TYPES, assetUrl } from '@/lib/storeCatalog';
import { APP_CATEGORY_LABEL, APP_CATEGORY_ICON, appPriceLabel } from '@/components/AppCard';
import InstallCta from '@/components/InstallCta';
import GamePlay from '@/components/GamePlay';

export const dynamic = 'force-dynamic';

const PRICE_MODEL_LABEL: Record<string, string> = {
  FREE: 'Free', ONE_TIME: 'One-time', SUBSCRIPTION: 'Subscription', USAGE: 'Pay-per-use',
};

// Permissions the store flags for extra scrutiny (mirrors the sensitive-permission policy gate).
const SENSITIVE_RE = /SMS|ACCESSIBILITY|BIND_DEVICE_ADMIN|READ_CONTACTS|RECORD_AUDIO|CAMERA|FINE_LOCATION|READ_CALL_LOG|SYSTEM_ALERT_WINDOW|REQUEST_INSTALL_PACKAGES|MANAGE_EXTERNAL_STORAGE/i;

function fmtBytes(n: number): string {
  if (!n) return '—';
  const mb = n / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(1)} MB`;
}

function permList(json: unknown): string[] {
  if (Array.isArray(json)) return json.map((p) => String(p));
  if (json && typeof json === 'object') return Object.keys(json as Record<string, unknown>);
  return [];
}

function shortPerm(p: string): string {
  return p.replace(/^android\.permission\./, '').replace(/^com\.[a-z.]+\./i, '');
}

export default async function AppDetail({ params }: { params: { slug: string } }) {
  const listing = await prisma.listing.findUnique({
    where: { slug: params.slug },
    include: {
      owner: { select: { address: true, displayName: true, isAgent: true } },
      anmDomain: { select: { name: true } },
      prices: { where: { active: true } },
      storeAssets: { orderBy: [{ kind: 'asc' }, { sortOrder: 'asc' }] },
      builds: {
        where: { status: 'APPROVED', channel: 'stable' },
        orderBy: { versionCode: 'desc' },
        take: 1,
      },
      reviews: { orderBy: { createdAt: 'desc' }, take: 20, select: { rating: true, text: true, createdAt: true } },
    },
  });
  if (!listing || listing.status === 'DRAFT' || !(STORE_TYPES as readonly string[]).includes(listing.type)) notFound();

  const l = jsonSafe(listing) as any;
  const cat = l.appCategory ?? '';
  const avg = l.ratingCount ? (l.ratingSum / l.ratingCount).toFixed(1) : null;
  const icon = l.storeAssets.find((a: any) => a.kind === 'ICON');
  const screenshots = l.storeAssets.filter((a: any) => a.kind === 'SCREENSHOT');
  const build = l.builds[0] ?? null;
  const perms = build ? permList(build.permissionsJson) : [];
  const publisherName = l.anmDomain ? `${l.anmDomain.name}.anm`
    : l.owner?.displayName || (l.owner?.address ? l.owner.address.slice(0, 14) + '…' : 'anon');
  const p = appPriceLabel(l.prices);
  const priceText = p.sub ? `${p.text} ${p.sub}` : p.text;

  // Game Lab web game: a DIGITAL_GOOD in GAMES with a self-contained HTML play bundle. FREE
  // games load straight from the public content route; PAID ones play through the license-gated
  // play-token. Mirrors lib/entitlement.ts isFreeToPlay (paid = any ONE_TIME/SUBSCRIPTION price).
  const isGameBundle = l.type === 'DIGITAL_GOOD' && cat === 'GAMES' && !!l.bundleCid;
  const isPaidGame = Array.isArray(l.prices) && l.prices.some((pr: any) => pr.model === 'ONE_TIME' || pr.model === 'SUBSCRIPTION');

  return (
    <div className="wrap" style={{ paddingTop: 34 }}>
      <a href="/marketplace/apps" className="muted" style={{ fontSize: 13 }}>← App Store</a>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 320px', gap: 28, marginTop: 16, alignItems: 'start' }} className="detail-grid">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <div className="ico app-ico" style={{ width: 56, height: 56, fontSize: 28, borderRadius: 14 }}>
              {icon ? <img src={assetUrl(icon.cid)} alt="" /> : (APP_CATEGORY_ICON[cat] ?? '📦')}
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <h1 style={{ fontSize: 30, margin: 0, letterSpacing: '-0.03em' }}>{l.name}</h1>
                {l.verified ? <span className="badge verified">✓ Animica Verified</span> : null}
                {cat ? <span className="badge type">{APP_CATEGORY_LABEL[cat] ?? cat}</span> : null}
                {l.owner?.isAgent ? <span className="badge agent">agent-owned</span> : null}
              </div>
              <p className="muted" style={{ fontSize: 15, marginTop: 6 }}>{l.tagline}</p>
            </div>
          </div>

          <div className="meta" style={{ marginTop: 12, gap: 18, color: 'var(--text-faint)', fontSize: 13, display: 'flex', flexWrap: 'wrap' }}>
            <span>by {publisherName}</span>
            <span>{avg ? `★ ${avg} (${l.ratingCount})` : 'no ratings yet'}</span>
            <span>{(l.usersCount ?? 0).toLocaleString()} installs</span>
            {build ? <span>v{build.versionName} ({build.versionCode})</span> : null}
            {build ? <span>{fmtBytes(Number(build.sizeBytes))}</span> : null}
          </div>

          {isGameBundle && (
            <div className="panel" style={{ marginTop: 20 }}>
              <h2 style={{ marginTop: 0, fontSize: 18 }}>Play</h2>
              <GamePlay slug={l.slug} bundleCid={l.bundleCid} isFree={!isPaidGame} name={l.name} />
            </div>
          )}

          {screenshots.length > 0 && (
            <div className="shots" style={{ marginTop: 20 }}>
              {screenshots.map((s: any) => (
                <img key={s.cid} className="shot" src={assetUrl(s.cid)} alt="screenshot"
                  style={s.width && s.height ? { aspectRatio: `${s.width} / ${s.height}` } : undefined} />
              ))}
            </div>
          )}

          <div className="panel" style={{ marginTop: 20 }}>
            <h2 style={{ marginTop: 0, fontSize: 18 }}>About</h2>
            <p className="muted" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{l.description || 'No description provided.'}</p>
          </div>

          {build && (
            <div className="panel" style={{ marginTop: 18 }}>
              <h2 style={{ marginTop: 0, fontSize: 18 }}>Latest release</h2>
              <div className="specs">
                <div><span>Version</span><b>{build.versionName} · code {build.versionCode}</b></div>
                <div><span>Channel</span><b>{build.channel}</b></div>
                <div><span>Size</span><b>{fmtBytes(Number(build.sizeBytes))}</b></div>
                <div><span>Android</span><b>API {build.minSdk}+ · targets {build.targetSdk}</b></div>
              </div>
              {build.releaseNotes ? (
                <>
                  <div className="muted" style={{ fontSize: 13, marginTop: 14, marginBottom: 4 }}>Release notes</div>
                  <p className="muted" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55, margin: 0 }}>{build.releaseNotes}</p>
                </>
              ) : null}
              <div className="muted mono" style={{ fontSize: 11, marginTop: 14, wordBreak: 'break-all' }}>
                <div>sha3-256 {build.sha3}</div>
                <div style={{ marginTop: 4 }}>signer {build.certSha256}</div>
              </div>
              <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                The wallet re-verifies both of these on your device before installing.
              </div>
            </div>
          )}

          {perms.length > 0 && (
            <div className="panel" style={{ marginTop: 18 }}>
              <h2 style={{ marginTop: 0, fontSize: 18 }}>Permissions</h2>
              <div className="sub" style={{ color: 'var(--text-faint)', fontSize: 13, marginBottom: 12 }}>
                {perms.length} requested{perms.some((x) => SENSITIVE_RE.test(x)) ? ' · sensitive ones highlighted' : ''}.
              </div>
              <div className="perms">
                {perms.map((perm) => (
                  <span key={perm} className={`pill ${SENSITIVE_RE.test(perm) ? 'sensitive' : ''}`} title={perm}>
                    {shortPerm(perm)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {l.reviews?.length > 0 && (
            <div className="panel" style={{ marginTop: 18 }}>
              <h2 style={{ marginTop: 0, fontSize: 18 }}>Reviews</h2>
              {l.reviews.map((r: any, i: number) => (
                <div key={i} style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
                  <div>{'★'.repeat(r.rating)}<span className="muted">{'★'.repeat(5 - r.rating)}</span></div>
                  {r.text ? <div className="muted" style={{ fontSize: 14 }}>{r.text}</div> : null}
                </div>
              ))}
            </div>
          )}
        </div>

        <aside style={{ position: 'sticky', top: 82 }}>
          <div className="panel">
            <h2 style={{ marginTop: 0, fontSize: 16 }}>Install</h2>
            {l.prices?.length ? l.prices.map((pr: any) => (
              <div key={pr.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '9px 0', borderBottom: '1px solid var(--border)' }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{PRICE_MODEL_LABEL[pr.model] ?? pr.model}</div>
                  {pr.label ? <div className="muted" style={{ fontSize: 12 }}>{pr.label}</div> : null}
                </div>
                <div style={{ fontWeight: 700 }}>
                  {pr.model === 'FREE' ? '0 ANM'
                    : pr.model === 'USAGE' ? `${formatAnm(BigInt(pr.perCallNanm))} ANM/call`
                    : pr.model === 'SUBSCRIPTION' ? `${formatAnm(BigInt(pr.amountNanm))} ANM/${pr.periodDays ?? 30}d`
                    : `${formatAnm(BigInt(pr.amountNanm))} ANM`}
                </div>
              </div>
            )) : <div className="muted" style={{ marginBottom: 8 }}>Free</div>}
            <div style={{ marginTop: 16 }}>
              <InstallCta
                slug={l.slug}
                packageName={l.packageName}
                priceText={priceText}
                // Game Lab web games get a standalone hosted play page (a real URL a browser/PWA can
                // open + Add-to-Home-Screen). Only pass it for GAMES bundles so other listings keep
                // the wallet-only CTA. Derived from the same Listing fields already loaded above.
                playHref={isGameBundle ? `/play/${l.slug}` : undefined}
              />
            </div>
          </div>

          <div className="panel" style={{ marginTop: 14 }}>
            <h2 style={{ marginTop: 0, fontSize: 15 }}>Publisher</h2>
            <div style={{ fontWeight: 600 }}>{publisherName}</div>
            {l.anmDomain ? (
              <div className="badge verified" style={{ display: 'inline-block', marginTop: 8 }}>✓ Verified publisher</div>
            ) : (
              <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>Unverified publisher</div>
            )}
            <div className="muted mono" style={{ fontSize: 11, marginTop: 10, wordBreak: 'break-all' }}>{l.owner?.address}</div>
          </div>
        </aside>
      </div>

      <style>{`
        .shots{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}
        .shots .shot{height:340px;width:auto;border-radius:14px;border:1px solid var(--border);background:var(--bg-elev);scroll-snap-align:start;flex:0 0 auto}
        .specs{display:grid;grid-template-columns:1fr 1fr;gap:12px 22px}
        .specs>div{display:flex;flex-direction:column;gap:2px}
        .specs span{font-size:12px;color:var(--text-faint)}
        .specs b{font-size:14px;font-weight:600}
        .perms{display:flex;flex-wrap:wrap;gap:8px}
        .perms .pill{font-size:12px}
        .perms .pill.sensitive{border-color:var(--warn);color:var(--warn)}
        @media (max-width:820px){.detail-grid{grid-template-columns:1fr!important}.shots .shot{height:260px}}
      `}</style>
    </div>
  );
}
