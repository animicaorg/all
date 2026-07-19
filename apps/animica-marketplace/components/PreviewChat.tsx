'use client';
import { useState } from 'react';

type Msg = { role: 'user' | 'assistant'; content: string };

export default function PreviewChat({ slug }: { slug: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function send() {
    const q = input.trim();
    if (!q || busy) return;
    setErr('');
    const next = [...msgs, { role: 'user' as const, content: q }];
    setMsgs(next);
    setInput('');
    setBusy(true);
    try {
      const res = await fetch(`/api/mkt/v1/ai/${slug}/preview`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ messages: next }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error?.message || 'preview failed');
      setMsgs((m) => [...m, { role: 'assistant', content: data.answer || '(no response)' }]);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 320, overflowY: 'auto', marginBottom: 12 }}>
        {msgs.length === 0 && <div className="muted" style={{ fontSize: 13.5 }}>Ask this AI a question to preview it…</div>}
        {msgs.map((m, i) => (
          <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '85%' }}>
            <div style={{
              background: m.role === 'user' ? 'linear-gradient(135deg, var(--accent), #7d6dff)' : 'var(--bg-elev)',
              border: m.role === 'user' ? 'none' : '1px solid var(--border)',
              color: m.role === 'user' ? '#fff' : 'var(--text)',
              padding: '9px 13px', borderRadius: 12, fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap',
            }}>{m.content}</div>
          </div>
        ))}
        {busy && <div className="muted" style={{ fontSize: 13 }}>thinking…</div>}
      </div>
      {err && <div style={{ color: 'var(--bad)', fontSize: 13, marginBottom: 8 }}>{err}</div>}
      <div className="search" style={{ maxWidth: '100%' }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          placeholder="Type a message…"
          disabled={busy}
        />
        <button className="btn primary" onClick={send} disabled={busy}>Send</button>
      </div>
    </div>
  );
}
