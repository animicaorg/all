'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import {
  api, uploadRaw, fmtDate, nanmToAnmInput, anmToNanm, StatusChip, Msg,
  APP_CATEGORIES, inputStyle, labelStyle, rowLabel,
} from '../../ui';

// App editor — metadata, imagery, prices and publish state for one store listing. Owner data via
// GET /store/apps/mine?slug= (drafts included, unlike the public detail route). Mutations:
//   PATCH /store/apps/[slug]                     metadata / visibility / status / .anm link
//   PUT   /store/apps/[slug]/prices              price set (ANM)
//   POST|DELETE|GET /store/assets                imagery (icon/banner/screenshots)

// ── metadata ─────────────────────────────────────────────────────────────────
function MetadataForm({ app, slug, onSaved }: { app: any; slug: string; onSaved: () => void }) {
  const [name, setName] = useState(app.name ?? '');
  const [tagline, setTagline] = useState(app.tagline ?? '');
  const [description, setDescription] = useState(app.description ?? '');
  const [category, setCategory] = useState(app.category ?? 'TOOLS');
  const [visibility, setVisibility] = useState(app.visibility ?? 'PUBLIC');
  const [coverUrl, setCoverUrl] = useState(app.coverUrl ?? '');
  const [anmDomain, setAnmDomain] = useState<string>(app.anmDomain ? String(app.anmDomain).replace(/\.anm$/, '') : '');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function save() {
    setBusy(true); setMsg(null);
    try {
      const body: any = { name, tagline, description, category, visibility, coverUrl };
      body.anmDomain = anmDomain.trim() ? anmDomain.trim() : null;
      await api(`/api/mkt/v1/store/apps/${encodeURIComponent(slug)}`, {
        method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      setMsg({ ok: true, text: 'Saved.' });
      onSaved();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  }

  return (
    <section className="panel">
      <h3 style={{ margin: '0 0 14px', fontSize: 17 }}>Listing details</h3>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 14 }}>
        <div>
          <label style={labelStyle}>Name</label>
          <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Category</label>
          <select style={inputStyle} value={category} onChange={(e) => setCategory(e.target.value)}>
            {APP_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <div>
          <label style={labelStyle}>Visibility</label>
          <select style={inputStyle} value={visibility} onChange={(e) => setVisibility(e.target.value)}>
            <option value="PUBLIC">Public — listed in the store</option>
            <option value="UNLISTED">Unlisted — direct link only</option>
            <option value="PRIVATE">Private — hidden</option>
          </select>
        </div>
        <div>
          <label style={labelStyle}>Verified publisher .anm <span className="muted">(a domain you own)</span></label>
          <div className="search" style={{ margin: 0 }}>
            <input value={anmDomain} onChange={(e) => setAnmDomain(e.target.value)} placeholder="yourname" spellCheck={false} />
            <span className="mono muted" style={{ fontSize: 13 }}>.anm</span>
          </div>
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>Tagline</label>
          <input style={inputStyle} value={tagline} onChange={(e) => setTagline(e.target.value)} placeholder="One line about what it does" />
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            style={{ ...inputStyle, minHeight: 130, resize: 'vertical', lineHeight: 1.5 }}
            placeholder="What the app does, features, what's new…"
          />
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>Cover image URL <span className="muted">(optional)</span></label>
          <input style={inputStyle} value={coverUrl} onChange={(e) => setCoverUrl(e.target.value)} placeholder="https://…" spellCheck={false} />
        </div>
      </div>
      <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save details'}</button>
        <Msg msg={msg} />
      </div>
    </section>
  );
}

// ── publish state ─────────────────────────────────────────────────────────────
function PublishControls({ app, slug, onChanged }: { app: any; slug: string; onChanged: () => void }) {
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const approved = (app.buildCounts?.APPROVED ?? 0) > 0;
  const needsBuild = app.type === 'APP' && !approved;

  async function setStatus(status: string) {
    setBusy(status); setMsg(null);
    try {
      await api(`/api/mkt/v1/store/apps/${encodeURIComponent(slug)}`, {
        method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ status }),
      });
      setMsg({ ok: true, text: status === 'PUBLISHED' ? 'Published — live in the store.' : `Status set to ${status.toLowerCase()}.` });
      onChanged();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(''); }
  }

  return (
    <section className="panel">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: 17 }}>Publish state</h3>
        <StatusChip status={app.status} />
      </div>
      <p className="muted" style={{ fontSize: 13, margin: '8px 0 12px' }}>
        {app.type === 'APP'
          ? 'An APP goes live only once it has an APPROVED build. Publishing a listing without one is blocked.'
          : 'Digital goods can be published without an APK build.'}
        {needsBuild && <> You have no approved build yet — <a className="inline" href={`/dev/apps/${slug}/builds`}>upload one</a>.</>}
      </p>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {app.status !== 'PUBLISHED' && (
          <button className="btn primary" onClick={() => setStatus('PUBLISHED')} disabled={!!busy || needsBuild}>
            {busy === 'PUBLISHED' ? 'Publishing…' : 'Publish'}
          </button>
        )}
        {app.status === 'PUBLISHED' && (
          <button className="btn" onClick={() => setStatus('DELISTED')} disabled={!!busy} style={{ color: 'var(--warn)' }}>
            {busy === 'DELISTED' ? 'Delisting…' : 'Delist'}
          </button>
        )}
        {app.status !== 'DRAFT' && (
          <button className="btn ghost" onClick={() => setStatus('DRAFT')} disabled={!!busy}>
            {busy === 'DRAFT' ? '…' : 'Back to draft'}
          </button>
        )}
        {app.status === 'PUBLISHED' && (
          <a className="btn ghost" href={`/marketplace/${slug}`} target="_blank" rel="noopener">View store page →</a>
        )}
      </div>
      <Msg msg={msg} />
    </section>
  );
}

