'use client';
import { useEffect, useState } from 'react';

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
      `}</style>
    </main>
  );
}
