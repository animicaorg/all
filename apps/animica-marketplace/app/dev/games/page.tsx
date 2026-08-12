'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, fmtAnm, StatusChip } from '../ui';

// Games — creator metrics for the publisher's Game Lab games: per-game plays, unique players and
// top score alongside the revenue the Earnings page tracks. Data: GET /api/mkt/v1/me/games.
//
// HONESTY: plays / players / top scores are CLIENT-REPORTED and UNTRUSTED. They are cosmetic /
// "for fun" and are NEVER tied to ANM payouts. Revenue (ANM) is the on-chain-settled number.

interface GameRow {
  slug: string;
  name: string;
  status: string;
  visibility: string;
  published: boolean;
  playable: boolean;
  plays: number;
  players: number;
  recentPlays: number;
  topScore: number | null;
  sales: number;
  earnedNanm: string;
}
interface Totals {
  games: number;
  plays: number;
  players: number;
  recentPlays: number;
  topScore: number;
  earnedNanm: string;
}

function compact(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1).replace(/\.0$/, '')}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(abs >= 10_000 ? 0 : 1).replace(/\.0$/, '')}k`;
  return String(Math.round(n));
}

export default function DevGames() {
  const [data, setData] = useState<{ games: GameRow[]; totals: Totals } | null>(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try {
      const d = await api('/api/mkt/v1/me/games');
      setData({ games: d.games ?? [], totals: d.totals ?? null });
    } catch (e: any) {
      setErr(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (err) return <div className="panel" style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)' }}>{err}</div>;
  if (!data) return <div className="empty">Loading game metrics…</div>;

  const { games, totals } = data;

  return (
    <div>
      <h1 style={{ fontSize: 28, letterSpacing: '-0.03em', margin: '0 0 4px' }}>Games</h1>
      <p className="muted" style={{ fontSize: 14, margin: '0 0 18px' }}>
        How your Game Lab games are performing — plays, players and top scores, alongside revenue.
        Play stats are player-reported and just for fun; ANM revenue is the on-chain number.
      </p>

      {games.length === 0 ? (
        <div className="empty">
          No games yet. Build one in the <a href="https://animica.io" className="mono">Game Lab</a> and publish it —
          it lists as a playable game here and in the wallet.
        </div>
      ) : (
        <>
          <div className="panel">
            <div className="kpi">
              <div className="k"><b>{totals.plays.toLocaleString()}</b><span>total plays</span></div>
              <div className="k"><b>{totals.recentPlays.toLocaleString()}</b><span>plays this week</span></div>
              <div className="k"><b>{totals.topScore > 0 ? compact(totals.topScore) : '—'}</b><span>best top score</span></div>
              <div className="k"><b style={{ color: 'var(--accent-2)' }}>{fmtAnm(totals.earnedNanm)} ANM</b><span>game revenue</span></div>
            </div>
          </div>

          <section className="section" style={{ paddingBottom: 8 }}>
            <h2>Per game</h2>
            <p className="sub">Plays and top scores are cosmetic / player-reported. Revenue is your 70% creator share, net of the store fee.</p>
            <div className="panel" style={{ padding: 0 }}>
              {games.map((g) => (
                <div key={g.slug} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '13px 18px', borderTop: '1px solid var(--border)', flexWrap: 'wrap' }}>
                  <div style={{ minWidth: 160, flex: '1 1 160px' }}>
                    <a href={`/dev/apps/${g.slug}`} style={{ fontWeight: 600, fontSize: 14 }}>{g.name}</a>
                    <div style={{ display: 'flex', gap: 8, marginTop: 4, alignItems: 'center', flexWrap: 'wrap' }}>
                      <StatusChip status={g.status} />
                      {!g.playable && <span className="pill" style={{ fontSize: 11, color: 'var(--text-faint)' }}>no bundle</span>}
                      {g.published && g.playable && (
                        <a href={`/play/${g.slug}`} target="_blank" rel="noreferrer" className="pill" style={{ fontSize: 11, color: 'var(--accent-2)' }}>▶ play</a>
                      )}
                    </div>
                  </div>
                  <Stat label="plays" value={g.plays.toLocaleString()} />
                  <Stat label="players" value={g.players.toLocaleString()} />
                  <Stat label="top score" value={g.topScore != null ? g.topScore.toLocaleString() : '—'} accent="var(--warn)" />
                  <Stat label="sales" value={String(g.sales)} />
                  <div style={{ marginLeft: 'auto', textAlign: 'right', minWidth: 96 }}>
                    <div style={{ fontWeight: 700, fontSize: 14 }}>{fmtAnm(g.earnedNanm)} <small className="muted">ANM</small></div>
                    <div className="muted" style={{ fontSize: 11 }}>revenue</div>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <p className="muted" style={{ fontSize: 12.5, maxWidth: 720 }}>
            Play counts, unique players and top scores are reported by the game running in a locked
            sandbox in the player&apos;s browser. They cannot be verified, so they are cosmetic — for-fun
            leaderboards only, never tied to ANM or any on-chain reward. Submissions are rate-limited;
            there is no anti-cheat beyond that.
          </p>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{ minWidth: 66, textAlign: 'right' }}>
      <div style={{ fontWeight: 600, fontSize: 14, color: accent }}>{value}</div>
      <div className="muted" style={{ fontSize: 11 }}>{label}</div>
    </div>
  );
}
