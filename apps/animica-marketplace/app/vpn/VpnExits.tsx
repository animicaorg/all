'use client';

import { useEffect, useState } from 'react';

// Live exit-location list for the /vpn landing. Fetches the PUBLIC locations endpoint at runtime
// (wallet-less, read-only) so the page build never couples to the VPN Prisma schema.

type Exit = {
  id: string;
  label?: string;
  region?: string;
  country?: string;
  city?: string;
  httpProxy?: string | null;
  load?: number | null;
  reputation?: number | null;
  online?: boolean;
};

function flag(cc?: string): string {
  if (!cc || !/^[a-zA-Z]{2}$/.test(cc)) return '🌐';
  const A = 0x1f1e6;
  return String.fromCodePoint(A + cc.toUpperCase().charCodeAt(0) - 65, A + cc.toUpperCase().charCodeAt(1) - 65);
}
function locLabel(x: Exit): string {
  return [x.city, x.country?.toUpperCase()].filter(Boolean).join(', ') || x.region || x.label || x.id;
}

export default function VpnExits() {
  const [state, setState] = useState<{ loading: boolean; exits: Exit[]; error: boolean }>({
    loading: true, exits: [], error: false,
  });

  useEffect(() => {
    let live = true;
    fetch('/api/mkt/v1/vpn/locations')
      .then((r) => r.json())
      .then((d) => {
        if (!live) return;
        const exits: Exit[] = (d.exits || d.results || []).slice().sort(
          (a: Exit, b: Exit) => (a.load ?? 1) - (b.load ?? 1),
        );
        setState({ loading: false, exits, error: false });
      })
      .catch(() => live && setState({ loading: false, exits: [], error: true }));
    return () => { live = false; };
  }, []);

  if (state.loading) return <p className="muted">Loading available exit locations…</p>;
  if (state.error) return <p className="muted">Could not reach the exit registry right now.</p>;
  if (!state.exits.length) {
    return (
      <div className="card">
        <p style={{ margin: 0 }}>
          No exits are online yet. <b>Be the first</b> — run{' '}
          <code className="inline">animica vpn exit serve --browser-proxy</code> and earn when settlement lands.
        </p>
      </div>
    );
  }

  return (
    <div className="grid">
      {state.exits.map((x) => {
        const meta = [
          x.region,
          x.reputation != null ? `rep ${Number(x.reputation).toFixed(1)}` : null,
          x.load != null ? `load ${Math.round(Number(x.load) * 100)}%` : null,
          x.httpProxy ? 'browser-proxy' : null,
        ].filter(Boolean).join(' · ');
        return (
          <div className="card" key={x.id}>
            <div className="top">
              <div className="ico" style={{ fontSize: 22 }}>{flag(x.country)}</div>
              <div>
                <h3 style={{ marginBottom: 2 }}>{locLabel(x)}</h3>
                <div className="muted" style={{ fontSize: 12.5 }}>{meta}</div>
              </div>
            </div>
            <p style={{ marginTop: 4, fontSize: 13 }}>
              {x.httpProxy
                ? <>Open the extension&apos;s <b>VPN</b> tab to route this browser here, or{' '}
                    <code className="inline">animica vpn up {x.id}</code> for the whole device.</>
                : <>Whole-device only: <code className="inline">animica vpn up {x.id}</code>.</>}
            </p>
          </div>
        );
      })}
    </div>
  );
}
