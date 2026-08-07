'use client';
import { useState } from 'react';
import { fmtAnm } from './fmt';

// The live "run it" box. POSTs a JSON payload to the REAL public execution endpoint
// (/api/cloud/v1/fn/{owner}/{slug}) and shows the result, the request id and the metered cost
// from the X-Animica-* headers. Free public functions run anonymously on the shared free tier;
// priced or auth-required functions take an API key (kept in memory only, sent as a Bearer
// header — never stored).

export interface RunFunctionProps {
  endpoint: string; // site-relative, e.g. /api/cloud/v1/fn/alice/summarize
  requiresAuth: boolean;
  priced: boolean; // per-call surcharge or non-free app pricing => a funded identity is needed
  buttonLabel?: string;
  samplePayload?: string;
}

interface RunResult {
  ok: boolean;
  httpStatus: number;
  requestId: string | null;
  costNanm: string | null;
  body: string;
}

export default function RunFunction({ endpoint, requiresAuth, priced, buttonLabel, samplePayload }: RunFunctionProps) {
  const [payload, setPayload] = useState(samplePayload ?? '{}');
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<RunResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const needsKey = requiresAuth || priced;

  async function run() {
    setErr(null);
    setRes(null);
    let body: string;
    try {
      body = JSON.stringify(payload.trim() ? JSON.parse(payload) : {});
    } catch {
      setErr('The payload must be valid JSON.');
      return;
    }
    if (needsKey && !apiKey.trim()) {
      setErr('This function requires an API key (it is priced or auth-required).');
      return;
    }
    setBusy(true);
    try {
      const r = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          ...(apiKey.trim() ? { authorization: `Bearer ${apiKey.trim()}` } : {}),
        },
        body,
      });
      const text = await r.text();
      let pretty = text;
      try {
        pretty = JSON.stringify(JSON.parse(text), null, 2);
      } catch {
        /* leave as-is */
      }
      setRes({
        ok: r.ok,
        httpStatus: r.status,
        requestId: r.headers.get('x-animica-request-id'),
        costNanm: r.headers.get('x-animica-cost-nanm'),
        body: pretty,
      });
    } catch (e: any) {
      setErr(e?.message || 'network error');
    } finally {
      setBusy(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', background: 'var(--bg-elev)', border: '1px solid var(--border-bright)',
    borderRadius: 8, color: 'var(--text)', padding: '10px 12px', fontSize: 13.5, outline: 'none',
  };

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="mono muted" style={{ fontSize: 12, wordBreak: 'break-all' }}>POST {endpoint}</div>
      <label style={{ display: 'grid', gap: 5 }}>
        <span className="muted" style={{ fontSize: 12.5 }}>JSON payload (becomes the function&apos;s <code className="inline">request</code>)</span>
        <textarea
          value={payload}
          onChange={(e) => setPayload(e.target.value)}
          rows={4}
          spellCheck={false}
          style={{ ...inputStyle, fontFamily: 'var(--mono)', fontSize: 12.5, resize: 'vertical', minHeight: 88 }}
        />
      </label>
      {needsKey ? (
        <label style={{ display: 'grid', gap: 5 }}>
          <span className="muted" style={{ fontSize: 12.5 }}>API key (required — {requiresAuth ? 'this endpoint requires auth' : 'this function is priced'}; used once, never stored)</span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="anm_mkt_…"
            autoComplete="off"
            style={inputStyle}
          />
        </label>
      ) : (
        <div className="muted" style={{ fontSize: 12.5 }}>
          Runs anonymously on the shared free tier (rate-limited). Add an API key from your account for higher limits.
        </div>
      )}
      <div>
        <button className="btn primary" style={{ minHeight: 44, minWidth: 120 }} onClick={run} disabled={busy}>
          {busy ? 'Running…' : buttonLabel || 'Run'}
        </button>
      </div>
      {err ? <div style={{ color: 'var(--bad)', fontSize: 13 }}>{err}</div> : null}
      {res ? (
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', fontSize: 12.5 }}>
            <span className="pill" style={{ color: res.ok ? 'var(--good)' : 'var(--bad)', borderColor: res.ok ? 'var(--good)' : 'var(--bad)' }}>
              HTTP {res.httpStatus}
            </span>
            {res.costNanm != null ? <span className="pill">cost {fmtAnm(res.costNanm)} ANM</span> : null}
            {res.requestId ? <span className="pill mono" style={{ fontSize: 11 }}>req {res.requestId.slice(0, 18)}…</span> : null}
          </div>
          <pre className="codebox" style={{ maxHeight: 320, overflow: 'auto' }}>{res.body}</pre>
        </div>
      ) : null}
    </div>
  );
}
