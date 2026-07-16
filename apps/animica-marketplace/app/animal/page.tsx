'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

// ── Animica Animal — operator console ─────────────────────────────────────────
// Sign in (rate-limited), connect the mascot's OWNED social accounts, and steer its continuous
// content stream via the goal chat. Live posting stays gated on the engine side; this console can
// connect accounts, pause the stream, and direct it — never widen posting authority.

const API = '/api/mkt/v1/animal';

type Conn = {
  platform: string; label: string; emoji: string; status: string; handle: string;
  autoPost: boolean; configured: boolean; manualOk: boolean; supportsMusic: boolean;
  note: string; connectedAt: string | null; lastPostAt: string | null;
};
type Directive = { role: string; kind: string; text: string; createdAt?: string };
type Post = { id: string; platform: string; kind: string; status: string; caption: string; createdAt: string };
type Status = {
  engine: { running: boolean; alive: boolean; dryRun: boolean; paused: boolean; lastHeartbeat: string | null };
  counts: { connected: number; posted: number; previews: number };
};

async function api(path: string, opts?: RequestInit) {
  const res = await fetch(API + path, { credentials: 'same-origin', ...opts });
  const j = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data: j };
}

export default function AnimalConsole() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => { api('/session').then((r) => setAuthed(!!r.data?.authed)); }, []);

  if (authed === null) return <Shell><div className="center muted">Loading…</div></Shell>;
  return authed ? <Console onLogout={() => setAuthed(false)} /> : <Login onIn={() => setAuthed(true)} />;
}

// ── login gate ────────────────────────────────────────────────────────────────
function Login({ onIn }: { onIn: () => void }) {
  const [user, setUser] = useState('animica');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr('');
    const r = await api('/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ user, password }) });
    setBusy(false);
    if (r.ok) onIn();
    else setErr(r.data?.message || 'Login failed.');
  };

  return (
    <Shell>
      <div className="loginwrap">
        <Mascot size={92} />
        <h1 className="brand">Animica Animal</h1>
        <p className="tagline">Your own 24/7 AI livestreamer</p>
      </div>
      <Pitch />
      <div className="loginwrap tight">
        <form onSubmit={submit} className="card login">
          <div className="loglbl">Operator sign-in</div>
          <label>Operator</label>
          <input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" />
          <label>Password</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          {err && <div className="error">{err}</div>}
          <button disabled={busy || !password} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
          <p className="fineprint">Already subscribed? Password attempts are rate-limited.</p>
        </form>
      </div>
    </Shell>
  );
}

// ── public product pitch + PayPal subscribe ─────────────────────────────────────
function Pitch() {
  const [price, setPrice] = useState<{ priceUsd: number; subscribeUrl: string; configured: boolean; contactEmail: string } | null>(null);
  useEffect(() => { api('/pricing').then((r) => { if (r.ok) setPrice(r.data); }); }, []);
  const usd = price?.priceUsd ?? 350;

  const FEATURES: [string, string][] = [
    ['🎥', '24/7 YouTube livestream of an animated character that never sleeps'],
    ['💬', 'Reads your live chat and replies out loud, in-character, in real time'],
    ['🐾', 'Design any character by chat, tune the palette, or upload your own PNG mascot'],
    ['📚', 'Give it a private knowledge base so it answers with facts unique to your world'],
    ['🎬', 'Auto-uploads every 1-hour segment as a VOD — your channel fills itself'],
    ['🎛️', 'Steer topics live from the console; runs on your own GPU box'],
  ];

  return (
    <section className="pitch">
      <div className="pitchgrid">
        {FEATURES.map(([e, t]) => (
          <div className="feat" key={t}><span className="fe">{e}</span><span>{t}</span></div>
        ))}
      </div>
      <div className="card pricecard">
        <div className="pricetop">
          <div className="priceamt">${usd}<span className="permo">/month</span></div>
          <div className="muted xs">Everything above · unlimited streaming · cancel anytime</div>
        </div>
        {price?.configured
          ? <a className="subbtn" href={price.subscribeUrl} target="_blank" rel="noreferrer">Subscribe with PayPal</a>
          : <div className="subpending">
              <button className="subbtn" disabled>Subscribe with PayPal</button>
              <p className="fineprint">Checkout is being finalized. Email <b>{price?.contactEmail || 'ai@3vdc.com'}</b> to start now.</p>
            </div>}
        <p className="fineprint">Secure recurring billing via PayPal. Your channel, your accounts — Animica Animal posts only through official APIs to accounts you own.</p>
      </div>
    </section>
  );
}

