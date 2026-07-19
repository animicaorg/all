'use client';
import { useState } from 'react';

// Play surface for a Game Lab web game published as a DIGITAL_GOOD listing (Listing.bundleCid).
// The bundle is one self-contained, sandboxed HTML document — the same artifact Forge plays — so
// playing it is just an <iframe sandbox="allow-scripts">, no server compute.
//   FREE  -> load the public, immutable content route straight away (no sign-in needed).
//   PAID  -> a Play button that mints a short-lived, entitlement-gated play-token (checked at mint
//            AND at serve) and points the iframe at /api/mkt/v1/store/play/[token]; the public
//            content CID route is NEVER used for paid bytes (immutable/ungated = paywall leak).
// Additive: this renders inside a panel the detail page adds; it does not change any other surface.
export default function GamePlay({
  slug,
  bundleCid,
  isFree,
  name,
}: {
  slug: string;
  bundleCid: string;
  isFree: boolean;
  name: string;
}) {
  const [src, setSrc] = useState<string | null>(isFree ? `/api/mkt/v1/content/${bundleCid}` : null);
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(false);

  async function play() {
    setLoading(true);
    setNote('');
    try {
      // same-origin: carries the anm_mkt_session cookie for the entitlement check.
      const r = await fetch(`/api/mkt/v1/store/play-token/${encodeURIComponent(slug)}`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (r.status === 401) {
        setNote('Connect your wallet to play — sign in from the store menu, then press Play.');
        return;
      }
      if (r.status === 403) {
        setNote('Purchase required. Buy this game above, then press Play.');
        return;
      }
      if (!r.ok) {
        setNote('Could not start the game. Please try again.');
        return;
      }
      const data = await r.json();
      if (typeof data?.url === 'string') setSrc(data.url);
      else setNote('Could not start the game. Please try again.');
    } catch {
      setNote('Could not start the game. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div className="game-stage">
        {src ? (
          <iframe
            className="game-frame"
            src={src}
            title={`Play ${name}`}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            loading="lazy"
          />
        ) : (
          <div className="game-cover">
            <button className="btn primary" onClick={play} disabled={loading}>
              {loading ? 'Starting…' : '▶ Play'}
            </button>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
              Paid game · plays here once you own it.
            </div>
          </div>
        )}
      </div>
      {note ? (
        <div className="muted" style={{ fontSize: 12.5, marginTop: 10, textAlign: 'center' }}>
          {note}
        </div>
      ) : (
        <div className="muted" style={{ fontSize: 12, marginTop: 10, textAlign: 'center' }}>
          Runs in a locked sandbox — no network, no storage, no wallet access.
        </div>
      )}

      <style>{`
        .game-stage{position:relative;width:100%;aspect-ratio:16/10;border-radius:14px;overflow:hidden;
          border:1px solid var(--border);background:#05060a}
        .game-frame{position:absolute;inset:0;width:100%;height:100%;border:0;display:block;background:#05060a}
        .game-cover{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
          justify-content:center;gap:2px;padding:20px;text-align:center}
        @media (max-width:820px){.game-stage{aspect-ratio:4/3}}
      `}</style>
    </div>
  );
}
