'use client';

// ── Animica Video — video.anm ────────────────────────────────────────────────
// A YouTube-style app on animica.dev/video. Connect the Animica wallet, publish videos (streamed
// into the DA layer via /api/mkt/v1/stream/uploads), browse a grid (New / Top), watch inline with a
// Range-served <video>, and tip the creator on-chain (wallet → creator's anim1 address) in ANM.
// Everything routes through the open /api/mkt/v1/stream agent API; this page is the human front door.

import { useCallback, useEffect, useRef, useState } from 'react';
import { connectAndLogin, hasWallet } from '@/components/wallet';

const API = '/api/mkt/v1/stream';

type Item = {
  id: string;
  kind: string;
  ownerAddress: string;
  title: string;
  creatorName: string;
  description?: string | null;
  posterCid?: string | null;
  plays: number;
  tipTotalNanm: string | number;
  tipCount: number;
  durationSec?: number | null;
  sizeBytes?: string | number;
  createdAt: string;
  visibility?: string;
};

type Tab = 'new' | 'top';

// ── formatting helpers ────────────────────────────────────────────────────────
function fmtAnm(nanm: unknown): string {
  const v = Number(nanm) / 1e9;
  if (!isFinite(v) || v <= 0) return '0';
  const s = v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(2) : v.toFixed(3);
  return s.replace(/\.?0+$/, '');
}
function fmtN(n: number): string {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return String(n);
}
function ago(iso?: string): string {
  const t = iso ? new Date(iso).getTime() : 0;
  if (!t) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  const units: [string, number][] = [['y', 31536000], ['mo', 2592000], ['d', 86400], ['h', 3600], ['m', 60]];
  for (const [l, sec] of units) if (s >= sec) return `${Math.floor(s / sec)}${l} ago`;
  return 'just now';
}
function fmtDur(sec?: number | null): string {
  if (!sec || sec <= 0) return '';
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
    : `${m}:${String(r).padStart(2, '0')}`;
}
function trunc(a?: string): string {
  return a && a.length > 16 ? `${a.slice(0, 8)}…${a.slice(-4)}` : a || '';
}

