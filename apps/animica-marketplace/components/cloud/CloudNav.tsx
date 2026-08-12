'use client';

// The /cloud console sub-navigation. Horizontal pill nav (scrolls on mobile — no wrap-induced
// tap-target squeeze), active state from the pathname, signed-in address + sign out on the right.

import { useCallback, useState } from 'react';
import { usePathname } from 'next/navigation';
import { shortAddr } from '@/app/dev/ui';

const NAV: { href: string; label: string }[] = [
  { href: '/cloud', label: 'Dashboard' },
  { href: '/cloud/functions', label: 'Functions' },
  { href: '/cloud/agents', label: 'Agents' },
  { href: '/cloud/secrets', label: 'Secrets' },
  { href: '/cloud/analytics', label: 'Analytics' },
  { href: '/cloud/earnings', label: 'Earnings' },
  { href: '/cloud/pricing', label: 'Pricing' },
  { href: '/cloud/settings', label: 'Settings' },
];

export default function CloudNav({ address }: { address: string | null }) {
  const pathname = usePathname() ?? '/cloud';
  const [busy, setBusy] = useState(false);

  const logout = useCallback(async () => {
    setBusy(true);
    await fetch('/api/mkt/v1/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
    window.location.href = '/cloud';
  }, []);

  return (
    <header style={{ borderBottom: '1px solid var(--border)', paddingBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <a href="/cloud" style={{ display: 'flex', alignItems: 'center', gap: 9, minHeight: 40 }}>
          <span style={{ width: 20, height: 20, borderRadius: 6, background: 'linear-gradient(135deg,var(--accent),var(--accent-2))', display: 'inline-block' }} />
          <b style={{ letterSpacing: '-0.02em', whiteSpace: 'nowrap' }}>Python Cloud</b>
          <span className="pill" style={{ fontSize: 10.5 }}>console</span>
        </a>
        <div style={{ flex: 1 }} />
        {address ? (
          <>
            <span className="mono muted" style={{ fontSize: 12.5 }} title={address}>{shortAddr(address)}</span>
            <button className="btn ghost" style={{ fontSize: 13, padding: '7px 12px' }} onClick={logout} disabled={busy}>
              {busy ? '…' : 'Sign out'}
            </button>
          </>
        ) : (
          <a className="btn ghost" style={{ fontSize: 13, padding: '7px 12px' }} href="/cloud">Sign in</a>
        )}
      </div>
      <nav className="cl-scroll" style={{ display: 'flex', gap: 6, marginTop: 12, overflowX: 'auto', paddingBottom: 2 }}>
        {NAV.map((n) => {
          const active = n.href === '/cloud' ? pathname === '/cloud' : pathname.startsWith(n.href);
          return (
            <a
              key={n.href}
              href={n.href}
              className="pill"
              style={{
                fontSize: 13, padding: '8px 14px', whiteSpace: 'nowrap', flexShrink: 0,
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
    </header>
  );
}
