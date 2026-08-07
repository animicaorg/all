'use client';

// The /cloud console connect gate — shown by server pages when there is no developer session.
// Same auth mechanics as app/dev/DevGate.tsx: a single-purpose `devportal` challenge signed by
// the window.animica provider mints an httpOnly session scoped to publish/withdraw/read.
// On success the page reloads so the SERVER re-renders with the session cookie present —
// every /cloud page reads its data server-side.

import { useCallback, useState } from 'react';
import { hasWallet, connectDevPortal } from '@/components/wallet';

export default function CloudGate({ next }: { next?: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const connect = useCallback(async () => {
    setErr('');
    setBusy(true);
    try {
      await connectDevPortal();
      window.location.href = next || window.location.pathname;
    } catch (e: any) {
      setErr(e?.message ?? 'connect failed');
      setBusy(false);
    }
  }, [next]);

  const installed = typeof window !== 'undefined' && hasWallet();

  return (
    <div style={{ maxWidth: 560, margin: '40px auto 60px', textAlign: 'center' }}>
      <div className="pill" style={{ marginBottom: 14 }}>🐍 Animica Python Cloud</div>
      <h1 style={{ fontSize: 32, letterSpacing: '-0.03em', margin: '0 0 10px' }}>
        Write Python. Deploy to Animica. Get paid when people use it.
      </h1>
      <p className="muted" style={{ fontSize: 15.5, lineHeight: 1.5 }}>
        Sign in with your Animica wallet to open the developer console: deploy functions, watch
        executions, manage agents and secrets, and withdraw your ANM earnings.
      </p>

      <div className="panel" style={{ marginTop: 22, textAlign: 'left' }}>
        {installed ? (
          <>
            <p className="muted" style={{ margin: '0 0 14px', fontSize: 14 }}>
              You&apos;ll be asked to sign a single-purpose <code className="inline">devportal</code> login
              message. Signing costs nothing and moves no funds — the session it mints can publish,
              withdraw and read, nothing else.
            </p>
            <button className="btn primary" onClick={connect} disabled={busy} style={{ minHeight: 42 }}>
              {busy ? 'Waiting for wallet…' : 'Connect wallet & sign in'}
            </button>
          </>
        ) : (
          <>
            <h3 style={{ margin: '0 0 8px', fontSize: 16 }}>No Animica wallet detected</h3>
            <p className="muted" style={{ margin: '0 0 10px', fontSize: 13.5, lineHeight: 1.55 }}>
              The console signs you in through the <code className="inline">window.animica</code> provider.
              Get it one of these ways, then reload this page:
            </p>
            <ul className="muted" style={{ margin: '0 0 14px', paddingLeft: 20, fontSize: 13.5, lineHeight: 1.7 }}>
              <li>Install the <a className="inline" href="/browser" style={{ textDecoration: 'underline' }}>Animica browser extension</a> (Chromium).</li>
              <li>Or open this page inside the <b>Animica mobile wallet</b>&apos;s built-in dapp browser.</li>
            </ul>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <a className="btn primary" href="/browser">Get the extension</a>
              <button className="btn" onClick={connect} disabled={busy}>I&apos;ve installed it — retry</button>
            </div>
          </>
        )}
        {err && <div style={{ color: 'var(--bad)', marginTop: 12, fontSize: 13 }}>{err}</div>}
      </div>

      <p className="muted" style={{ fontSize: 12.5, marginTop: 18 }}>
        Just browsing? See <a href="/pricing" style={{ textDecoration: 'underline' }}>plans &amp; pricing</a> —
        the Free tier deploys real functions with no card and no commitment.
      </p>
    </div>
  );
}