export default function VideoApp() {
  const [address, setAddress] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [tab, setTab] = useState<Tab>('new');
  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [watching, setWatching] = useState<Item | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [topMsg, setTopMsg] = useState('');

  // ── data ────────────────────────────────────────────────────────────────────
  const loadList = useCallback(async (which: Tab) => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/list?kind=VIDEO&sort=${which}`, { credentials: 'include' });
      const d = await res.json().catch(() => ({}));
      const list: Item[] = d.items ?? d.videos ?? d.list ?? d.results ?? [];
      setItems(Array.isArray(list) ? list : []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadList(tab); }, [tab, loadList]);

  // Optimistic patch of a single item across list + open watch view.
  const patchItem = useCallback((id: string, patch: Partial<Item>) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));
    setWatching((w) => (w && w.id === id ? { ...w, ...patch } : w));
  }, []);

  async function connect() {
    setTopMsg('');
    setConnecting(true);
    try {
      const { address } = await connectAndLogin();
      setAddress(address);
    } catch (e: any) {
      setTopMsg(e?.message || 'Could not connect wallet.');
    } finally {
      setConnecting(false);
    }
  }

  async function openWatch(item: Item) {
    setWatching(item);
    // count a play (best effort) + optimistic bump
    patchItem(item.id, { plays: (item.plays || 0) + 1 });
    fetch(`${API}/item/${item.id}/play`, { method: 'POST', credentials: 'include' }).catch(() => {});
  }

  return (
    <div className="wrap" style={{ paddingTop: 34, paddingBottom: 60 }}>
      {/* hero */}
      <div className="vhero">
        <div>
          <h1 className="vh1">
            Animica <span className="grad">Video</span>
          </h1>
          <p className="muted vsub">
            Publish, watch, and tip — a creator-owned video network. Files live on Animica DA nodes,
            tips settle in <b>ANM</b> straight to the creator&apos;s wallet. <code className="inline">video.anm</code>
          </p>
        </div>
        <div className="vhero-actions">
          {address ? (
            <>
              <span className="vaddr mono" title={address}>
                <span className="vdot" /> {trunc(address)}
              </span>
              <button className="btn primary" onClick={() => setShowUpload((s) => !s)}>
                {showUpload ? 'Close' : '＋ Upload video'}
              </button>
            </>
          ) : (
            <button className="btn primary" onClick={connect} disabled={connecting}>
              {connecting ? 'Connecting…' : hasWallet() ? 'Connect Wallet' : 'Get the wallet'}
            </button>
          )}
        </div>
      </div>

      {topMsg && <div className="vnote">{topMsg}</div>}
      {!address && (
        <div className="vnote muted">
          {hasWallet()
            ? 'Connect your Animica wallet to upload and to tip creators. Watching is open to everyone.'
            : 'Install the Animica wallet extension (animica.org/wallet) to upload and tip. You can still watch everything below.'}
        </div>
      )}

      {/* upload panel */}
      {address && showUpload && (
        <UploadPanel
          onDone={(msg) => {
            setTopMsg(msg);
            setShowUpload(false);
            setTab('new');
            loadList('new');
          }}
        />
      )}

      {/* tabs */}
      <div className="vtabs">
        <button className={`vtab ${tab === 'new' ? 'on' : ''}`} onClick={() => setTab('new')}>New</button>
        <button className={`vtab ${tab === 'top' ? 'on' : ''}`} onClick={() => setTab('top')}>Top</button>
        <span className="nav-spacer" />
        <button className="btn ghost vrefresh" onClick={() => loadList(tab)}>↻ Refresh</button>
      </div>

      {/* grid */}
      {loading ? (
        <div className="vgrid">
          {Array.from({ length: 6 }).map((_, i) => <div key={i} className="vcard vskel" />)}
        </div>
      ) : items.length === 0 ? (
        <div className="panel vempty">
          <div style={{ fontSize: 30 }}>🎬</div>
          <div style={{ fontWeight: 600, marginTop: 8 }}>No videos yet</div>
          <div className="muted" style={{ fontSize: 14, marginTop: 4 }}>
            {address ? 'Be the first — hit “＋ Upload video”.' : 'Connect a wallet to publish the first one.'}
          </div>
        </div>
      ) : (
        <div className="vgrid">
          {items.map((it) => <VideoCard key={it.id} item={it} onOpen={() => openWatch(it)} />)}
        </div>
      )}

      {/* watch modal */}
      {watching && (
        <Watch
          item={watching}
          address={address}
          onClose={() => setWatching(null)}
          onConnect={connect}
          connecting={connecting}
          onTipped={(amountNanm) =>
            patchItem(watching.id, {
              tipTotalNanm: String(Number(watching.tipTotalNanm || 0) + Number(amountNanm)),
              tipCount: (watching.tipCount || 0) + 1,
            })
          }
        />
      )}

      <Styles />
    </div>
  );
}

// ── upload ────────────────────────────────────────────────────────────────────
function UploadPanel({ onDone }: { onDone: (msg: string) => void }) {
  const [title, setTitle] = useState('');
  const [channel, setChannel] = useState('');
  const [note, setNote] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState(0);
  const [err, setErr] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  function pick(f: File | null) {
    setErr('');
    if (f && !f.type.startsWith('video/')) { setErr('That doesn’t look like a video file.'); return; }
    setFile(f);
  }

  function submit() {
    setErr('');
    if (!title.trim()) { setErr('Give it a title.'); return; }
    if (!channel.trim()) { setErr('Add a channel name.'); return; }
    if (!file) { setErr('Choose a video file.'); return; }

    setBusy(true);
    setProg(0);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}/uploads`);
    xhr.withCredentials = true;
    xhr.setRequestHeader('x-anm-kind', 'video');
    // Headers can only carry latin-1; percent-encode so arbitrary UTF-8 titles/emoji survive.
    xhr.setRequestHeader('x-anm-title', encodeURIComponent(title.trim()));
    xhr.setRequestHeader('x-anm-creator', encodeURIComponent(channel.trim()));
    if (note.trim()) xhr.setRequestHeader('x-anm-note', encodeURIComponent(note.trim()));
    xhr.setRequestHeader('content-type', file.type || 'application/octet-stream');

    xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProg(Math.round((e.loaded / e.total) * 100)); };
    xhr.onload = () => {
      setBusy(false);
      let body: any = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* noop */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        onDone(`Published “${title.trim()}”.`);
      } else {
        setErr(body?.error?.message || body?.message || `Upload failed (${xhr.status}).`);
      }
    };
    xhr.onerror = () => { setBusy(false); setErr('Network error during upload.'); };
    xhr.send(file);
  }

  return (
    <div className="panel vupload">
      <div className="vupload-grid">
        <label className="vfield">
          <span>Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="My first Animica video" maxLength={140} disabled={busy} />
        </label>
        <label className="vfield">
          <span>Channel</span>
          <input value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="Your channel name" maxLength={80} disabled={busy} />
        </label>
        <label className="vfield vfield-wide">
          <span>Thumbnail note <em className="muted">(optional)</em></span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="A short line about this video" maxLength={200} disabled={busy} />
        </label>
      </div>

      <div
        className={`vdrop ${file ? 'has' : ''}`}
        onClick={() => !busy && inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); }}
        onDrop={(e) => { e.preventDefault(); if (!busy) pick(e.dataTransfer.files?.[0] || null); }}
        role="button"
      >
        <input
          ref={inputRef}
          type="file"
          accept="video/*"
          hidden
          onChange={(e) => pick(e.target.files?.[0] || null)}
        />
        {file ? (
          <div className="vdrop-file">
            <span className="vdrop-ico">🎞️</span>
            <div>
              <div className="vdrop-name">{file.name}</div>
              <div className="muted" style={{ fontSize: 12.5 }}>{(file.size / 1048576).toFixed(1)} MB · {file.type || 'video'}</div>
            </div>
            {!busy && <button className="btn ghost vsmall" onClick={(e) => { e.stopPropagation(); pick(null); }}>Change</button>}
          </div>
        ) : (
          <div className="vdrop-empty">
            <span className="vdrop-ico">⬆️</span>
            <div>Drop a video here, or <span className="vlink">choose a file</span></div>
            <div className="muted" style={{ fontSize: 12.5 }}>Streamed straight to Animica DA nodes — big files are fine.</div>
          </div>
        )}
      </div>

      {busy && (
        <div className="vprog">
          <div className="vprog-bar"><div className="vprog-fill" style={{ width: `${prog}%` }} /></div>
          <div className="muted mono" style={{ fontSize: 12.5, marginTop: 6 }}>Uploading… {prog}%</div>
        </div>
      )}

      {err && <div className="vnote vnote-bad">{err}</div>}

      <div className="vupload-actions">
        <button className="btn primary" onClick={submit} disabled={busy}>
          {busy ? 'Publishing…' : 'Publish video'}
        </button>
      </div>
    </div>
  );
}

