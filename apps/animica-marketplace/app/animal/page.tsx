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
        <p className="tagline">The autonomous ambassador console</p>
        <form onSubmit={submit} className="card login">
          <label>Operator</label>
          <input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" />
          <label>Password</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus />
          {err && <div className="error">{err}</div>}
          <button disabled={busy || !password} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
          <p className="fineprint">Password attempts are rate-limited. Access is for the mascot operator only.</p>
        </form>
      </div>
    </Shell>
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
    </Shell>
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
      .tagline{color:var(--mut);margin:0 0 14px}
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
    `}</style>
  );
}