// ── prices ────────────────────────────────────────────────────────────────────
interface PriceRow { model: string; amountAnm: string; periodDays: number; label: string }

function toRows(prices: any[]): PriceRow[] {
  const active = (prices ?? []).filter((p) => p.active !== false);
  if (!active.length) return [{ model: 'FREE', amountAnm: '0', periodDays: 30, label: '' }];
  return active.map((p) => ({
    model: p.model,
    amountAnm: nanmToAnmInput(p.amountNanm ?? '0'),
    periodDays: p.periodDays ?? 30,
    label: p.label ?? '',
  }));
}

function PricesEditor({ app, slug, onSaved }: { app: any; slug: string; onSaved: () => void }) {
  const [rows, setRows] = useState<PriceRow[]>(() => toRows(app.prices));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  function update(i: number, patch: Partial<PriceRow>) {
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function remove(i: number) { setRows((rs) => rs.filter((_, idx) => idx !== i)); }
  function add() { setRows((rs) => [...rs, { model: 'ONE_TIME', amountAnm: '', periodDays: 30, label: '' }]); }

  async function save() {
    setBusy(true); setMsg(null);
    try {
      const prices = rows.map((r) => {
        const row: any = { model: r.model, label: r.label || undefined };
        if (r.model === 'FREE') row.amountNanm = '0';
        else row.amountNanm = anmToNanm(r.amountAnm || '0'); // throws on malformed input
        if (r.model === 'SUBSCRIPTION') row.periodDays = r.periodDays;
        return row;
      });
      const d = await api(`/api/mkt/v1/store/apps/${encodeURIComponent(slug)}/prices`, {
        method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ prices }),
      });
      setRows(toRows(d.prices));
      setMsg({ ok: true, text: 'Prices updated.' });
      onSaved();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  }

  return (
    <section className="panel">
      <h3 style={{ margin: '0 0 4px', fontSize: 17 }}>Pricing</h3>
      <p className="muted" style={{ fontSize: 13, margin: '0 0 14px' }}>
        Amounts are in ANM. The store fee is 30% (70% to you), settled in your marketplace balance and withdrawable on the <a className="inline" href="/dev/earnings">earnings</a> page.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {rows.map((r, i) => (
          <div key={i} style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', borderTop: i ? '1px solid var(--border)' : 'none', paddingTop: i ? 10 : 0 }}>
            <div style={{ flex: '0 0 170px' }}>
              <label style={labelStyle}>Model</label>
              <select style={inputStyle} value={r.model} onChange={(e) => update(i, { model: e.target.value })}>
                <option value="FREE">Free</option>
                <option value="ONE_TIME">One-time</option>
                <option value="SUBSCRIPTION">Subscription</option>
              </select>
            </div>
            {r.model !== 'FREE' && (
              <div style={{ flex: '0 0 150px' }}>
                <label style={labelStyle}>Price (ANM)</label>
                <input style={inputStyle} value={r.amountAnm} onChange={(e) => update(i, { amountAnm: e.target.value })} placeholder="9.99" inputMode="decimal" />
              </div>
            )}
            {r.model === 'SUBSCRIPTION' && (
              <div style={{ flex: '0 0 120px' }}>
                <label style={labelStyle}>Period (days)</label>
                <input style={inputStyle} type="number" min={1} value={r.periodDays} onChange={(e) => update(i, { periodDays: Number(e.target.value) })} />
              </div>
            )}
            <div style={{ flex: 1, minWidth: 140 }}>
              <label style={labelStyle}>Label <span className="muted">(optional)</span></label>
              <input style={inputStyle} value={r.label} onChange={(e) => update(i, { label: e.target.value })} placeholder="e.g. Pro" />
            </div>
            <button className="btn ghost" style={{ color: 'var(--bad)', padding: '9px 12px' }} onClick={() => remove(i)} title="Remove">✕</button>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn" onClick={add} disabled={rows.length >= 6}>+ Add tier</button>
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save prices'}</button>
        <Msg msg={msg} />
      </div>
    </section>
  );
}

// ── imagery ───────────────────────────────────────────────────────────────────
function AssetUploader({ slug, kind, label, onDone }: { slug: string; kind: 'icon' | 'banner' | 'screenshot'; label: string; onDone: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function pick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (ref.current) ref.current.value = '';
    if (!file) return;
    setBusy(true); setErr('');
    try {
      await uploadRaw('/api/mkt/v1/store/assets', file, { 'x-anm-listing': slug, 'x-anm-kind': kind });
      onDone();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 4 }}>
      <button className="btn" onClick={() => ref.current?.click()} disabled={busy} style={{ fontSize: 13 }}>
        {busy ? 'Uploading…' : label}
      </button>
      <input ref={ref} type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={pick} style={{ display: 'none' }} />
      {err && <span style={{ color: 'var(--bad)', fontSize: 12 }}>{err}</span>}
    </div>
  );
}

function AssetsManager({ app, slug, onChanged }: { app: any; slug: string; onChanged: () => void }) {
  const assets: any[] = app.assets ?? [];
  const icon = assets.find((a) => a.kind === 'ICON');
  const banner = assets.find((a) => a.kind === 'BANNER');
  const shots = assets.filter((a) => a.kind === 'SCREENSHOT').sort((a, b) => a.sortOrder - b.sortOrder);

  async function del(id: string) {
    try { await api(`/api/mkt/v1/store/assets?id=${encodeURIComponent(id)}`, { method: 'DELETE' }); onChanged(); }
    catch { /* surfaced by reload */ }
  }

  const thumb: React.CSSProperties = { width: 64, height: 64, borderRadius: 12, objectFit: 'cover', border: '1px solid var(--border)' };

  return (
    <section className="panel">
      <h3 style={{ margin: '0 0 4px', fontSize: 17 }}>Imagery</h3>
      <p className="muted" style={{ fontSize: 13, margin: '0 0 14px' }}>PNG / JPEG / WebP / GIF, up to 2 MB each. Icon and banner replace the previous one.</p>

      <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div>
          <div style={rowLabel}>Icon</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
            {icon ? <img src={icon.url} alt="icon" style={thumb} /> : <div style={{ ...thumb, display: 'grid', placeItems: 'center', color: 'var(--text-faint)' }}>—</div>}
            <AssetUploader slug={slug} kind="icon" label={icon ? 'Replace' : 'Upload icon'} onDone={onChanged} />
          </div>
        </div>
        <div>
          <div style={rowLabel}>Banner</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
            {banner ? <img src={banner.url} alt="banner" style={{ ...thumb, width: 120, height: 64 }} /> : <div style={{ ...thumb, width: 120, display: 'grid', placeItems: 'center', color: 'var(--text-faint)' }}>—</div>}
            <AssetUploader slug={slug} kind="banner" label={banner ? 'Replace' : 'Upload banner'} onDone={onChanged} />
          </div>
        </div>
      </div>

      <div style={{ marginTop: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <span style={rowLabel}>Screenshots</span>
          <AssetUploader slug={slug} kind="screenshot" label="+ Add screenshot" onDone={onChanged} />
        </div>
        {shots.length === 0 ? (
          <div className="muted" style={{ fontSize: 13 }}>No screenshots yet.</div>
        ) : (
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {shots.map((s) => (
              <div key={s.id} style={{ position: 'relative' }}>
                <img src={s.url} alt="screenshot" style={{ width: 110, height: 190, objectFit: 'cover', borderRadius: 10, border: '1px solid var(--border)' }} />
                <button
                  onClick={() => del(s.id)}
                  title="Remove"
                  style={{ position: 'absolute', top: 5, right: 5, background: 'rgba(7,8,12,0.8)', border: '1px solid var(--border-bright)', color: 'var(--bad)', borderRadius: 7, width: 24, height: 24, cursor: 'pointer', fontSize: 13, lineHeight: 1 }}
                >✕</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

// ── page ────────────────────────────────────────────────────────────────────────
export default function AppEditor() {
  const params = useParams();
  const slug = String(params.slug ?? '');
  const [app, setApp] = useState<any>(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try {
      const d = await api(`/api/mkt/v1/store/apps/mine?slug=${encodeURIComponent(slug)}`);
      setApp(d.app);
    } catch (e: any) { setErr(e.message); }
  }, [slug]);

  useEffect(() => { if (slug) load(); }, [slug, load]);

  if (err) {
    return (
      <div>
        <a className="muted" href="/dev" style={{ fontSize: 13 }}>← My apps</a>
        <div className="panel" style={{ marginTop: 12, borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)' }}>{err}</div>
      </div>
    );
  }
  if (!app) return <div className="empty">Loading…</div>;

  const buildStatus = app.latestBuild?.status;

  return (
    <div>
      <a className="muted" href="/dev" style={{ fontSize: 13 }}>← My apps</a>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', margin: '10px 0 20px' }}>
        <h1 style={{ fontSize: 28, letterSpacing: '-0.03em', margin: 0 }}>{app.name}</h1>
        <StatusChip status={app.status} />
        {app.type === 'DIGITAL_GOOD' && <span className="badge type">digital good</span>}
        {app.verified && <span className="badge verified">✓ verified</span>}
        <div style={{ flex: 1 }} />
        <a className="btn" href={`/dev/apps/${slug}/builds`}>
          Builds{buildStatus ? ` · ${buildStatus.replace(/_/g, ' ').toLowerCase()}` : ''}
        </a>
      </div>

      <div className="mono muted" style={{ fontSize: 12.5, marginTop: -12, marginBottom: 20 }}>
        {app.packageName ? <>package <code className="inline">{app.packageName}</code> · </> : null}
        slug <code className="inline">{app.slug}</code> · created {fmtDate(app.createdAt)}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <PublishControls app={app} slug={slug} onChanged={load} />
        <MetadataForm app={app} slug={slug} onSaved={load} />
        <PricesEditor app={app} slug={slug} onSaved={load} />
        <AssetsManager app={app} slug={slug} onChanged={load} />
      </div>
    </div>
  );
}
