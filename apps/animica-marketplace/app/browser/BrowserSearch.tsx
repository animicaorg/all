'use client';
import { useState } from 'react';

// The interactive .anm index demo. Seeded with server-rendered names so the page never
// flashes an empty index; re-queries the public API as the visitor types.
export default function BrowserSearch({ initial, total }: { initial: any[]; total: number }) {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState<any[]>(initial ?? []);
  const [count, setCount] = useState<number>(total ?? 0);

  async function run(query: string) {
    const d = await fetch(`/api/mkt/v1/names?search=${encodeURIComponent(query)}`)
      .then((r) => r.json())
      .catch(() => ({ results: [], total: 0 }));
    setRows(d.results ?? []);
    setCount(d.total ?? 0);
  }

  return (
    <div className="panel" style={{ marginTop: 34 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <b style={{ fontSize: 15 }}>Try the index</b>
        <span className="muted" style={{ fontSize: 12.5 }}>— {count} names online · this is exactly what the extension&apos;s popup &amp; omnibox show</span>
      </div>
      <form className="search" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); run(q); }}>
        <span style={{ opacity: 0.6 }}>🔎</span>
        <input value={q} onChange={(e) => { setQ(e.target.value); run(e.target.value); }} placeholder="Search .anm names…" />
      </form>
      <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
        {rows.length ? rows.slice(0, 8).map((x: any) => {
          let desc = x.kind; try { desc = JSON.parse(x.recordsJson).description || x.kind; } catch {}
          return (
            <a key={x.name} href={`/anm/${x.name}`} style={{ textDecoration: 'none', color: 'inherit', display: 'flex', alignItems: 'baseline', gap: 10, padding: '9px 12px', border: '1px solid var(--border)', borderRadius: 10, background: 'var(--bg-elev)' }}>
              <span className="mono" style={{ color: 'var(--accent)' }}>{x.name}.anm</span>
              <span className="badge type">{x.kind}</span>
              <span className="muted" style={{ fontSize: 13 }}>{desc}</span>
            </a>
          );
        }) : <div className="muted" style={{ fontSize: 14, padding: 8 }}>No names match — <a href="/names" style={{ color: 'var(--accent)' }}>register one</a>.</div>}
      </div>
    </div>
  );
}
