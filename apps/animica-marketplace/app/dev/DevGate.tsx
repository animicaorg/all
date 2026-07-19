'use client';
import { useCallback, useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { hasWallet, connectDevPortal } from '@/components/wallet';
import { api, shortAddr, DevSessionProvider, type DevSession } from './ui';

// The Developer Center auth gate. It wraps every /dev page: probe the session, and if it is not
// a devportal (publish-scoped) session, render a connect-wallet gate instead of the children so
// no unauthenticated request is ever fired.
//
// Login is the purpose=devportal v2 challenge flow (components/wallet.ts connectDevPortal):
//   requestAccounts -> GET challenge?purpose=devportal -> sign the single-purpose string ->
//   POST verify -> httpOnly session scoped to ['publish','withdraw','read']. The probe uses
//   GET /store/apps/mine, which requires the 'publish' scope, so a buyer 'store' session (which
//   lacks publish) is correctly treated as "not signed in as a developer".

type State = 'loading' | 'in' | 'out';

const NAV: { href: string; label: string }[] = [
  { href: '/dev', label: 'My apps' },
  { href: '/dev/earnings', label: 'Earnings' },
  { href: '/dev/keys', label: 'API keys' },
];

export default function DevGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [state, setState] = useState<State>('loading');
  const [address, setAddress] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const probe = useCallback(async () => {
    try {
      const d = await api('/api/mkt/v1/store/apps/mine');
      setAddress(d.address ?? null);
      setState('in');
    } catch (e: any) {
      if (e?.status === 401 || e?.status === 403) setState('out');
      else { setErr(e?.message ?? 'failed to load'); setState('out'); }
    }
  }, []);

  useEffect(() => { probe(); }, [probe]);

  const connect = useCallback(async () => {
    setErr(''); setBusy(true);
    try {
      const { address } = await connectDevPortal();
      setAddress(address);
      setState('in');
    } catch (e: any) {
      setErr(e?.message ?? 'connect failed');
    } finally { setBusy(false); }
  }, []);

  const logout = useCallback(async () => {
    await fetch('/api/mkt/v1/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
    setAddress(null);
    setState('out');
  }, []);

  const session: DevSession = { address, reload: probe, logout };

  if (state === 'loading') {
    return (
      <div className="wrap" style={{ paddingTop: 60 }}>
        <div className="empty">Loading the Developer Center…</div>
      </div>
    );
  }

  if (state === 'out') {
    const installed = hasWallet();
    return (
      <div className="wrap" style={{ paddingTop: 50, paddingBottom: 60 }}>
        <div style={{ maxWidth: 560, margin: '0 auto', textAlign: 'center' }}>
          <div className="pill" style={{ marginBottom: 14 }}>🛠 Developer Center</div>
          <h1 style={{ fontSize: 34, letterSpacing: '-0.03em', margin: '0 0 10px' }}>Publish to the Animica App Store</h1>
          <p className="muted" style={{ fontSize: 15.5, lineHeight: 1.5 }}>
            Sign in with your Animica wallet to manage listings, upload signed APK builds, set prices in ANM,
            track earnings, and mint API keys. Payments settle in ANM on the Animica chain.
          </p>

          <div className="panel" style={{ marginTop: 22, textAlign: 'left' }}>
            {installed ? (
              <>
                <p className="muted" style={{ margin: '0 0 14px', fontSize: 14 }}>
                  You&apos;ll be asked to sign a single-purpose <code className="inline">devportal</code> login
                  message. This session can publish, withdraw and read — nothing else.
                </p>
                <button className="btn primary" onClick={connect} disabled={busy}>
                  {busy ? 'Waiting for wallet…' : 'Connect wallet & sign in'}
                </button>
              </>
            ) : (
              <>
                <h3 style={{ margin: '0 0 8px', fontSize: 16 }}>No Animica wallet detected</h3>
                <p className="muted" style={{ margin: '0 0 10px', fontSize: 13.5, lineHeight: 1.55 }}>
                  The Developer Center signs you in through the <code className="inline">window.animica</code> provider.
                  Get it one of these ways, then reload this page:
                </p>
                <ul className="muted" style={{ margin: '0 0 14px', paddingLeft: 20, fontSize: 13.5, lineHeight: 1.7 }}>
                  <li>Install the <a className="inline" href="/browser">Animica browser extension</a> (Chromium).</li>
                  <li>Or open this page inside the <b>Animica mobile wallet</b>&apos;s built-in dapp browser.</li>
                </ul>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <a className="btn primary" href="/browser">Get the extension</a>
                  <button className="btn" onClick={probe}>I&apos;ve installed it — retry</button>
                </div>
              </>
            )}
            {err && <div style={{ color: 'var(--bad)', marginTop: 12, fontSize: 13 }}>{err}</div>}
          </div>

          <p className="muted" style={{ fontSize: 12.5, marginTop: 18 }}>
            Prefer automation? Any <code className="inline">anm_mkt_</code> API key with the{' '}
            <code className="inline">publish</code> scope drives the same{' '}
            <a className="inline" href="/docs">store API</a> — mint one under <b>API keys</b> once signed in.
          </p>
        </div>
      </div>
    );
  }

  // signed in
  return (
    <DevSessionProvider value={session}>
      <div className="wrap" style={{ paddingTop: 26, paddingBottom: 70 }}>
        <header
          style={{
            display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
            paddingBottom: 14, borderBottom: '1px solid var(--border)',
          }}
        >
          <a href="/dev" style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span className="dot" style={{ width: 20, height: 20, borderRadius: 6, background: 'linear-gradient(135deg,var(--accent),var(--accent-2))' }} />
            <b style={{ letterSpacing: '-0.02em' }}>Developer Center</b>
          </a>
          <nav style={{ display: 'flex', gap: 6, marginLeft: 8 }}>
            {NAV.map((n) => {
              const active = n.href === '/dev' ? pathname === '/dev' : pathname.startsWith(n.href);
              return (
                <a
                  key={n.href}
                  href={n.href}
                  className="pill"
                  style={{
                    fontSize: 13, padding: '6px 12px',
                    color: active ? 'var(--text)' : 'var(--text-dim)',
                    borderColor: active ? 'var(--accent)' : 'var(--border)',
                    background: active ? 'rgba(108,92,255,0.08)' : 'transparent',
                  }}
                >
                  {n.label}
                </a>
              );
            })}
          </nav>
          <div style={{ flex: 1 }} />
          <span className="mono muted" style={{ fontSize: 12.5 }} title={address ?? ''}>{shortAddr(address)}</span>
          <button className="btn ghost" style={{ fontSize: 13, padding: '7px 12px' }} onClick={logout}>Sign out</button>
        </header>

        <div style={{ marginTop: 22 }}>{children}</div>
      </div>
    </DevSessionProvider>
  );
}
