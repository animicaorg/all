'use client';
import type { Leaderboard } from '@/lib/gameClient';

// Presentational leaderboard body shared by both play surfaces (the standalone /play overlay and
// the in-page GamePlay collapsible). Pure render from a Leaderboard (or null while loading); the
// open/close chrome lives in each surface. Scores are player-reported — the footnote says so.
export default function GameLeaderboard({ board }: { board: Leaderboard | null }) {
  const top = board?.top ?? [];
  const you = board?.you ?? null;
  const plays = board?.plays ?? 0;
  const players = board?.players ?? 0;
  // Show the standalone "your best" row only when the player has a score that isn't already the
  // highlighted row in the visible top list (avoids showing it twice).
  const youInTop = top.some((e) => e.isYou);

  return (
    <div className="lb">
      {(plays > 0 || players > 0) && (
        <div className="lb-counts">
          <span>▶ {plays.toLocaleString()} {plays === 1 ? 'play' : 'plays'}</span>
          {players > 0 && <span>· {players.toLocaleString()} {players === 1 ? 'player' : 'players'}</span>}
        </div>
      )}

      {board === null ? (
        <div className="lb-note">Loading scores…</div>
      ) : top.length === 0 ? (
        <div className="lb-note">No scores yet — be the first!</div>
      ) : (
        <ol className="lb-list">
          {top.map((e) => (
            <li key={`${e.rank}-${e.name}`} className={e.isYou ? 'lb-row you' : 'lb-row'}>
              <span className="lb-rank">{e.rank}</span>
              <span className="lb-name">{e.name}{e.isYou ? ' · you' : ''}</span>
              <span className="lb-score">{e.score.toLocaleString()}</span>
            </li>
          ))}
        </ol>
      )}

      {you && you.best != null && !youInTop && (
        <div className="lb-you">
          <span className="lb-rank">{you.rank ?? '—'}</span>
          <span className="lb-name">Your best</span>
          <span className="lb-score">{you.best.toLocaleString()}</span>
        </div>
      )}

      <div className="lb-foot">Player-reported — just for fun, not tied to ANM.</div>

      <style>{`
        .lb{display:flex;flex-direction:column;gap:8px}
        .lb-counts{display:flex;gap:6px;flex-wrap:wrap;font-size:12px;color:var(--text-faint)}
        .lb-note{font-size:13px;color:var(--text-dim);padding:6px 0}
        .lb-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
        .lb-row,.lb-you{display:grid;grid-template-columns:26px 1fr auto;align-items:center;gap:10px;
          padding:6px 8px;border-radius:8px;font-size:13.5px}
        .lb-row:nth-child(odd){background:var(--bg-elev)}
        .lb-row.you{background:rgba(108,92,255,0.14);outline:1px solid var(--border-bright)}
        .lb-you{margin-top:4px;background:rgba(108,92,255,0.14);outline:1px solid var(--border-bright)}
        .lb-rank{font-variant-numeric:tabular-nums;color:var(--text-faint);font-weight:600;text-align:right}
        .lb-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text)}
        .lb-score{font-variant-numeric:tabular-nums;font-weight:700;color:var(--text)}
        .lb-you .lb-name,.lb-row.you .lb-name{color:#c8bfff}
        .lb-foot{font-size:11px;color:var(--text-faint);margin-top:2px;line-height:1.4}
      `}</style>
    </div>
  );
}