// ── console ─────────────────────────────────────────────────────────────────
function Console({ onLogout }: { onLogout: () => void }) {
  const [conns, setConns] = useState<Conn[]>([]);
  const [dirs, setDirs] = useState<Directive[]>([]);
  const [posts, setPosts] = useState<Post[]>([]);
  const [status, setStatus] = useState<Status | null>(null);

  const load = useCallback(async () => {
    const [c, d, p, s] = await Promise.all([api('/connections'), api('/directives'), api('/posts'), api('/status')]);
    if (c.ok) setConns(c.data.connections || []);
    if (d.ok) setDirs(d.data.directives || []);
    if (p.ok) setPosts(p.data.posts || []);
    if (s.ok) setStatus(s.data);
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  const logout = async () => { await api('/logout', { method: 'POST' }); onLogout(); };
  const setPaused = async (paused: boolean) => { await api('/control', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ paused }) }); load(); };

  const eng = status?.engine;
  const posture = eng?.paused ? 'paused' : eng?.running ? (eng.dryRun ? 'dry-run' : 'live') : 'offline';

  return (
    <Shell>
      <header className="topbar">
        <div className="ident"><Mascot size={44} /><div><div className="brand sm">Animica Animal</div><div className="muted xs">autonomous ambassador</div></div></div>
        <div className="controls">
          <span className={`pill ${posture}`}>{posture === 'live' ? '● LIVE' : posture === 'dry-run' ? '◐ dry-run' : posture === 'paused' ? '⏸ paused' : '○ offline'}</span>
          {eng?.paused
            ? <button className="ghost" onClick={() => setPaused(false)}>Resume</button>
            : <button className="ghost" onClick={() => setPaused(true)}>Pause stream</button>}
          <button className="ghost" onClick={logout}>Sign out</button>
        </div>
      </header>

      <div className="stats">
        <Stat n={status?.counts.connected ?? 0} label="channels connected" />
        <Stat n={status?.counts.posted ?? 0} label="live posts" />
        <Stat n={status?.counts.previews ?? 0} label="previews generated" />
        <Stat n={eng?.alive ? 'on' : 'off'} label="engine heartbeat" />
      </div>

      <section>
        <h2>Connect socials</h2>
        <p className="muted">Link accounts <b>you own</b> — the mascot posts through official APIs to your connected channels. It never creates accounts.</p>
        <div className="grid">
          {conns.map((c) => <ConnCard key={c.platform} c={c} reload={load} />)}
        </div>
      </section>

      <div className="two">
        <section className="chatcol">
          <h2>Steer the stream</h2>
          <p className="muted">Tell Animica Animal what to focus on. It reads your goals each cycle and adjusts its continuous content.</p>
          <Chat dirs={dirs} reload={load} />
        </section>
        <section className="actcol">
          <h2>Recent content</h2>
          <div className="feed">
            {posts.length === 0 && <div className="muted center pad">No content yet — connect a channel and set a goal.</div>}
            {posts.map((p) => (
              <div className="postrow" key={p.id}>
                <span className={`dot ${p.status}`} />
                <span className="pfx">{p.platform}</span>
                <span className={`tag ${p.status}`}>{p.status.toLowerCase().replace('_', '-')}</span>
                <span className="cap">{p.caption || <i className="muted">(media only)</i>}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <LiveStudio conns={conns} />
      <CharacterStudio />
    </Shell>
  );
}

// ── 24/7 livestream studio ──────────────────────────────────────────────────────
function LiveStudio({ conns }: { conns: Conn[] }) {
  const [live, setLive] = useState<any>({ live: false });
  const yt = conns.find((c) => c.platform === 'youtube');
  const ytConnected = yt?.status === 'CONNECTED';

  useEffect(() => {
    const poll = () => api('/live').then((r) => { if (r.ok) setLive(r.data.live || { live: false }); });
    poll(); const t = setInterval(poll, 10000); return () => clearInterval(t);
  }, []);

  const hhmm = (s: number) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  };

  return (
    <section>
      <h2>Live studio · YouTube 24/7</h2>
      <p className="muted">A continuously rendered, chat-interactive livestream of your character — with automatic 1-hour VOD segment uploads. Connect YouTube above, then start the stream worker from any GPU box.</p>
      <div className="studio card">
        <div className="livehead">
          <span className={`pill ${live.live ? 'live' : 'offline'}`}>{live.live ? '● LIVE' : '○ offline'}</span>
          {live.live && <span className="livemeta">{Number(live.viewers) || 0} watching · up {hhmm(Number(live.uptime) || 0)}{live.character ? ` · ${live.character}` : ''}</span>}
          {live.live && live.watchUrl && <a className="watchbtn" href={live.watchUrl} target="_blank" rel="noreferrer">Watch on YouTube ↗</a>}
        </div>
        <div className="studiobody">
          <ol className="steps">
            <li className={ytConnected ? 'done' : ''}>
              <b>Connect YouTube</b> — {ytConnected ? <span className="ok">connected as {yt?.handle ? `@${yt.handle}` : 'your channel'} ✓</span> : <span className="warn">use the YouTube card above (grants live-broadcast + upload).</span>}
            </li>
            <li>
              <b>Install the node</b> on a machine with a GPU (or CPU for a lighter render):
              <pre className="cmd">pip install -U animica</pre>
            </li>
            <li>
              <b>Go live</b> — the worker renders the character, reads YouTube live chat, replies with voice + on-screen captions, and streams over RTMP:
              <pre className="cmd">animica animal stream --youtube --record-dir ./vods</pre>
              <span className="stepnote">It pulls the live-editable character + your Google OAuth from this console automatically. Segments in <code>./vods</code> upload as hourly VODs and are deleted after upload. Preview locally first with <code>--preview --seconds 20 out.mp4</code>.</span>
            </li>
          </ol>
        </div>
      </div>
    </section>
  );
}

// ── character studio: chat editor + palette + sprite + knowledge ────────────────
type Character = {
  name: string; species: string; kind: 'cat' | 'sprite'; sprite_url: string; knowledge_ref: string;
  personality: string; speaking_style: string; catchphrases: string[]; topics: string[];
  fur: number[]; fur_dk: number[]; belly: number[]; iris: number[]; accent: number[];
  default_emotion: string; voice_pitch: number; voice_wpm: number;
};
const hex = (rgb: number[]) => '#' + rgb.map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, '0')).join('');
const fromHex = (h: string): number[] => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) || 0);