// ── card ──────────────────────────────────────────────────────────────────────
function VideoCard({ item, onOpen }: { item: Item; onOpen: () => void }) {
  const d = fmtDur(item.durationSec);
  return (
    <button className="vcard" onClick={onOpen}>
      <div className="vthumb">
        {item.posterCid ? (
          <img
            src={`${API}/item/${item.id}/poster`}
            alt=""
            loading="lazy"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        ) : null}
        <span className="vplay">▶</span>
        {d && <span className="vdur">{d}</span>}
      </div>
      <div className="vcard-body">
        <div className="vcard-title" title={item.title}>{item.title || 'Untitled'}</div>
        <div className="vcard-chan">{item.creatorName || 'Unknown channel'}</div>
        <div className="vcard-meta">
          <span>{fmtN(item.plays)} views</span>
          <span>·</span>
          <span>{ago(item.createdAt)}</span>
          {Number(item.tipTotalNanm) > 0 && (
            <>
              <span>·</span>
              <span className="vtip-badge">⚡ {fmtAnm(item.tipTotalNanm)} ANM</span>
            </>
          )}
        </div>
      </div>
    </button>
  );
}

// ── watch ─────────────────────────────────────────────────────────────────────
function Watch({
  item,
  address,
  onClose,
  onConnect,
  connecting,
  onTipped,
}: {
  item: Item;
  address: string | null;
  onClose: () => void;
  onConnect: () => void;
  connecting: boolean;
  onTipped: (amountNanm: string) => void;
}) {
  const [amt, setAmt] = useState('1');
  const [tipping, setTipping] = useState(false);
  const [tipMsg, setTipMsg] = useState('');
  const presets = ['0.5', '1', '5', '10'];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function tip() {
    setTipMsg('');
    const anm = Number(amt);
    if (!isFinite(anm) || anm <= 0) { setTipMsg('Enter an amount greater than 0.'); return; }
    if (!address) { setTipMsg('Connect a wallet first.'); return; }
    if (!item.ownerAddress) { setTipMsg('This creator has no payout address.'); return; }
    const w = (typeof window !== 'undefined' ? (window as any).animica : null);
    if (!w?.isAnimica) { setTipMsg('Animica wallet not found.'); return; }

    setTipping(true);
    try {
      const value = String(BigInt(Math.round(anm * 1e9))); // nANM base units
      const txid: string = await w.request({
        method: 'animica_sendTransaction',
        params: [{ from: address, to: item.ownerAddress, value, memo: `tip:${item.id}` }],
      });
      const res = await fetch(`${API}/item/${item.id}/tip`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ txid, amountNanm: value, fromAddress: address }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.error?.message || b?.message || 'Tip recorded on-chain but the server rejected it.');
      }
      onTipped(value);
      setTipMsg(`Sent ${fmtAnm(value)} ANM — thanks for supporting ${item.creatorName || 'the creator'}! ⚡`);
    } catch (e: any) {
      setTipMsg(e?.message || 'Tip cancelled.');
    } finally {
      setTipping(false);
    }
  }

  return (
    <div className="vmodal" onClick={onClose}>
      <div className="vmodal-inner" onClick={(e) => e.stopPropagation()}>
        <button className="vclose" onClick={onClose} aria-label="Close">✕</button>
        <div className="vplayer">
          <video
            controls
            autoPlay
            playsInline
            src={`${API}/item/${item.id}/media`}
            poster={item.posterCid ? `${API}/item/${item.id}/poster` : undefined}
          />
        </div>
        <div className="vwatch-body">
          <h2 className="vwatch-title">{item.title || 'Untitled'}</h2>
          <div className="vwatch-meta">
            <span className="vwatch-chan">{item.creatorName || 'Unknown channel'}</span>
            <span className="muted">· {fmtN(item.plays)} views · {ago(item.createdAt)}</span>
          </div>

          {item.description && <p className="vwatch-desc">{item.description}</p>}

          <div className="vtip">
            <div className="vtip-head">
              <span className="vtip-title">⚡ Tip creator</span>
              {Number(item.tipTotalNanm) > 0 && (
                <span className="muted mono" style={{ fontSize: 12.5 }}>
                  {fmtAnm(item.tipTotalNanm)} ANM · {fmtN(item.tipCount)} tips
                </span>
              )}
            </div>

            {address ? (
              <>
                <div className="vtip-row">
                  {presets.map((p) => (
                    <button
                      key={p}
                      className={`vchip ${amt === p ? 'on' : ''}`}
                      onClick={() => setAmt(p)}
                      disabled={tipping}
                    >
                      {p} ANM
                    </button>
                  ))}
                  <div className="vtip-custom">
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      value={amt}
                      onChange={(e) => setAmt(e.target.value)}
                      disabled={tipping}
                    />
                    <span className="muted">ANM</span>
                  </div>
                  <button className="btn primary vtip-send" onClick={tip} disabled={tipping}>
                    {tipping ? 'Sending…' : `⚡ Tip ${amt || '0'} ANM`}
                  </button>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                  Paid wallet → creator (<span className="mono">{trunc(item.ownerAddress)}</span>), on-chain, no middleman.
                </div>
              </>
            ) : (
              <div className="vtip-connect">
                <span className="muted" style={{ fontSize: 13.5 }}>Connect your wallet to send a tip.</span>
                <button className="btn primary vsmall" onClick={onConnect} disabled={connecting}>
                  {connecting ? 'Connecting…' : 'Connect Wallet'}
                </button>
              </div>
            )}

            {tipMsg && <div className="vnote" style={{ marginTop: 10 }}>{tipMsg}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────
function Styles() {
  return (
    <style>{`
      .vhero { display:flex; align-items:flex-end; justify-content:space-between; gap:22px; flex-wrap:wrap; margin-bottom:14px; }
      .vh1 { font-size:38px; letter-spacing:-0.035em; margin:0; }
      .vh1 .grad { background:linear-gradient(120deg, var(--accent-2), var(--accent) 60%, #b6aaff); -webkit-background-clip:text; background-clip:text; color:transparent; }
      .vsub { max-width:640px; font-size:14.5px; margin:8px 0 0; line-height:1.5; }
      .vhero-actions { display:flex; align-items:center; gap:12px; }
      .vaddr { display:inline-flex; align-items:center; gap:8px; font-size:12.5px; color:var(--text-dim); border:1px solid var(--border); background:var(--bg-elev); border-radius:999px; padding:7px 13px; }
      .vdot { width:8px; height:8px; border-radius:50%; background:var(--good); box-shadow:0 0 8px var(--good); }

      .vnote { margin:14px 0 0; font-size:13.5px; color:var(--text-dim); background:var(--bg-elev); border:1px solid var(--border); border-radius:10px; padding:10px 14px; }
      .vnote-bad { color:var(--bad); border-color:rgba(255,92,114,0.4); background:rgba(255,92,114,0.08); }

      /* upload */
      .vupload { margin-top:16px; }
      .vupload-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
      .vfield { display:flex; flex-direction:column; gap:6px; }
      .vfield-wide { grid-column:1 / -1; }
      .vfield > span { font-size:12.5px; color:var(--text-dim); font-weight:600; }
      .vfield > span em { font-style:normal; font-weight:400; }
      .vfield input { background:var(--bg-elev); border:1px solid var(--border-bright); border-radius:10px; color:var(--text); padding:11px 13px; font-size:14.5px; outline:none; }
      .vfield input:focus { border-color:var(--accent); }
      .vdrop { margin-top:14px; border:1.5px dashed var(--border-bright); border-radius:14px; padding:22px; cursor:pointer; transition:border-color .15s, background .15s; }
      .vdrop:hover { border-color:var(--accent); background:rgba(108,92,255,0.04); }
      .vdrop.has { border-style:solid; border-color:var(--border); }
      .vdrop-empty { display:flex; flex-direction:column; align-items:center; gap:4px; text-align:center; font-size:14px; color:var(--text-dim); }
      .vdrop-file { display:flex; align-items:center; gap:14px; }
      .vdrop-name { font-weight:600; font-size:14.5px; word-break:break-all; }
      .vdrop-ico { font-size:26px; }
      .vlink { color:var(--accent-2); }
      .vprog { margin-top:14px; }
      .vprog-bar { height:8px; border-radius:999px; background:var(--bg-elev); border:1px solid var(--border); overflow:hidden; }
      .vprog-fill { height:100%; background:linear-gradient(90deg, var(--accent), var(--accent-2)); transition:width .2s; }
      .vupload-actions { margin-top:16px; display:flex; justify-content:flex-end; }
      .vsmall { padding:7px 12px; font-size:13px; }

      /* tabs */
      .vtabs { display:flex; align-items:center; gap:8px; margin:26px 0 16px; border-bottom:1px solid var(--border); padding-bottom:0; }
      .vtab { background:transparent; border:0; color:var(--text-dim); font-size:15px; font-weight:600; padding:8px 4px; margin-right:14px; cursor:pointer; border-bottom:2px solid transparent; }
      .vtab:hover { color:var(--text); }
      .vtab.on { color:var(--text); border-bottom-color:var(--accent); }
      .vrefresh { padding:6px 12px; font-size:13px; margin-bottom:6px; }

      /* grid + card */
      .vgrid { display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:18px; }
      .vcard { text-align:left; background:transparent; border:0; padding:0; cursor:pointer; color:inherit; display:flex; flex-direction:column; gap:10px; }
      .vthumb { position:relative; aspect-ratio:16/9; border-radius:12px; overflow:hidden; background:linear-gradient(135deg, #1a1f36, #10131f); border:1px solid var(--border); display:grid; place-items:center; }
      .vthumb img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }
      .vplay { position:relative; z-index:1; width:52px; height:52px; border-radius:50%; background:rgba(7,8,12,0.55); backdrop-filter:blur(4px); display:grid; place-items:center; font-size:19px; color:#fff; border:1px solid rgba(255,255,255,0.18); transition:transform .15s, background .15s; }
      .vcard:hover .vplay { transform:scale(1.08); background:var(--accent); border-color:transparent; box-shadow:0 6px 22px var(--accent-glow); }
      .vcard:hover .vthumb { border-color:var(--border-bright); }
      .vdur { position:absolute; right:8px; bottom:8px; z-index:1; font-size:11.5px; font-family:var(--mono); background:rgba(7,8,12,0.82); color:#fff; padding:2px 6px; border-radius:5px; }
      .vcard-body { display:flex; flex-direction:column; gap:2px; }
      .vcard-title { font-size:15px; font-weight:600; letter-spacing:-0.01em; line-height:1.3; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
      .vcard-chan { font-size:13px; color:var(--text-dim); margin-top:2px; }
      .vcard-meta { display:flex; align-items:center; gap:6px; flex-wrap:wrap; font-size:12.5px; color:var(--text-faint); margin-top:2px; }
      .vtip-badge { color:var(--accent-2); }
      .vskel { aspect-ratio:16/9; border-radius:12px; background:linear-gradient(100deg, #12151f 30%, #191d2b 50%, #12151f 70%); background-size:200% 100%; animation:vshine 1.3s infinite; border:1px solid var(--border); }
      @keyframes vshine { to { background-position:-200% 0; } }

      .vempty { text-align:center; padding:48px 22px; }

      /* watch modal */
      .vmodal { position:fixed; inset:0; z-index:100; background:rgba(4,5,9,0.78); backdrop-filter:blur(6px); display:flex; align-items:flex-start; justify-content:center; padding:40px 20px; overflow:auto; }
      .vmodal-inner { position:relative; width:100%; max-width:920px; background:var(--bg-card); border:1px solid var(--border-bright); border-radius:16px; overflow:hidden; box-shadow:0 30px 80px rgba(0,0,0,0.55); }
      .vclose { position:absolute; top:12px; right:12px; z-index:3; width:34px; height:34px; border-radius:50%; background:rgba(7,8,12,0.7); color:#fff; border:1px solid var(--border-bright); cursor:pointer; font-size:14px; }
      .vclose:hover { background:var(--accent); border-color:transparent; }
      .vplayer { background:#000; aspect-ratio:16/9; }
      .vplayer video { width:100%; height:100%; display:block; background:#000; }
      .vwatch-body { padding:20px 22px 24px; }
      .vwatch-title { font-size:21px; margin:0; letter-spacing:-0.02em; }
      .vwatch-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:6px; font-size:13.5px; }
      .vwatch-chan { font-weight:600; }
      .vwatch-desc { color:var(--text-dim); font-size:14px; line-height:1.55; margin:14px 0 0; white-space:pre-wrap; }

      .vtip { margin-top:20px; border:1px solid var(--border); border-radius:14px; padding:16px; background:var(--bg-elev); }
      .vtip-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:12px; }
      .vtip-title { font-weight:700; font-size:15px; }
      .vtip-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
      .vchip { background:var(--bg-card); border:1px solid var(--border-bright); color:var(--text-dim); border-radius:999px; padding:8px 14px; font-size:13px; font-weight:600; cursor:pointer; }
      .vchip:hover { color:var(--text); border-color:var(--accent); }
      .vchip.on { color:#fff; background:rgba(108,92,255,0.18); border-color:var(--accent); }
      .vtip-custom { display:flex; align-items:center; gap:6px; background:var(--bg-card); border:1px solid var(--border-bright); border-radius:10px; padding:2px 10px 2px 4px; }
      .vtip-custom input { width:74px; background:transparent; border:0; outline:0; color:var(--text); font-size:14px; padding:8px 6px; text-align:right; }
      .vtip-send { margin-left:auto; }
      .vtip-connect { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }

      @media (max-width:620px) {
        .vh1 { font-size:30px; }
        .vupload-grid { grid-template-columns:1fr; }
        .vhero { align-items:flex-start; }
        .vtip-send { margin-left:0; width:100%; justify-content:center; }
      }
    `}</style>
  );
}
