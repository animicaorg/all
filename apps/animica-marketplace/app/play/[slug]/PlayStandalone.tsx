'use client';
import { useEffect, useRef, useState } from 'react';
import GameLeaderboard from '@/components/GameLeaderboard';
import { useGameLeaderboard } from '@/lib/useGameLeaderboard';

// The interactive standalone play shell (rendered chrome-minimal by app/play/[slug]/page.tsx).
// Mirrors components/GamePlay.tsx's entitlement flow, but full-viewport for a browser tab / PWA
// launch instead of a panel on the detail page:
//   FREE  -> iframe src = the public, immutable content route (no sign-in needed).
//   PAID  -> auto-mint a short-lived, entitlement-gated play-token (checked at mint AND at serve)
//            and point the iframe at /api/mkt/v1/store/play/[token]; the public content CID route
//            is NEVER used for paid bytes. Owner/purchaser plays immediately; otherwise a
//            sign-in / "Buy to play" CTA back to the storefront.
// The iframe is `sandbox="allow-scripts"` (opaque origin) and the served bundle carries the same
// `sandbox allow-scripts ...` CSP as Forge's play sandbox — no network, storage, or wallet access.
type Phase = 'idle' | 'loading' | 'signin' | 'buy' | 'error';

export default function PlayStandalone({
  slug,
  bundleCid,
  name,
  isFree,
}: {
  slug: string;
  bundleCid: string;
  name: string;
  isFree: boolean;
}) {
  const [src, setSrc] = useState<string | null>(isFree ? `/api/mkt/v1/content/${bundleCid}` : null);
  const [phase, setPhase] = useState<Phase>(isFree ? 'idle' : 'loading');

  // Score capture + (cosmetic, player-reported) leaderboard. The hook listens for anm-game
  // postMessages from OUR opaque iframe, records a play, submits terminal scores, and reads the
  // board; it auto-opens `open` on game-over/win. The game→shell channel is one-way, read-only.
  const frameRef = useRef<HTMLIFrameElement>(null);
  const { board, open, setOpen } = useGameLeaderboard(slug, frameRef);
  const yourRank = board?.you?.rank ?? null;

  async function mint() {
    setPhase('loading');
    try {
      // same-origin: carries the anm_mkt_session cookie for the entitlement check.
      const r = await fetch(`/api/mkt/v1/store/play-token/${encodeURIComponent(slug)}`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (r.status === 401) return setPhase('signin');
      if (r.status === 403) return setPhase('buy');
      if (!r.ok) return setPhase('error');
      const data = await r.json();
      if (typeof data?.url === 'string') {
        setSrc(data.url);
        setPhase('idle');
      } else {
        setPhase('error');
      }
    } catch {
      setPhase('error');
    }
  }

  // Paid game: attempt entitlement on load so an owner/purchaser lands straight in the game.
  useEffect(() => {
    if (!isFree && !src) mint();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const storeHref = `/marketplace/apps/${slug}`;

  return (
    <main className="play-root">
      {src ? (
        <iframe
          ref={frameRef}
          className="play-frame"
          src={src}
          title={`Play ${name}`}
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          allow="autoplay; fullscreen; gamepad; accelerometer; gyroscope"
        />
      ) : (
        <div className="play-cover">
          <div className="play-card">
            <div className="play-dot" />
            <h1>{name}</h1>
            {phase === 'loading' && <p>Checking your library…</p>}
            {phase === 'signin' && (
              <>
                <p>Connect your Animica wallet to play this game.</p>
                <a className="btn primary" href="/my-ai">Connect wallet</a>
                <button className="btn ghost" onClick={mint}>I&apos;m signed in — retry</button>
              </>
            )}
            {phase === 'buy' && (
              <>
                <p>You don&apos;t own this game yet.</p>
                <a className="btn primary" href={storeHref}>Buy to play</a>
                <button className="btn ghost" onClick={mint}>Already bought? Retry</button>
              </>
            )}
            {phase === 'error' && (
              <>
                <p>Couldn&apos;t start the game.</p>
                <button className="btn primary" onClick={mint}>Try again</button>
              </>
            )}
          </div>
        </div>
      )}

      {/* Unobtrusive escape hatch back to the store listing (the only chrome). */}
      <a className="play-home" href={storeHref} aria-label="Back to Animica store" title="Back to Animica store">
        ‹ Animica
      </a>

      {/* Floating leaderboard — a corner toggle + a slide-in drawer (auto-opens on game-over/win).
          Only while the game is actually playing; cosmetic + player-reported. */}
      {src && (
        <>
          <button
            type="button"
            className="play-lb-btn"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-label="Leaderboard"
            title="Leaderboard"
          >
            🏆{yourRank != null ? <span className="play-lb-rank">#{yourRank}</span> : null}
          </button>

          {open && <div className="play-lb-scrim" onClick={() => setOpen(false)} />}
          <aside className={open ? 'play-lb-drawer open' : 'play-lb-drawer'} aria-hidden={!open}>
            <div className="play-lb-head">
              <span className="play-lb-title">🏆 Leaderboard</span>
              <button
                type="button"
                className="play-lb-close"
                onClick={() => setOpen(false)}
                aria-label="Close leaderboard"
              >
                ✕
              </button>
            </div>
            <div className="play-lb-body">
              <GameLeaderboard board={board} />
            </div>
          </aside>
        </>
      )}

      <style>{`
        .play-root{position:fixed;inset:0;height:100dvh;background:#05060a;overflow:hidden}
        .play-frame{position:absolute;inset:0;width:100%;height:100%;border:0;display:block;background:#05060a}
        .play-cover{position:absolute;inset:0;display:grid;place-items:center;padding:24px;
          background:radial-gradient(80% 60% at 50% 28%, var(--accent-glow), transparent 70%), var(--bg)}
        .play-card{width:100%;max-width:360px;text-align:center;display:flex;flex-direction:column;
          align-items:center;gap:12px;background:var(--bg-card);border:1px solid var(--border);
          border-radius:16px;padding:28px 24px}
        .play-card h1{font-size:22px;margin:2px 0;letter-spacing:-0.02em}
        .play-card p{color:var(--text-dim);font-size:14px;margin:0 0 4px;line-height:1.45}
        .play-card .btn{width:100%;justify-content:center}
        .play-dot{width:40px;height:40px;border-radius:12px;
          background:linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:0 0 22px var(--accent-glow)}
        .play-home{position:fixed;top:calc(env(safe-area-inset-top,0px) + 10px);left:10px;z-index:10;
          font-size:12px;font-weight:600;color:var(--text);background:rgba(13,15,22,0.55);
          border:1px solid var(--border-bright);border-radius:999px;padding:5px 11px;
          backdrop-filter:blur(8px);opacity:.35;transition:opacity .15s}
        .play-home:hover,.play-home:focus{opacity:1}

        .play-lb-btn{position:fixed;right:12px;bottom:calc(env(safe-area-inset-bottom,0px) + 12px);z-index:11;
          display:inline-flex;align-items:center;gap:6px;font-size:16px;line-height:1;color:var(--text);
          background:rgba(13,15,22,0.62);border:1px solid var(--border-bright);border-radius:999px;
          padding:9px 12px;cursor:pointer;backdrop-filter:blur(8px);opacity:.6;transition:opacity .15s}
        .play-lb-btn:hover,.play-lb-btn:focus{opacity:1}
        .play-lb-rank{font-size:12px;font-weight:700;color:#c8bfff}
        .play-lb-scrim{position:fixed;inset:0;z-index:12;background:rgba(3,4,7,0.45);backdrop-filter:blur(1px)}
        .play-lb-drawer{position:fixed;z-index:13;top:0;right:0;height:100dvh;width:min(360px,88vw);
          display:flex;flex-direction:column;background:var(--bg-card);border-left:1px solid var(--border);
          box-shadow:-14px 0 40px rgba(0,0,0,0.5);transform:translateX(100%);transition:transform .2s ease;
          padding:calc(env(safe-area-inset-top,0px) + 12px) 16px calc(env(safe-area-inset-bottom,0px) + 16px)}
        .play-lb-drawer.open{transform:translateX(0)}
        .play-lb-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
        .play-lb-title{font-weight:700;font-size:16px;letter-spacing:-0.01em}
        .play-lb-close{background:transparent;border:1px solid var(--border-bright);color:var(--text-dim);
          border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:13px;line-height:1}
        .play-lb-close:hover{color:var(--text);border-color:var(--accent)}
        .play-lb-body{overflow-y:auto}
        @media (max-width:560px){
          .play-lb-drawer{top:auto;bottom:0;right:0;left:0;width:auto;height:auto;max-height:72dvh;
            border-left:0;border-top:1px solid var(--border);border-radius:16px 16px 0 0;
            transform:translateY(100%);box-shadow:0 -14px 40px rgba(0,0,0,0.5)}
          .play-lb-drawer.open{transform:translateY(0)}
        }
      `}</style>
    </main>
  );
}