function CharacterStudio() {
  const [ch, setCh] = useState<Character | null>(null);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [kb, setKb] = useState<{ sources: string[]; chunks: number }>({ sources: [], chunks: 0 });
  const [paste, setPaste] = useState('');
  const [msg, setMsg] = useState('');
  const spriteInput = useRef<HTMLInputElement>(null);
  const docInput = useRef<HTMLInputElement>(null);

  const loadCh = useCallback(async () => {
    const r = await api('/character'); if (r.ok) setCh(r.data.character);
    const k = await api('/character/knowledge'); if (k.ok) setKb({ sources: k.data.sources || [], chunks: k.data.chunks || 0 });
  }, []);
  useEffect(() => { loadCh(); }, [loadCh]);

  const flash = (t: string) => { setMsg(t); setTimeout(() => setMsg(''), 3500); };

  const send = async (body: any, ok = 'Saved.') => {
    setBusy(true);
    const r = await api('/character', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    setBusy(false);
    if (r.ok) { setCh(r.data.character); flash(ok); } else flash(r.data?.error || 'Failed.');
  };
  const applyPrompt = async () => { if (!prompt.trim()) return; await send({ prompt: prompt.trim() }, 'Character updated.'); setPrompt(''); };
  const patchColor = (field: string, h: string) => send({ patch: { [field]: fromHex(h) } });

  const uploadSprite = async (f: File) => {
    setBusy(true); setMsg('Uploading sprite…');
    const fd = new FormData(); fd.append('file', f);
    const res = await fetch(API + '/character/sprite', { method: 'POST', credentials: 'same-origin', body: fd });
    const j = await res.json().catch(() => ({}));
    setBusy(false);
    if (res.ok) { setCh(j.character); flash('Custom sprite set.'); } else flash(j?.error || 'Upload failed.');
  };
  const uploadDoc = async (f: File) => {
    setBusy(true); setMsg('Ingesting document…');
    const fd = new FormData(); fd.append('file', f);
    const res = await fetch(API + '/character/knowledge', { method: 'POST', credentials: 'same-origin', body: fd });
    const j = await res.json().catch(() => ({}));
    setBusy(false);
    if (res.ok) { flash(`Added ${j.chunksAdded} chunks from ${j.source}.`); loadCh(); } else flash(j?.error || 'Upload failed.');
  };
  const addPaste = async () => {
    if (!paste.trim()) return;
    setBusy(true);
    const r = await api('/character/knowledge', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ source: 'pasted note', text: paste }) });
    setBusy(false);
    if (r.ok) { setPaste(''); flash(`Added ${r.data.chunksAdded} chunks.`); loadCh(); } else flash(r.data?.error || 'Failed.');
  };
  const clearKb = async () => {
    if (!confirm('Clear this character\'s entire knowledge base?')) return;
    const r = await api('/character/knowledge', { method: 'DELETE' });
    if (r.ok) { flash(`Cleared ${r.data.cleared} chunks.`); loadCh(); }
  };

  if (!ch) return null;
  const COLS: [string, keyof Character][] = [['Fur', 'fur'], ['Fur (dark)', 'fur_dk'], ['Belly', 'belly'], ['Eyes', 'iris'], ['Accent', 'accent']];

  return (
    <section>
      <h2>Character studio</h2>
      <p className="muted">Design who goes live. Animica ships with Momo the cat — end users can restyle her by chat, tweak the palette, upload a PNG mascot, and give the character its own knowledge base for grounded replies.</p>
      {msg && <div className="flash">{msg}</div>}
      <div className="charwrap">
        <div className="card charmain">
          <div className="chartop">
            {ch.kind === 'sprite' && ch.sprite_url
              ? <img className="spritepreview" src={ch.sprite_url} alt="character sprite" />
              : <Mascot size={72} />}
            <div>
              <div className="charname">{ch.name} <span className="muted xs">· {ch.species} · {ch.kind === 'sprite' ? 'custom PNG' : 'built-in cat'} · {ch.default_emotion}</span></div>
              <div className="muted xs charpers">{ch.personality}</div>
            </div>
          </div>

          <label className="fieldlbl">Redesign by chat</label>
          <div className="promptrow">
            <input value={prompt} placeholder="e.g. make her a sassy blue dragon who loves DeFi" onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') applyPrompt(); }} />
            <button disabled={busy || !prompt.trim()} onClick={applyPrompt}>Apply</button>
          </div>

          <label className="fieldlbl">Palette</label>
          <div className="palette">
            {COLS.map(([lbl, f]) => (
              <label key={f} className="swatch">
                <input type="color" value={hex(ch[f] as number[])} onChange={(e) => patchColor(f as string, e.target.value)} />
                <span>{lbl}</span>
              </label>
            ))}
          </div>

          <div className="sliders">
            <label>Voice pitch <b>{ch.voice_pitch.toFixed(2)}×</b>
              <input type="range" min={0.5} max={2} step={0.05} value={ch.voice_pitch}
                onChange={(e) => setCh({ ...ch, voice_pitch: Number(e.target.value) })}
                onMouseUp={(e) => send({ patch: { voice_pitch: Number((e.target as HTMLInputElement).value) } }, 'Voice updated.')} />
            </label>
            <label>Speech pace <b>{ch.voice_wpm} wpm</b>
              <input type="range" min={60} max={260} step={5} value={ch.voice_wpm}
                onChange={(e) => setCh({ ...ch, voice_wpm: Number(e.target.value) })}
                onMouseUp={(e) => send({ patch: { voice_wpm: Number((e.target as HTMLInputElement).value) } }, 'Voice updated.')} />
            </label>
          </div>

          <div className="charact">
            <input ref={spriteInput} type="file" accept="image/png" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadSprite(f); e.target.value = ''; }} />
            <button className="ghost sm" disabled={busy} onClick={() => spriteInput.current?.click()}>Upload PNG mascot</button>
            {ch.kind === 'sprite' && <button className="ghost sm" disabled={busy} onClick={() => send({ patch: { kind: 'cat', sprite_url: '' } }, 'Back to the cat.')}>Use built-in cat</button>}
            <button className="ghost sm danger" disabled={busy} onClick={() => send({ reset: true }, 'Reset to Animica cat.')}>Reset to Momo</button>
          </div>
        </div>

        <div className="card charkb">
          <div className="kbhead"><b>Knowledge base</b><span className="muted xs">{kb.chunks} chunks · {kb.sources.length} sources</span></div>
          <p className="muted xs">Upload docs (.txt/.md/.csv/.json) or paste notes. The livestream brain retrieves from these to answer chat in-character with facts unique to your world.</p>
          <input ref={docInput} type="file" accept=".txt,.md,.markdown,.csv,.json,.text" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadDoc(f); e.target.value = ''; }} />
          <div className="kbact">
            <button className="ghost sm" disabled={busy} onClick={() => docInput.current?.click()}>Upload document</button>
            {kb.chunks > 0 && <button className="ghost sm danger" disabled={busy} onClick={clearKb}>Clear all</button>}
          </div>
          <textarea className="kbpaste" placeholder="…or paste knowledge/notes here" value={paste} onChange={(e) => setPaste(e.target.value)} />
          <button className="sm" disabled={busy || !paste.trim()} onClick={addPaste}>Add note</button>
          {kb.sources.length > 0 && (
            <div className="kbsources">{kb.sources.slice(0, 8).map((s, i) => <span key={i} className="kbtag">{s}</span>)}</div>
          )}
        </div>
      </div>
    </section>
  );
}

