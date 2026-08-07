'use client';
import { useState } from 'react';
import RunFunction from './RunFunction';

// The /functions "try it" panel: pick any public function from the directory and invoke it
// live against its real endpoint. Selection can be pre-seeded server-side via ?fn=owner/slug
// (the per-row "Try it" links), so it works as a plain link before any JS runs.

export interface TryFn {
  key: string; // "owner/slug"
  name: string;
  endpoint: string;
  requiresAuth: boolean;
  priced: boolean;
}

export default function TryFunctions({ functions, initialKey }: { functions: TryFn[]; initialKey?: string }) {
  const [key, setKey] = useState(() =>
    functions.some((f) => f.key === initialKey) ? (initialKey as string) : functions[0]?.key ?? '',
  );
  const fn = functions.find((f) => f.key === key) ?? null;
  if (!functions.length) return null;

  return (
    <div className="panel" id="try">
      <h2 style={{ margin: '0 0 4px', fontSize: 20 }}>Try a function live</h2>
      <p className="muted" style={{ margin: '0 0 14px', fontSize: 13.5 }}>
        Every call below hits the real execution endpoint and runs in the hardened sandbox.
      </p>
      <label style={{ display: 'grid', gap: 5, marginBottom: 12 }}>
        <span className="muted" style={{ fontSize: 12.5 }}>Function</span>
        <select
          value={key}
          onChange={(e) => setKey(e.target.value)}
          style={{
            width: '100%', background: 'var(--bg-elev)', border: '1px solid var(--border-bright)',
            borderRadius: 8, color: 'var(--text)', padding: '10px 12px', fontSize: 13.5, minHeight: 44,
          }}
        >
          {functions.map((f) => (
            <option key={f.key} value={f.key}>
              {f.key} — {f.name}
            </option>
          ))}
        </select>
      </label>
      {fn ? <RunFunction key={fn.key} endpoint={fn.endpoint} requiresAuth={fn.requiresAuth} priced={fn.priced} /> : null}
    </div>
  );
}
