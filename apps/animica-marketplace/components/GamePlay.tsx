'use client';
import { useRef, useState } from 'react';
import GameLeaderboard from './GameLeaderboard';
import { useGameLeaderboard } from '@/lib/useGameLeaderboard';

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

  // Score capture + (cosmetic, player-reported) leaderboard. The hook listens for anm-game
  // postMessages from OUR iframe (opaque origin), records a play, submits terminal scores, and
  // reads the board. It auto-opens `open` on game-over/win. The game→shell channel is read-only.
  const frameRef = useRef<HTMLIFrameElement>(null);
  const { board, open, setOpen } = useGameLeaderboard(slug, frameRef);
  const topScore = board?.top?.[0]?.score ?? null;
  const plays = board?.plays ?? 0;

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
            ref={frameRef}
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

      {/* Collapsible leaderboard — auto-opens on game-over/win; cosmetic + player-reported. */}
      <div className="lb-block">
        <button
          type="button"
          className="lb-toggle"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          <span className="lb-toggle-title">🏆 Leaderboard</span>
          <span className="lb-toggle-meta">
            {topScore != null ? `Top ${topScore.toLocaleString()}` : ''}
            {topScore != null && plays > 0 ? ' · ' : ''}
            {plays > 0 ? `${plays.toLocaleString()} ${plays === 1 ? 'play' : 'plays'}` : ''}
          </span>
          <span className={open ? 'lb-chev open' : 'lb-chev'} aria-hidden>▾</span>
        </button>
        {open && (
          <div className="lb-panel">
            <GameLeaderboard board={board} />
          </div>
        )}
      </div>

      <style>{`
        .game-stage{position:relative;width:100%;aspect-ratio:16/10;border-radius:14px;overflow:hidden;
          border:1px solid var(--border);background:#05060a}
        .game-frame{position:absolute;inset:0;width:100%;height:100%;border:0;display:block;background:#05060a}
        .game-cover{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
          justify-content:center;gap:2px;padding:20px;text-align:center}
        @media (max-width:820px){.game-stage{aspect-ratio:4/3}}
        .lb-block{margin-top:14px;border:1px solid var(--border);border-radius:12px;background:var(--bg-elev);overflow:hidden}
        .lb-toggle{width:100%;display:flex;align-items:center;gap:10px;padding:11px 14px;background:transparent;
          border:0;cursor:pointer;color:var(--text);font-weight:600;font-size:14px;text-align:left}
        .lb-toggle:hover{background:rgba(108,92,255,0.06)}
        .lb-toggle-title{flex:0 0 auto}
        .lb-toggle-meta{flex:1;min-width:0;color:var(--text-faint);font-weight:500;font-size:12.5px;
          overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .lb-chev{flex:0 0 auto;color:var(--text-faint);transition:transform .15s}
        .lb-chev.open{transform:rotate(180deg)}
        .lb-panel{padding:4px 14px 14px;border-top:1px solid var(--border)}
      `}</style>
    </div>
  );
}