function Stat({ n, label }: { n: number | string; label: string }) {
  return <div className="statcard"><div className="statn">{n}</div><div className="statl">{label}</div></div>;
}

function ConnCard({ c, reload }: { c: Conn; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState('');
  const [handle, setHandle] = useState('');
  const [owned, setOwned] = useState(false);
  const [msg, setMsg] = useState('');
  const connected = c.status === 'CONNECTED';

  const connect = async () => {
    const r = await api(`/connect/${c.platform}/start`);
    if (r.data?.configured && r.data?.authorizeUrl) { window.location.href = r.data.authorizeUrl; return; }
    setOpen(true);
    setMsg(r.data?.message || 'Paste a token for your owned account to connect.');
  };
  const manual = async () => {
    setMsg('');
    const r = await api(`/connect/${c.platform}/manual`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ accessToken: token, handle, ownedAttestation: owned }) });
    if (r.ok) { setOpen(false); setToken(''); reload(); } else setMsg(r.data?.error || 'Failed.');
  };
  const disconnect = async () => { await api(`/disconnect/${c.platform}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }); reload(); };
  const toggleAuto = async () => { await api(`/disconnect/${c.platform}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ autoPost: !c.autoPost }) }); reload(); };

  return (
    <div className={`card conn ${connected ? 'on' : ''}`}>
      <div className="connhead">
        <span className="emoji">{c.emoji}</span>
        <div className="cinfo">
          <div className="clabel">{c.label} {c.supportsMusic && <span className="music" title="custom music/audio">♪</span>}</div>
          <div className="cstatus">
            {connected ? <span className="ok">Connected{c.handle ? ` · @${c.handle}` : ''}</span>
              : c.configured ? <span className="muted">Ready to connect</span>
              : <span className="warn">Configure or paste token</span>}
          </div>
        </div>
      </div>
      <p className="cnote">{c.note}</p>
      <div className="connact">
        {connected ? (
          <>
            <button className="ghost sm" onClick={toggleAuto}>{c.autoPost ? 'Auto-post: on' : 'Auto-post: off'}</button>
            <button className="ghost sm danger" onClick={disconnect}>Disconnect</button>
          </>
        ) : (
          <>
            <button className="sm" onClick={connect}>Connect</button>
            {c.manualOk && <button className="ghost sm" onClick={() => setOpen(!open)}>Paste token</button>}
          </>
        )}
      </div>
      {open && !connected && (
        <div className="manual">
          {msg && <div className="hint">{msg}</div>}
          <input placeholder="@handle (optional)" value={handle} onChange={(e) => setHandle(e.target.value)} />
          <input placeholder="access token" value={token} onChange={(e) => setToken(e.target.value)} />
          <label className="chk"><input type="checkbox" checked={owned} onChange={(e) => setOwned(e.target.checked)} /> I own this account</label>
          <button className="sm" disabled={!token || !owned} onClick={manual}>Save connection</button>
        </div>
      )}
    </div>
  );
}

