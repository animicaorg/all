'use client';

// /cloud/secrets client: create + delete. A secret's value is shown NOWHERE after creation —
// not in this UI, not in any API response, not in logs (the sandbox redacts it).

import { useCallback, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { fmtDate } from '@/app/dev/ui';
import {
  api, type CloudApiError, ApiErrBox, OkBox, ConfirmDialog, timeAgo, inputStyle, labelStyle,
} from '@/components/cloud/ui';

export interface SecretsDto {
  plan: { key: string; maxSecrets: number; used: number };
  maxSecretBytes: number;
  secrets: {
    id: string; name: string; hint: string; functionId: string | null;
    lastUsedAt: string | null; createdAt: string;
    function: { slug: string } | null;
  }[];
  functions: { id: string; slug: string }[];
}

const NAME_RE = /^[A-Z][A-Z0-9_]{0,63}$/;

export default function SecretsClient({ dto }: { dto: SecretsDto }) {
  const router = useRouter();
  const [apiError, setApiError] = useState<CloudApiError | null>(null);
  const [notice, setNotice] = useState('');
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [scope, setScope] = useState(''); // '' => account-wide
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<SecretsDto['secrets'][number] | null>(null);
  const [reveal, setReveal] = useState(false);

  const nameOk = NAME_RE.test(name);
  const valueBytes = useMemo(() => new TextEncoder().encode(value).length, [value]);
  const valueOk = valueBytes > 0 && valueBytes <= dto.maxSecretBytes;
  const atCap = dto.plan.maxSecrets !== -1 && dto.plan.used >= dto.plan.maxSecrets;

  const create = useCallback(async () => {
    setApiError(null);
    setBusy(true);
    try {
      await api('/api/cloud/v1/secrets', {
        method: 'POST',
        body: JSON.stringify({ name, value, functionId: scope || null }),
      });
      setNotice(`Secret ${name} stored. Its value is now sealed — it will never be shown again, here or anywhere else. To change it, delete and re-create.`);
      setName('');
      setValue('');
      setScope('');
      router.refresh();
    } catch (e) {
      setApiError(e as CloudApiError);
    } finally {
      setBusy(false);
    }
  }, [name, value, scope, router]);

  const doDelete = useCallback(async () => {
    if (!deleting) return;
    setApiError(null);
    setBusy(true);
    try {
      await api(`/api/cloud/v1/secrets/${encodeURIComponent(deleting.id)}`, { method: 'DELETE' });
      setNotice(`Secret ${deleting.name} deleted. Executions that referenced it will no longer receive it.`);
      setDeleting(null);
      router.refresh();
    } catch (e) {
      setApiError(e as CloudApiError);
      setDeleting(null);
    } finally {
      setBusy(false);
    }
  }, [deleting, router]);

  return (
    <div>
      <h1 style={{ margin: 0, fontSize: 26, letterSpacing: '-0.03em' }}>Secrets</h1>
      <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
        Encrypted at rest (AES-256-GCM), injected as environment values into authorized executions,
        redacted from logs. Values are write-only: after creation nobody — including you — can read
        one back.{dto.plan.maxSecrets === -1 ? '' : ` ${dto.plan.used} of ${dto.plan.maxSecrets} used on the ${dto.plan.key} plan.`}
      </p>

      {notice && <OkBox>{notice}</OkBox>}
      <ApiErrBox error={apiError} />

      <div className="cl-grid2" style={{ marginTop: 18 }}>
        {/* create */}
        <div className="panel">
          <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>New secret</h3>
          {atCap ? (
            <p className="muted" style={{ fontSize: 13.5 }}>
              Your {dto.plan.key} plan allows {dto.plan.maxSecrets} secrets and all slots are used.{' '}
              <a href="/pricing" style={{ textDecoration: 'underline' }}>Upgrade →</a>
            </p>
          ) : (
            <>
              <label style={labelStyle}>Name (ENV_STYLE)</label>
              <input
                style={{ ...inputStyle, fontFamily: 'var(--mono)', borderColor: name && !nameOk ? 'var(--bad)' : undefined }}
                value={name}
                onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_'))}
                placeholder="OPENWEATHER_API_KEY"
                spellCheck={false}
                autoComplete="off"
              />
              {name && !nameOk && <div style={{ color: 'var(--bad)', fontSize: 11.5, marginTop: 4 }}>must start with a letter: A–Z, 0–9, _ only</div>}

              <label style={{ ...labelStyle, marginTop: 12 }}>Value</label>
              <div style={{ position: 'relative' }}>
                <textarea
                  className="cl-input"
                  rows={3}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  spellCheck={false}
                  autoComplete="off"
                  style={reveal ? undefined : ({ WebkitTextSecurity: 'disc' } as React.CSSProperties)}
                />
                <button type="button" className="btn ghost" onClick={() => setReveal((v) => !v)}
                  style={{ position: 'absolute', top: 6, right: 6, fontSize: 11.5, padding: '4px 8px', minHeight: 26 }}>
                  {reveal ? 'hide' : 'show'}
                </button>
              </div>
              <div className="muted" style={{ fontSize: 11.5, marginTop: 4, color: valueBytes > dto.maxSecretBytes ? 'var(--bad)' : undefined }}>
                {valueBytes.toLocaleString()} / {dto.maxSecretBytes.toLocaleString()} bytes
              </div>

              <label style={{ ...labelStyle, marginTop: 12 }}>Scope</label>
              <select className="cl-input" value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value="">All my functions</option>
                {dto.functions.map((f) => (
                  <option key={f.id} value={f.id}>only {f.slug}</option>
                ))}
              </select>

              <button className="btn primary" style={{ marginTop: 14 }} onClick={create} disabled={busy || !nameOk || !valueOk}>
                {busy ? 'Sealing…' : '🔒 Store secret'}
              </button>
              <p className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
                The value is sealed server-side and never returned by any API afterwards.
              </p>
            </>
          )}
        </div>

        {/* list */}
        <div className="panel">
          <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>Stored secrets ({dto.secrets.length})</h3>
          {dto.secrets.length === 0 ? (
            <div className="empty" style={{ padding: '26px 12px', fontSize: 13 }}>
              No secrets yet. Store API keys and tokens here instead of hardcoding them in source —
              deployed source is content-addressed and anchored, so anything in it is public forever.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {dto.secrets.map((s) => (
                <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 10, flexWrap: 'wrap' }}>
                  <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>🔒 {s.name}</span>
                  <span className="mono muted" style={{ fontSize: 11.5 }}>…{s.hint}</span>
                  <span className="pill" style={{ fontSize: 10.5 }}>{s.function ? `fn: ${s.function.slug}` : 'all functions'}</span>
                  <span style={{ flex: 1 }} />
                  <span className="muted" style={{ fontSize: 11.5 }} title={fmtDate(s.createdAt)}>
                    {s.lastUsedAt ? `used ${timeAgo(s.lastUsedAt)}` : 'never used'}
                  </span>
                  <button className="btn ghost" style={{ fontSize: 12, padding: '5px 10px', minHeight: 30, color: 'var(--bad)' }}
                    onClick={() => setDeleting(s)} disabled={busy}>
                    Delete
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={deleting != null}
        title={`Delete secret ${deleting?.name}?`}
        danger
        busy={busy}
        confirmLabel="Delete secret"
        body={<>Executions that reference <code className="inline">{deleting?.name}</code> will stop receiving it on their next run. The sealed value is destroyed — there is no undo.</>}
        onConfirm={doDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
