'use client';

/**
 * music.anm — a SoundCloud-style music app served at animica.dev/music.
 *
 * Self-contained client page. It talks to the marketplace stream API:
 *   - GET  /api/mkt/v1/stream/list?kind=AUDIO&sort=new|top   (public feed)
 *   - POST /api/mkt/v1/stream/uploads                        (raw File body, wallet session)
 *   - GET  /api/mkt/v1/stream/item/{id}/media               (Range-streamed audio)
 *   - POST /api/mkt/v1/stream/item/{id}/play                (play count)
 *   - POST /api/mkt/v1/stream/item/{id}/tip                 (record an on-chain tip)
 * Cover art (if any) is content-addressed: /api/mkt/v1/content/{posterCid}
 * Login is the window.animica connect → challenge → sign → verify session-cookie flow.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

// ── window.animica (injected wallet provider) ────────────────────────────────
type AnimicaProvider = {
  request: (args: { method: string; params?: any[] }) => Promise<any>;
};
function wallet(): AnimicaProvider | null {
  if (typeof window === 'undefined') return null;
  return (window as any).animica ?? null;
}

// ── types ────────────────────────────────────────────────────────────────────
type Track = {
  id: string;
  kind: string;
  title: string;
  creatorName: string;
  ownerAddress: string;
  posterCid: string | null;
  mime: string;
  durationSec: number | null;
  plays: number;
  tipTotalNanm: string; // BigInt serialized to string by jsonSafe
  tipCount: number;
  createdAt: string;
};

// ── helpers ────────────────────────────────────────────────────────────────────
const truncAddr = (a: string) => (a.length > 14 ? `${a.slice(0, 8)}…${a.slice(-4)}` : a);

function fmtAnm(nanm: string | number | bigint): string {
  let n: bigint;
  try {
    n = BigInt(nanm as any);
  } catch {
    return '0';
  }
  const whole = n / 1_000_000_000n;
  const frac = n % 1_000_000_000n;
  if (frac === 0n) return whole.toString();
  // up to 3 decimals, trimmed
  const dec = (frac / 1_000_000n).toString().padStart(3, '0').replace(/0+$/, '');
  return dec ? `${whole}.${dec}` : whole.toString();
}

function fmtDur(sec: number | null): string {
  if (!sec || sec <= 0) return '';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function coverGradient(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  const a = h % 360;
  const b = (a + 90) % 360;
  return `linear-gradient(135deg, hsl(${a} 70% 22%), hsl(${b} 70% 30%))`;
}

// ── page ─────────────────────────────────────────────────────────────────────
export default function MusicPage() {
  const [hasWallet, setHasWallet] = useState(true);
  const [address, setAddress] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [tab, setTab] = useState<'new' | 'top'>('new');
  const [tracks, setTracks] = useState<Track[]>([]);
  const [loadingFeed, setLoadingFeed] = useState(true);
  const [feedError, setFeedError] = useState<string | null>(null);

  // upload panel state
  const [upTitle, setUpTitle] = useState('');
  const [upCreator, setUpCreator] = useState('');
  const [upFile, setUpFile] = useState<File | null>(null);
  const [upState, setUpState] = useState<'idle' | 'uploading' | 'done' | 'error'>('idle');
  const [upPct, setUpPct] = useState(0);
  const [upMsg, setUpMsg] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const playedOnce = useRef<Set<string>>(new Set());

  useEffect(() => {
    setHasWallet(!!wallet());
  }, []);

  // ── feed ────────────────────────────────────────────────────────────────────
  const loadFeed = useCallback(async (sort: 'new' | 'top') => {
    setLoadingFeed(true);
    setFeedError(null);
    try {
      const res = await fetch(`/api/mkt/v1/stream/list?kind=AUDIO&sort=${sort}`, {
        cache: 'no-store',
      });
      if (!res.ok) throw new Error(`feed ${res.status}`);
      const data = await res.json();
      setTracks(Array.isArray(data.items) ? data.items : []);
    } catch (e: any) {
      setFeedError('Could not load the feed. Try again in a moment.');
      setTracks([]);
    } finally {
      setLoadingFeed(false);
    }
  }, []);

  useEffect(() => {
    loadFeed(tab);
  }, [tab, loadFeed]);

  // ── connect + login ──────────────────────────────────────────────────────────
  const connect = useCallback(async () => {
    const w = wallet();
    if (!w) {
      setHasWallet(false);
      return;
    }
    setConnecting(true);
    setNotice(null);
    try {
      const accounts: string[] = await w.request({ method: 'animica_requestAccounts' });
      const addr = accounts?.[0];
      if (!addr) throw new Error('No account returned by the wallet.');

      // 1. challenge
      const cRes = await fetch(
        `/api/mkt/v1/auth/challenge?address=${encodeURIComponent(addr)}`,
        { credentials: 'include', cache: 'no-store' },
      );
      if (!cRes.ok) throw new Error('Could not fetch a login challenge.');
      const { challenge } = await cRes.json();
      if (!challenge) throw new Error('Malformed challenge.');

      // 2. sign + public key
      const signature: string = await w.request({
        method: 'animica_signMessage',
        params: [{ message: challenge }],
      });
      const pk = await w.request({ method: 'animica_getPublicKey', params: [addr] });
      const publicKey = pk?.publicKey ?? pk;

      // 3. verify → session cookie
      const vRes = await fetch('/api/mkt/v1/auth/verify', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ address: addr, challenge, signature, publicKey }),
      });
      if (!vRes.ok) throw new Error('Wallet signature was rejected.');

      setAddress(addr);
      if (!upCreator) setUpCreator(truncAddr(addr));
    } catch (e: any) {
      setNotice(e?.message ?? 'Wallet connection failed.');
    } finally {
      setConnecting(false);
    }
  }, [upCreator]);

  // ── upload (XHR for real progress) ────────────────────────────────────────────
  const doUpload = useCallback(() => {
    if (!upFile) {
      setUpMsg('Choose an audio file first.');
      return;
    }
    if (!upTitle.trim()) {
      setUpMsg('Give your track a title.');
      return;
    }
    setUpState('uploading');
    setUpPct(0);
    setUpMsg(null);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/mkt/v1/stream/uploads', true);
    xhr.withCredentials = true;
    // Header values are URI-encoded so unicode titles never trip fetch/XHR's latin1 header check;
    // the server decodeURIComponent's them back.
    xhr.setRequestHeader('x-anm-kind', 'audio');
    xhr.setRequestHeader('x-anm-title', encodeURIComponent(upTitle.trim()));
    xhr.setRequestHeader('x-anm-creator', encodeURIComponent((upCreator || 'Anonymous').trim()));
    xhr.setRequestHeader('content-type', upFile.type || 'application/octet-stream');

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) setUpPct(Math.round((ev.loaded / ev.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        setUpState('done');
        setUpPct(100);
        setUpMsg('Uploaded to the DA layer. Now live in the feed.');
        setUpTitle('');
        setUpFile(null);
        if (fileInputRef.current) fileInputRef.current.value = '';
        loadFeed(tab);
      } else {
        setUpState('error');
        let m = `Upload failed (${xhr.status}).`;
        try {
          const j = JSON.parse(xhr.responseText);
          if (j?.error?.message) m = j.error.message;
        } catch {}
        setUpMsg(m);
      }
    };
    xhr.onerror = () => {
      setUpState('error');
      setUpMsg('Network error during upload.');
    };
    xhr.send(upFile);
  }, [upFile, upTitle, upCreator, tab, loadFeed]);

  // ── play count ────────────────────────────────────────────────────────────────
  const onPlay = useCallback((t: Track) => {
    if (playedOnce.current.has(t.id)) return;
    playedOnce.current.add(t.id);
    setTracks((prev) => prev.map((x) => (x.id === t.id ? { ...x, plays: x.plays + 1 } : x)));
    fetch(`/api/mkt/v1/stream/item/${t.id}/play`, {
      method: 'POST',
      credentials: 'include',
    }).catch(() => {});
  }, []);

  // ── tip ──────────────────────────────────────────────────────────────────────
  const tip = useCallback(
    async (t: Track) => {
      const w = wallet();
      if (!w || !address) {
        setNotice('Connect your wallet to tip.');
        return;
      }
      const raw = window.prompt(`Tip "${t.title}" — amount in ANM`, '1');
      if (raw == null) return;
      const anm = parseFloat(raw);
      if (!isFinite(anm) || anm <= 0) {
        setNotice('Enter a positive ANM amount.');
        return;
      }
      const value = BigInt(Math.round(anm * 1e9)).toString(); // base units (nANM)
      try {
        const txid: string = await w.request({
          method: 'animica_sendTransaction',
          params: [{ from: address, to: t.ownerAddress, value, memo: `tip:${t.id}` }],
        });
        if (!txid) throw new Error('Wallet did not return a txid.');

        await fetch(`/api/mkt/v1/stream/item/${t.id}/tip`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ txid, amountNanm: value, fromAddress: address }),
        });

        setTracks((prev) =>
          prev.map((x) =>
            x.id === t.id
              ? {
                  ...x,
                  tipTotalNanm: (BigInt(x.tipTotalNanm) + BigInt(value)).toString(),
                  tipCount: x.tipCount + 1,
                }
              : x,
          ),
        );
        setNotice(`⚡ Tipped ${fmtAnm(value)} ANM to ${t.creatorName || truncAddr(t.ownerAddress)}.`);
      } catch (e: any) {
        setNotice(e?.message ?? 'Tip failed or was rejected.');
      }
    },
    [address],
  );

  // ── render ─────────────────────────────────────────────────────────────────────
  return (
    <div className="wrap">
      <style jsx global>{`
        :root {
          --bg: #0a0e16;
          --bg2: #0e1420;
          --card: #121a28;
          --card2: #16202f;
          --line: #202b3d;
          --txt: #e7edf7;
          --muted: #8a99b3;
          --blue: #6ea8fe;
          --purple: #b98cff;
          --green: #4fe3b0;
        }
        * { box-sizing: border-box; }
        html, body {
          margin: 0;
          padding: 0;
          background: var(--bg);
          color: var(--txt);
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial,
            sans-serif;
          -webkit-font-smoothing: antialiased;
        }
        a { color: var(--blue); text-decoration: none; }
        a:hover { text-decoration: underline; }
        .wrap { min-height: 100vh; }
        .container { max-width: 940px; margin: 0 auto; padding: 0 20px; }

        header.top {
          position: sticky;
          top: 0;
          z-index: 20;
          background: rgba(10, 14, 22, 0.82);
          backdrop-filter: saturate(140%) blur(12px);
          border-bottom: 1px solid var(--line);
        }
        .toprow {
          display: flex;
          align-items: center;
          justify-content: space-between;
          height: 62px;
        }
        .wordmark {
          display: flex;
          align-items: center;
          gap: 10px;
          font-weight: 800;
          font-size: 19px;
          letter-spacing: -0.02em;
        }
        .logo {
          width: 30px;
          height: 30px;
          border-radius: 9px;
          display: grid;
          place-items: center;
          background: linear-gradient(135deg, var(--blue), var(--purple));
          box-shadow: 0 4px 16px rgba(110, 168, 254, 0.35);
          font-size: 16px;
        }
        .wordmark .accent {
          background: linear-gradient(90deg, var(--blue), var(--purple), var(--green));
          -webkit-background-clip: text;
          background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        button {
          font-family: inherit;
          cursor: pointer;
          border: none;
        }
        .btn {
          border-radius: 10px;
          padding: 9px 16px;
          font-weight: 700;
          font-size: 14px;
          transition: transform 0.06s ease, filter 0.15s ease;
        }
        .btn:active { transform: translateY(1px); }
        .btn-primary {
          color: #06121f;
          background: linear-gradient(120deg, var(--blue), var(--purple));
        }
        .btn-primary:hover { filter: brightness(1.07); }
        .btn-ghost {
          color: var(--txt);
          background: var(--card2);
          border: 1px solid var(--line);
        }
        .btn-ghost:hover { border-color: var(--blue); }
        .chip {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 7px 12px;
          border-radius: 999px;
          background: var(--card2);
          border: 1px solid var(--line);
          font-size: 13px;
          font-weight: 600;
        }
        .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 8px var(--green); }

        .hero { padding: 34px 0 8px; }
        .hero h1 {
          margin: 0 0 8px;
          font-size: 30px;
          letter-spacing: -0.03em;
          font-weight: 800;
        }
        .hero p { margin: 0; color: var(--muted); font-size: 15px; }

        .notice {
          margin: 14px 0 0;
          padding: 11px 14px;
          border-radius: 10px;
          background: rgba(110, 168, 254, 0.1);
          border: 1px solid rgba(110, 168, 254, 0.35);
          color: var(--txt);
          font-size: 14px;
        }

        .panel {
          margin-top: 20px;
          background: linear-gradient(180deg, var(--card), var(--bg2));
          border: 1px solid var(--line);
          border-radius: 16px;
          padding: 18px;
        }
        .panel h3 { margin: 0 0 4px; font-size: 15px; }
        .panel .sub { margin: 0 0 14px; color: var(--muted); font-size: 13px; }
        .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        @media (max-width: 620px) { .grid2 { grid-template-columns: 1fr; } }
        .field { display: flex; flex-direction: column; gap: 6px; }
        .field label { font-size: 12px; color: var(--muted); font-weight: 600; }
        .input {
          background: var(--bg);
          border: 1px solid var(--line);
          border-radius: 10px;
          color: var(--txt);
          padding: 10px 12px;
          font-size: 14px;
          font-family: inherit;
          outline: none;
        }
        .input:focus { border-color: var(--blue); }
        .filerow {
          margin-top: 12px;
          display: flex;
          align-items: center;
          gap: 12px;
          flex-wrap: wrap;
        }
        .fake-file {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 9px 14px;
          border-radius: 10px;
          border: 1px dashed var(--line);
          background: var(--bg);
          color: var(--muted);
          font-size: 13px;
          font-weight: 600;
        }
        .fake-file:hover { border-color: var(--purple); color: var(--txt); }
        .prog {
          margin-top: 12px;
          height: 8px;
          border-radius: 999px;
          background: var(--bg);
          overflow: hidden;
          border: 1px solid var(--line);
        }
        .prog > span {
          display: block;
          height: 100%;
          background: linear-gradient(90deg, var(--blue), var(--green));
          transition: width 0.15s ease;
        }
        .up-msg { margin-top: 10px; font-size: 13px; }
        .up-ok { color: var(--green); }
        .up-err { color: #ff8f9c; }

        .tabs { display: flex; gap: 8px; margin: 26px 0 14px; }
        .tab {
          padding: 8px 16px;
          border-radius: 999px;
          background: transparent;
          color: var(--muted);
          font-weight: 700;
          font-size: 14px;
          border: 1px solid transparent;
        }
        .tab.active { color: var(--txt); background: var(--card2); border-color: var(--line); }

        .track {
          display: flex;
          gap: 14px;
          padding: 14px;
          border: 1px solid var(--line);
          border-radius: 14px;
          background: var(--card);
          margin-bottom: 12px;
          align-items: center;
        }
        .cover {
          width: 66px;
          height: 66px;
          border-radius: 10px;
          flex: 0 0 auto;
          object-fit: cover;
          display: grid;
          place-items: center;
          font-size: 24px;
          font-weight: 800;
          color: rgba(255, 255, 255, 0.85);
        }
        .tbody { flex: 1 1 auto; min-width: 0; }
        .ttitle {
          font-weight: 700;
          font-size: 15px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .tcreator { color: var(--muted); font-size: 13px; margin-top: 1px; }
        .tmeta { display: flex; gap: 14px; color: var(--muted); font-size: 12px; margin-top: 8px; flex-wrap: wrap; }
        .tmeta b { color: var(--txt); }
        audio { width: 100%; margin-top: 10px; height: 34px; }
        .tside { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; flex: 0 0 auto; }
        .tip-btn {
          background: linear-gradient(120deg, var(--purple), var(--blue));
          color: #06121f;
          border-radius: 10px;
          padding: 8px 13px;
          font-weight: 800;
          font-size: 13px;
          white-space: nowrap;
        }
        .tip-btn:hover { filter: brightness(1.08); }
        .tipsum { font-size: 12px; color: var(--green); font-weight: 700; }

        .empty, .loading {
          text-align: center;
          color: var(--muted);
          padding: 46px 0;
          font-size: 14px;
        }
        .skel {
          height: 94px;
          border-radius: 14px;
          margin-bottom: 12px;
          background: linear-gradient(100deg, var(--card) 30%, var(--card2) 50%, var(--card) 70%);
          background-size: 200% 100%;
          animation: sh 1.2s infinite;
        }
        @keyframes sh { from { background-position: 200% 0; } to { background-position: -200% 0; } }

        footer.foot {
          border-top: 1px solid var(--line);
          margin-top: 40px;
          padding: 24px 0 40px;
          color: var(--muted);
          font-size: 13px;
        }
      `}</style>

      {/* header */}
      <header className="top">
        <div className="container toprow">
          <div className="wordmark">
            <span className="logo">🎵</span>
            <span>
              Animica <span className="accent">Music</span>
            </span>
          </div>
          {address ? (
            <span className="chip">
              <span className="dot" /> {truncAddr(address)}
            </span>
          ) : (
            <button
              className="btn btn-primary"
              onClick={connect}
              disabled={connecting}
            >
              {connecting ? 'Connecting…' : 'Connect wallet'}
            </button>
          )}
        </div>
      </header>

      <div className="container">
        {/* hero */}
        <section className="hero">
          <h1>Sound on the open network</h1>
          <p>
            Upload audio straight to the Animica DA layer. Anyone can stream it. Fans tip creators
            in ANM — peer to peer, no middleman.
          </p>
        </section>

        {notice && <div className="notice">{notice}</div>}

        {!hasWallet && (
          <div className="notice">
            No Animica wallet detected in this browser. Open Animica Music inside the Animica
            Internet browser, or{' '}
            <a href="https://animica.org/internet" target="_blank" rel="noreferrer">
              get the Animica browser & wallet
            </a>
            .
          </div>
        )}

        {/* upload */}
        {address && (
          <section className="panel">
            <h3>Upload a track</h3>
            <p className="sub">Streamed into the DA layer in chunks. Public the moment it lands.</p>
            <div className="grid2">
              <div className="field">
                <label>Title</label>
                <input
                  className="input"
                  placeholder="Midnight Drive"
                  value={upTitle}
                  onChange={(e) => setUpTitle(e.target.value)}
                />
              </div>
              <div className="field">
                <label>Creator name</label>
                <input
                  className="input"
                  placeholder="Your artist name"
                  value={upCreator}
                  onChange={(e) => setUpCreator(e.target.value)}
                />
              </div>
            </div>
            <div className="filerow">
              <label className="fake-file">
                🎧 {upFile ? upFile.name : 'Choose audio file'}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="audio/*"
                  style={{ display: 'none' }}
                  onChange={(e) => {
                    setUpFile(e.target.files?.[0] ?? null);
                    setUpState('idle');
                    setUpMsg(null);
                    setUpPct(0);
                  }}
                />
              </label>
              <button
                className="btn btn-primary"
                onClick={doUpload}
                disabled={upState === 'uploading'}
              >
                {upState === 'uploading' ? `Uploading ${upPct}%` : 'Publish'}
              </button>
              {upFile && (
                <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                  {(upFile.size / (1024 * 1024)).toFixed(1)} MB
                </span>
              )}
            </div>
            {(upState === 'uploading' || upState === 'done') && (
              <div className="prog">
                <span style={{ width: `${upPct}%` }} />
              </div>
            )}
            {upMsg && (
              <div className={`up-msg ${upState === 'error' ? 'up-err' : 'up-ok'}`}>{upMsg}</div>
            )}
          </section>
        )}

        {/* tabs */}
        <div className="tabs">
          <button
            className={`tab ${tab === 'new' ? 'active' : ''}`}
            onClick={() => setTab('new')}
          >
            New
          </button>
          <button
            className={`tab ${tab === 'top' ? 'active' : ''}`}
            onClick={() => setTab('top')}
          >
            Top by tips
          </button>
        </div>

        {/* feed */}
        {loadingFeed ? (
          <div>
            <div className="skel" />
            <div className="skel" />
            <div className="skel" />
          </div>
        ) : feedError ? (
          <div className="empty">{feedError}</div>
        ) : tracks.length === 0 ? (
          <div className="empty">
            No tracks yet. {address ? 'Be the first to upload one.' : 'Connect your wallet to upload the first track.'}
          </div>
        ) : (
          tracks.map((t) => (
            <article key={t.id} className="track">
              {t.posterCid ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  className="cover"
                  src={`/api/mkt/v1/content/${t.posterCid}`}
                  alt=""
                />
              ) : (
                <div className="cover" style={{ background: coverGradient(t.id + t.title) }}>
                  {(t.title || '♪').slice(0, 1).toUpperCase()}
                </div>
              )}
              <div className="tbody">
                <div className="ttitle">{t.title || 'Untitled'}</div>
                <div className="tcreator">{t.creatorName || truncAddr(t.ownerAddress)}</div>
                <audio
                  controls
                  preload="none"
                  src={`/api/mkt/v1/stream/item/${t.id}/media`}
                  onPlay={() => onPlay(t)}
                />
                <div className="tmeta">
                  <span>
                    ▶ <b>{t.plays.toLocaleString()}</b> plays
                  </span>
                  <span>
                    ⚡ <b>{fmtAnm(t.tipTotalNanm)}</b> ANM · {t.tipCount} tips
                  </span>
                  {fmtDur(t.durationSec) && <span>{fmtDur(t.durationSec)}</span>}
                </div>
              </div>
              <div className="tside">
                <button className="tip-btn" onClick={() => tip(t)}>
                  ⚡ Tip
                </button>
                {BigInt(t.tipTotalNanm || '0') > 0n && (
                  <span className="tipsum">{fmtAnm(t.tipTotalNanm)} ANM</span>
                )}
              </div>
            </article>
          ))
        )}

        <footer className="foot">
          <div>
            Animica Music — <span style={{ color: 'var(--muted)' }}>music.anm</span> · audio stored
            on the Animica DA layer, tips settled on-chain in ANM.
          </div>
          <div style={{ marginTop: 6 }}>
            Part of the{' '}
            <a href="https://animica.org/internet" target="_blank" rel="noreferrer">
              Animica Internet
            </a>
            .
          </div>
        </footer>
      </div>
    </div>
  );
}
