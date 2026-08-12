'use client';
import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  api, fmtAnm, fmtDate, StatusChip, APP_CATEGORIES, inputStyle, labelStyle, Msg, useDevSession,
} from './ui';

// My apps — the owner's store listings (drafts included) with listing + build-status chips,
// plus a create-app form. Data: GET /api/mkt/v1/store/apps/mine ; create: POST /api/mkt/v1/store/apps.

function priceLabel(prices?: any[]): string {
  if (!prices || !prices.length) return 'Free';
  const has = (m: string) => prices.find((p) => p.model === m);
  if (has('FREE') && prices.length === 1) return 'Free';
  const sub = has('SUBSCRIPTION');
  if (sub) return `${fmtAnm(sub.amountNanm)} ANM / ${sub.periodDays ?? 30}d`;
  const once = has('ONE_TIME');
  if (once) return `${fmtAnm(once.amountNanm)} ANM`;
  return 'Free';
}

// Best build-status signal for the card chip.
function buildChip(app: any): { status: string; note?: string } | null {
  const c = app.buildCounts ?? {};
  if (app.type === 'DIGITAL_GOOD') return null;
  if ((c.APPROVED ?? 0) > 0) return { status: 'APPROVED', note: `${c.APPROVED} approved` };
  if (app.latestBuild) return { status: app.latestBuild.status };
  return { status: 'no build', note: 'upload an APK' } as any;
}

function AppCard({ app }: { app: any }) {
  const chip = buildChip(app);
  return (
    <a className="card" href={`/dev/apps/${app.slug}`}>
      <div className="top">
        <div className="ico" style={{ overflow: 'hidden', padding: 0 }}>
          {app.iconUrl ? <img src={app.iconUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : (app.type === 'DIGITAL_GOOD' ? '📦' : '📱')}
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <h3 style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{app.name}</h3>
          <div className="by mono">{app.packageName || app.slug}</div>
        </div>
        <div className="price-tag">{priceLabel(app.prices)}</div>
      </div>
      <p style={{ minHeight: 20 }}>{app.tagline || 'No tagline yet.'}</p>
      <div className="meta" style={{ flexWrap: 'wrap', gap: 8 }}>
        <StatusChip status={app.status} />
        {app.type === 'DIGITAL_GOOD' && <span className="badge type">digital good</span>}
        {app.category && <span className="pill" style={{ fontSize: 11 }}>{String(app.category).replace(/_/g, ' ').toLowerCase()}</span>}
        {chip && (
          <span
            className="pill"
            style={{ fontSize: 11, color: chip.status === 'APPROVED' ? 'var(--good)' : chip.status === 'no build' ? 'var(--text-faint)' : 'var(--warn)' }}
            title={chip.note}
          >
            {chip.status === 'no build' ? 'no build' : `build: ${chip.status.replace(/_/g, ' ').toLowerCase()}`}
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: 'var(--text-faint)' }}>{fmtDate(app.createdAt)}</span>
      </div>
    </a>
  );
}

function CreateApp({ onCreated }: { onCreated: () => void }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [type, setType] = useState<'APP' | 'DIGITAL_GOOD'>('APP');
  const [category, setCategory] = useState('TOOLS');
  const [pkg, setPkg] = useState('');
  const [tagline, setTagline] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function create() {
    if (!name.trim()) { setMsg({ ok: false, text: 'name is required' }); return; }
    setBusy(true); setMsg(null);
    try {
      const body: any = { name: name.trim(), type, category };
      if (slug.trim()) body.slug = slug.trim();
      if (type === 'APP' && pkg.trim()) body.packageName = pkg.trim();
      if (tagline.trim()) body.tagline = tagline.trim();
      const d = await api('/api/mkt/v1/store/apps', {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      onCreated();
      router.push(`/dev/apps/${d.listing.slug}`);
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    } finally { setBusy(false); }
  }

  if (!open) {
    return <button className="btn primary" onClick={() => setOpen(true)}>+ New app</button>;
  }
  return (
    <div className="panel" style={{ marginBottom: 22 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <h3 style={{ margin: 0, fontSize: 17 }}>Create a listing</h3>
        <button className="btn ghost" style={{ fontSize: 13, padding: '6px 11px' }} onClick={() => setOpen(false)}>Cancel</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 14 }}>
        <div>
          <label style={labelStyle}>Name</label>
          <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} placeholder="My Great App" />
        </div>
        <div>
          <label style={labelStyle}>Slug <span className="muted">(optional)</span></label>
          <input style={inputStyle} value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="my-great-app" />
        </div>
        <div>
          <label style={labelStyle}>Type</label>
          <select style={inputStyle} value={type} onChange={(e) => setType(e.target.value as any)}>
            <option value="APP">App (Android APK)</option>
            <option value="DIGITAL_GOOD">Digital good (no APK)</option>
          </select>
        </div>
        <div>
          <label style={labelStyle}>Category</label>
          <select style={inputStyle} value={category} onChange={(e) => setCategory(e.target.value)}>
            {APP_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        {type === 'APP' && (
          <div>
            <label style={labelStyle}>Package name <span className="muted">(optional — else claimed at first build)</span></label>
            <input style={inputStyle} value={pkg} onChange={(e) => setPkg(e.target.value)} placeholder="com.example.app" spellCheck={false} />
          </div>
        )}
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>Tagline <span className="muted">(optional)</span></label>
          <input style={inputStyle} value={tagline} onChange={(e) => setTagline(e.target.value)} placeholder="One line about what it does" />
        </div>
      </div>
      <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn primary" onClick={create} disabled={busy}>{busy ? 'Creating…' : 'Create listing'}</button>
        <span className="muted" style={{ fontSize: 12.5 }}>Created as a draft — add builds, imagery and prices next.</span>
      </div>
      <Msg msg={msg} />
    </div>
  );
}

export default function MyApps() {
  const { reload: reloadSession } = useDevSession();
  const [apps, setApps] = useState<any[] | null>(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try {
      const d = await api('/api/mkt/v1/store/apps/mine');
      setApps(d.apps ?? []);
    } catch (e: any) { setErr(e.message); setApps([]); }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <div>
          <h1 style={{ fontSize: 28, letterSpacing: '-0.03em', margin: 0 }}>My apps</h1>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 14 }}>Your store listings, drafts included. Click one to edit metadata, imagery, builds and prices.</p>
        </div>
        <CreateApp onCreated={() => { load(); reloadSession(); }} />
      </div>

      {err && <div className="panel" style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)', fontSize: 14, marginBottom: 16 }}>{err}</div>}

      {apps == null ? (
        <div className="empty">Loading your apps…</div>
      ) : apps.length === 0 ? (
        <div className="empty">
          No listings yet. Create your first app to get a store page, upload a signed APK, and set a price in ANM.
        </div>
      ) : (
        <div className="grid">
          {apps.map((a) => <AppCard key={a.slug} app={a} />)}
        </div>
      )}
    </div>
  );
}