function Chat({ dirs, reload }: { dirs: Directive[]; reload: () => void }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [dirs.length]);

  const send = async () => {
    if (!text.trim()) return;
    setBusy(true);
    await api('/directives', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text, kind: 'goal' }) });
    setText(''); setBusy(false); reload();
  };

  return (
    <div className="chat card">
      <div className="msgs">
        {dirs.length === 0 && <div className="muted center pad">e.g. “Focus this week on the dVPN launch and post a fun TikTok explainer with upbeat music.”</div>}
        {dirs.map((d, i) => (
          <div key={i} className={`msg ${d.role}`}>
            <span className="who">{d.role === 'agent' ? '🐾 animal' : 'you'}</span>
            <span className="body">{d.text}</span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="composer">
        <textarea value={text} placeholder="Give the mascot a goal…" onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send(); }} />
        <button disabled={busy || !text.trim()} onClick={send}>Send</button>
      </div>
    </div>
  );
}

// ── mascot + chrome ───────────────────────────────────────────────────────────
function Mascot({ size = 64 }: { size?: number }) {
  return (
    <svg className="mascot" width={size} height={size} viewBox="0 0 100 100" fill="none" aria-hidden>
      <defs>
        <radialGradient id="mg" cx="50%" cy="38%" r="70%">
          <stop offset="0%" stopColor="#5cf3d6" /><stop offset="60%" stopColor="#38b6ff" /><stop offset="100%" stopColor="#7b6bff" />
        </radialGradient>
      </defs>
      {/* pointy cat ears with inner pink */}
      <path d="M22 36 L30 12 L46 30 Z" fill="url(#mg)" />
      <path d="M78 36 L70 12 L54 30 Z" fill="url(#mg)" />
      <path d="M28 30 L31 18 L39 28 Z" fill="#ff9ecb" opacity="0.7" />
      <path d="M72 30 L69 18 L61 28 Z" fill="#ff9ecb" opacity="0.7" />
      <circle cx="50" cy="56" r="30" fill="url(#mg)" />
      {/* almond cat eyes */}
      <ellipse className="eye" cx="40" cy="53" rx="4.5" ry="6" fill="#0a0b14" />
      <ellipse className="eye" cx="60" cy="53" rx="4.5" ry="6" fill="#0a0b14" />
      <circle cx="41.4" cy="50.6" r="1.5" fill="#fff" /><circle cx="61.4" cy="50.6" r="1.5" fill="#fff" />
      {/* nose + mouth */}
      <path d="M47.5 63 L52.5 63 L50 66 Z" fill="#ff9ecb" />
      <path d="M50 66 Q46 70 42 68 M50 66 Q54 70 58 68" stroke="#0a0b14" strokeWidth="2" strokeLinecap="round" fill="none" />
      {/* whiskers */}
      <g stroke="#0a0b14" strokeWidth="1.6" strokeLinecap="round" opacity="0.8">
        <path d="M34 62 L20 59 M34 66 L20 66 M66 62 L80 59 M66 66 L80 66" />
      </g>
    </svg>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="animal-root"><div className="stars" /><main className="wrap">{children}</main><Style /></div>;
}

function Style() {
  return (
    <style>{`
      .animal-root{--bg:#0a0b14;--panel:#141726;--panel2:#0f1220;--line:#252a41;--tx:#e8eaf2;--mut:#8b90a8;--cy:#38e1c6;--vi:#8b7bff;--ok:#3fdd91;--warn:#ffcf6b;--bad:#ff6b7d;
        min-height:100vh;background:radial-gradient(1200px 700px at 50% -10%,#1a2140 0%,var(--bg) 55%);color:var(--tx);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;position:relative;overflow-x:hidden}
      .stars{position:fixed;inset:0;background-image:radial-gradient(1px 1px at 20% 30%,#fff5,transparent),radial-gradient(1px 1px at 70% 60%,#fff3,transparent),radial-gradient(1px 1px at 40% 80%,#fff4,transparent),radial-gradient(1px 1px at 85% 20%,#fff3,transparent);opacity:.5;pointer-events:none}
      .wrap{max-width:1000px;margin:0 auto;padding:26px 20px 80px;position:relative}
      h1,h2{margin:0}
      h2{font-size:15px;letter-spacing:.04em;text-transform:uppercase;color:var(--tx);margin:26px 0 6px}
      section p.muted{margin:0 0 12px}
      .muted{color:var(--mut)} .xs{font-size:11px}.sm{font-size:13px}
      .center{text-align:center}.pad{padding:26px}
      .brand{font-weight:800;font-size:30px;letter-spacing:-.01em;background:linear-gradient(90deg,var(--cy),var(--vi));-webkit-background-clip:text;background-clip:text;color:transparent}
      .brand.sm{font-size:18px}
      .card{background:linear-gradient(180deg,#161a2c,#0f1220);border:1px solid var(--line);border-radius:16px}
      button{background:linear-gradient(90deg,var(--cy),#43c9ff);color:#04121a;border:0;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer;font-size:14px}
      button:disabled{opacity:.45;cursor:default}
      button.ghost{background:transparent;color:var(--tx);border:1px solid var(--line);font-weight:600}
      button.ghost.danger{color:var(--bad);border-color:#3a2130}
      button.sm{padding:7px 12px;font-size:13px}
      input,textarea{background:var(--panel2);border:1px solid var(--line);border-radius:10px;color:var(--tx);padding:10px 12px;font-size:14px;width:100%;font-family:inherit}
      input:focus,textarea:focus{outline:2px solid var(--cy);outline-offset:1px}
      .mascot{filter:drop-shadow(0 6px 20px #38e1c655);animation:float 5s ease-in-out infinite}
      .mascot .eye{animation:blink 5.5s infinite}
      @keyframes float{50%{transform:translateY(-6px)}}
      @keyframes blink{0%,92%,100%{transform:scaleY(1)}96%{transform:scaleY(.1)}}
      @media (prefers-reduced-motion:reduce){.mascot,.mascot .eye{animation:none}}
      /* login */
      .loginwrap{max-width:380px;margin:6vh auto 0;text-align:center;display:flex;flex-direction:column;align-items:center;gap:6px}
      .loginwrap.tight{margin:8px auto 0}
      .tagline{color:var(--mut);margin:0 0 14px;font-size:16px}
      .loglbl{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:2px}
      /* pitch */
      .pitch{max-width:760px;margin:6px auto 0;display:flex;flex-direction:column;gap:16px}
      .pitchgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
      @media (max-width:620px){.pitchgrid{grid-template-columns:1fr}}
      .feat{display:flex;gap:10px;align-items:flex-start;background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:12px 14px;font-size:13.5px;line-height:1.4}
      .fe{font-size:20px;flex:none}
      .pricecard{padding:20px;text-align:center;display:flex;flex-direction:column;gap:12px;align-items:center}
      .pricetop{display:flex;flex-direction:column;gap:2px}
      .priceamt{font-size:42px;font-weight:800;letter-spacing:-.02em;background:linear-gradient(90deg,var(--cy),var(--vi));-webkit-background-clip:text;background-clip:text;color:transparent}
      .permo{font-size:16px;color:var(--mut);-webkit-text-fill-color:var(--mut)}
      .subbtn{display:inline-block;background:linear-gradient(90deg,#ffc439,#f0a500);color:#0a0b14;text-decoration:none;font-weight:800;font-size:16px;padding:13px 34px;border-radius:12px;border:0;cursor:pointer;box-shadow:0 6px 24px #f0a50033}
      .subbtn:disabled{opacity:.5;cursor:default}
      .subpending{display:flex;flex-direction:column;gap:6px;align-items:center}
      .login{padding:20px;text-align:left;display:flex;flex-direction:column;gap:8px;width:100%}
      .login label{font-size:12px;color:var(--mut);margin-top:6px}
      .login button{margin-top:12px}
      .fineprint{font-size:11px;color:var(--mut);margin:6px 0 0;text-align:center}
      .error{color:var(--bad);font-size:13px;margin-top:8px}
      /* topbar */
      .topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
      .ident{display:flex;align-items:center;gap:12px}
      .controls{display:flex;align-items:center;gap:8px}
      .pill{font-size:12px;font-weight:700;padding:5px 11px;border-radius:999px;border:1px solid var(--line)}
      .pill.live{color:var(--bad);border-color:#3a2130;background:#2a1320}
      .pill.dry-run{color:var(--cy);border-color:#173a38;background:#0e2321}
      .pill.paused{color:var(--warn)}
      .pill.offline{color:var(--mut)}
      /* stats */
      .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0 4px}
      .statcard{background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
      .statn{font-size:24px;font-weight:800}.statl{font-size:12px;color:var(--mut)}
      /* connect grid */
      .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
      .conn{padding:16px}
      .conn.on{border-color:#1c5f4f;box-shadow:0 0 0 1px #1c5f4f55 inset}
      .connhead{display:flex;gap:12px;align-items:center}
      .emoji{font-size:26px}
      .clabel{font-weight:700}.music{color:var(--vi)}
      .cstatus{font-size:12px}.ok{color:var(--ok)}.warn{color:var(--warn)}
      .cnote{color:var(--mut);font-size:12px;margin:10px 0}
      .connact{display:flex;gap:8px;flex-wrap:wrap}
      .manual{margin-top:12px;display:flex;flex-direction:column;gap:8px;border-top:1px dashed var(--line);padding-top:12px}
      .hint{font-size:12px;color:var(--warn)}
      .chk{font-size:12px;color:var(--mut);display:flex;gap:8px;align-items:center}
      .chk input{width:auto}
      /* two-col */
      .two{display:grid;grid-template-columns:1.05fr .95fr;gap:20px}
      @media (max-width:820px){.two{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}
      /* chat */
      .chat{display:flex;flex-direction:column;height:420px;overflow:hidden}
      .msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
      .msg{display:flex;flex-direction:column;gap:2px;max-width:92%}
      .msg.operator{align-self:flex-end;align-items:flex-end}
      .msg .who{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
      .msg .body{background:var(--panel2);border:1px solid var(--line);padding:9px 12px;border-radius:12px;font-size:14px;line-height:1.45}
      .msg.operator .body{background:linear-gradient(90deg,#173a38,#122a3a);border-color:#1c5f4f}
      .msg.agent .body{background:#181433;border-color:#2b2456}
      .composer{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line)}
      .composer textarea{height:44px;resize:none}
      /* activity */
      .feed{display:flex;flex-direction:column;gap:2px;max-height:420px;overflow-y:auto}
      .postrow{display:flex;align-items:center;gap:10px;padding:9px 10px;border-bottom:1px solid #1a1e30;font-size:13px}
      .dot{width:8px;height:8px;border-radius:50%;background:var(--mut);flex:none}
      .dot.POSTED{background:var(--ok)}.dot.DRY_RUN{background:var(--cy)}.dot.FAILED{background:var(--bad)}.dot.QUEUED{background:var(--warn)}
      .pfx{font-weight:700;min-width:64px}
      .tag{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);border:1px solid var(--line);border-radius:6px;padding:1px 6px}
      .tag.POSTED{color:var(--ok)}.tag.DRY_RUN{color:var(--cy)}
      .cap{color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      /* live studio */
      .studio{padding:16px}
      .livehead{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px}
      .livemeta{font-size:13px;color:var(--mut)}
      .watchbtn{margin-left:auto;background:linear-gradient(90deg,#ff5b6e,#ff3d3d);color:#fff;text-decoration:none;padding:7px 14px;border-radius:10px;font-weight:700;font-size:13px}
      .steps{margin:6px 0 0;padding-left:22px;display:flex;flex-direction:column;gap:14px}
      .steps li{line-height:1.5}.steps li.done{opacity:.85}
      .stepnote{display:block;font-size:12px;color:var(--mut);margin-top:4px}
      .cmd{background:#05060c;border:1px solid var(--line);border-radius:8px;padding:9px 12px;margin:6px 0 0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;color:var(--cy);overflow-x:auto}
      code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#05060c;border:1px solid var(--line);border-radius:5px;padding:1px 5px;font-size:12px;color:var(--cy)}
      /* character studio */
      .flash{background:#0e2321;border:1px solid #173a38;color:var(--cy);border-radius:10px;padding:8px 12px;font-size:13px;margin-bottom:10px}
      .charwrap{display:grid;grid-template-columns:1.25fr .75fr;gap:16px}
      @media (max-width:820px){.charwrap{grid-template-columns:1fr}}
      .charmain,.charkb{padding:16px;display:flex;flex-direction:column;gap:10px}
      .chartop{display:flex;gap:14px;align-items:center}
      .spritepreview{width:72px;height:72px;object-fit:contain;background:#05060c;border:1px solid var(--line);border-radius:12px;image-rendering:auto}
      .charname{font-weight:800;font-size:17px}
      .charpers{margin-top:3px;line-height:1.4}
      .fieldlbl{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-top:4px}
      .promptrow{display:flex;gap:8px}
      .palette{display:flex;gap:12px;flex-wrap:wrap}
      .swatch{display:flex;flex-direction:column;align-items:center;gap:4px;font-size:11px;color:var(--mut)}
      .swatch input[type=color]{width:40px;height:34px;padding:0;border:1px solid var(--line);border-radius:8px;background:none;cursor:pointer}
      .sliders{display:flex;gap:18px;flex-wrap:wrap}
      .sliders label{font-size:12px;color:var(--mut);display:flex;flex-direction:column;gap:4px;flex:1;min-width:160px}
      .sliders b{color:var(--tx)}
      .sliders input[type=range]{width:100%;accent-color:var(--cy)}
      .charact{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
      .kbhead{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
      .kbact{display:flex;gap:8px;flex-wrap:wrap}
      .kbpaste{height:70px;resize:vertical}
      .kbsources{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
      .kbtag{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:6px;padding:2px 7px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    `}</style>
  );
}
