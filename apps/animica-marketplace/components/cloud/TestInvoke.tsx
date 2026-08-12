'use client';

// Test-invoke panel: fires a REAL request at the function's public endpoint
// (POST /api/cloud/v1/fn/{owner}/{slug}) and shows the response body plus the execution
// receipt headers (request id, charged cost, status). Used by the editor page and the
// function detail page. Every invocation here is a genuine billed/free-tier execution —
// the panel says so instead of pretending to be a dry run.

import { useCallback, useMemo, useState } from 'react';
import { fmtAnm } from '@/app/dev/ui';
import { CopyButton, ErrBox } from './ui';

export default function TestInvoke({
  ownerSegment,
  slug,
  displayBase,
}: {
  ownerSegment: string;
  slug: string;
  /** Absolute base for DISPLAY (e.g. https://animica.dev); requests go same-origin relative. */
  displayBase: string;
}) {
  const [payload, setPayload] = useState('{\n  "n": 10\n}');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [out, setOut] = useState<{
    status: number;
    body: string;
    requestId: string | null;
    costNanm: string | null;
    execStatus: string | null;
    ms: number;
  } | null>(null);

  const path = `/api/cloud/v1/fn/${encodeURIComponent(ownerSegment)}/${encodeURIComponent(slug)}`;
  const url = `${displayBase}${path}`;

  const payloadValid = useMemo(() => {
    try {
      JSON.parse(payload || '{}');
      return true;
    } catch {
      return false;
    }
  }, [payload]);

  const run = useCallback(async () => {
    setErr('');
    setBusy(true);
    setOut(null);
    const started = Date.now();
    try {
      const body = payload.trim() ? JSON.stringify(JSON.parse(payload)) : '{}';
      const r = await fetch(path, {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body,
      });
      const text = await r.text();
      let pretty = text;
      try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch {}
      setOut({
        status: r.status,
        body: pretty,
        requestId: r.headers.get('x-animica-request-id'),
        costNanm: r.headers.get('x-animica-cost-nanm'),
        execStatus: r.headers.get('x-animica-status'),
        ms: Date.now() - started,
      });
    } catch (e: any) {
      setErr(e?.message ?? 'request failed');
    } finally {
      setBusy(false);
    }
  }, [path, payload]);

  const curl = `curl -s -X POST ${url} \\\n  -H 'content-type: application/json' \\\n  -d '${payload.replace(/\n\s*/g, ' ').replace(/'/g, "'\\''")}'`;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <code className="inline" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{url}</code>
        <CopyButton text={url} label="Copy URL" small />
        <CopyButton text={curl} label="Copy curl" small />
      </div>

      <label style={{ display: 'block', fontSize: 12.5, color: 'var(--text-dim)', margin: '12px 0 5px' }}>
        Request JSON (becomes the function&apos;s <code className="inline">request</code> argument)
      </label>
      <textarea
        className="cl-input"
        rows={4}
        value={payload}
        onChange={(e) => setPayload(e.target.value)}
        spellCheck={false}
        style={{ borderColor: payloadValid ? undefined : 'var(--bad)' }}
      />
      {!payloadValid && <div style={{ color: 'var(--bad)', fontSize: 12, marginTop: 4 }}>not valid JSON</div>}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
        <button className="btn primary" onClick={run} disabled={busy || !payloadValid}>
          {busy ? 'Running…' : '▶ Invoke'}
        </button>
        <span className="muted" style={{ fontSize: 12 }}>
          Runs the real endpoint — the execution is metered exactly like a caller&apos;s.
        </span>
      </div>

      {err && <ErrBox>{err}</ErrBox>}

      {out && (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12.5, alignItems: 'center' }}>
            <span className="pill" style={{
              color: out.status < 400 ? 'var(--good)' : 'var(--bad)',
              borderColor: out.status < 400 ? 'var(--good)' : 'var(--bad)', fontWeight: 700,
            }}>
              HTTP {out.status}
            </span>
            {out.execStatus && <span className="muted">status: <b style={{ color: 'var(--text)' }}>{out.execStatus}</b></span>}
            <span className="muted">{out.ms}ms round-trip</span>
            {out.costNanm != null && (
              <span className="muted">charged: <b style={{ color: 'var(--text)' }}>{fmtAnm(out.costNanm)} ANM</b></span>
            )}
            {out.requestId && <span className="mono muted" style={{ fontSize: 11.5 }}>req {out.requestId}</span>}
          </div>
          <pre className="cl-code" style={{ marginTop: 8, maxHeight: 320, overflow: 'auto' }}>{out.body || '(empty body)'}</pre>
        </div>
      )}
    </div>
  );
}
