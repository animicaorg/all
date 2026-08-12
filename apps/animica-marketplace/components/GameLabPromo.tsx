// Featured "Animica Game Lab" entry on the storefront — the create-a-game studio
// (hosted in Forge at animica.io/game-lab). Per the marketplace spec, Game Lab is
// a first-class FREE app on the store, not only the thing that publishes games
// into it. Additive, self-contained; links out to the studio.
const GAME_LAB_URL = process.env.NEXT_PUBLIC_GAME_LAB_URL || 'https://animica.io/game-lab';

export default function GameLabPromo({ compact = false }: { compact?: boolean }) {
  return (
    <a
      href={GAME_LAB_URL}
      className="gamelab-promo"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        flexWrap: 'wrap',
        textDecoration: 'none',
        color: 'var(--text)',
        background:
          'linear-gradient(135deg, rgba(108,92,255,0.14), rgba(20,224,200,0.10)), var(--bg-card)',
        border: '1px solid var(--border-bright)',
        borderRadius: 'var(--radius)',
        padding: compact ? '16px 18px' : '20px 22px',
      }}
    >
      <div
        aria-hidden
        style={{
          width: compact ? 44 : 54,
          height: compact ? 44 : 54,
          borderRadius: 14,
          display: 'grid',
          placeItems: 'center',
          fontSize: compact ? 24 : 30,
          flex: '0 0 auto',
          background: 'linear-gradient(135deg, var(--accent), var(--accent-2))',
          boxShadow: '0 0 22px var(--accent-glow)',
        }}
      >
        🎮
      </div>
      <div style={{ flex: '1 1 260px', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h3 style={{ margin: 0, fontSize: compact ? 16 : 18, letterSpacing: '-0.01em' }}>
            Animica Game Lab
          </h3>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: '0.04em',
              padding: '2px 8px',
              borderRadius: 999,
              color: 'var(--accent-2)',
              border: '1px solid var(--border-bright)',
            }}
          >
            FREE
          </span>
        </div>
        <p style={{ margin: '5px 0 0', color: 'var(--text-dim)', fontSize: 13.6, lineHeight: 1.45 }}>
          Describe a game in a sentence and get a playable game in seconds — then publish it here and
          earn ANM. No code, no install.
        </p>
      </div>
      <span
        className="btn primary"
        style={{ flex: '0 0 auto', pointerEvents: 'none' }}
      >
        Open Game Lab →
      </span>
    </a>
  );
}
