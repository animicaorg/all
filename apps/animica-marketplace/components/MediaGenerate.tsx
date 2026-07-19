'use client';
import { useState } from 'react';

// Media generation panel for a MEDIA listing. Tries the entitled generate endpoint first
// (full-res, metered); falls back to the free preview when not entitled.
export default function MediaGenerate({ slug }: { slug: string }) {
  const [prompt, setPrompt] = useState('');
  const [img, setImg] = useState('');
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState('');
  const [err, setErr] = useState('');

  async function go() {
    const p = prompt.trim();
    if (!p || busy) return;
    setErr(''); setImg(''); setMeta(''); setBusy(true);
    try {
      // Try metered generate (works if logged in + entitled).
      let res = await fetch(`/api/mkt/v1/media/${slug}/generate`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ prompt: p }),
      });
      // Fall back to free preview on auth/entitlement failure.
      if (res.status === 401 || res.status === 402) {
        res = await fetch(`/api/mkt/v1/media/${slug}/preview`, {
          method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ prompt: p }),
        });
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error?.message || 'generation failed');
      setImg(`data:image/png;base64,${data.image.b64_json}`);
      setMeta(`${data.preview ? 'preview' : 'full-res'} · served by ${data.served_by ?? 'miner'}${data.model ? ' · ' + data.model.split('/').pop() : ''}`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="search" style={{ maxWidth: '100%', marginBottom: 12 }}>
        <input value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && go()} placeholder="Describe an image…" disabled={busy} />
        <button className="btn primary" onClick={go} disabled={busy}>{busy ? 'Generating…' : 'Generate'}</button>
      </div>
      {busy && <div className="muted" style={{ fontSize: 13 }}>Rendering on a miner… (CPU providers can take up to a minute)</div>}
      {err && <div style={{ color: 'var(--bad)', fontSize: 13 }}>{err}</div>}
      {img && (
        <figure style={{ margin: 0 }}>
          <img src={img} alt={prompt} style={{ width: '100%', maxWidth: 384, borderRadius: 12, border: '1px solid var(--border)' }} />
          <figcaption className="muted" style={{ fontSize: 12, marginTop: 6 }}>{meta}</figcaption>
        </figure>
      )}
    </div>
  );
}
