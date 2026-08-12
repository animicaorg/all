'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import {
  api, uploadRaw, fmtBytes, fmtDate, shortHash, StatusChip, SENSITIVE_PERMISSIONS, inputStyle, labelStyle, rowLabel,
} from '../../../ui';

// Build upload — drag-drop a signed APK, POST it to the builds route, and render the server's
// apkVerify verdict (signature scheme + signer cert), the parsed permission list (sensitive
// ones flagged) and the resulting review status live. Data:
//   GET  /store/apps/[slug]/builds   -> { builds, packageName, pinnedCertSha256 }
//   POST /store/apps/[slug]/builds   -> raw APK body; { build, verdict } (201)

const REVIEW_NOTE: Record<string, { color: string; text: string }> = {
  APPROVED: { color: 'var(--good)', text: 'Approved — this build can go live. Publish the listing to make it installable.' },
  PENDING_REVIEW: { color: 'var(--warn)', text: 'Submitted for manual review. A listing’s first build, or one adding sensitive permissions, needs an admin approval.' },
  CHECKS_FAILED: { color: 'var(--bad)', text: 'Rejected at upload — the binary was discarded. Fix the issues below and re-upload.' },
  REJECTED: { color: 'var(--bad)', text: 'Rejected by review.' },
  DELISTED: { color: 'var(--text-faint)', text: 'Delisted.' },
  UPLOADED: { color: 'var(--accent-2)', text: 'Uploaded.' },
};

function PermList({ perms }: { perms: string[] }) {
  if (!perms?.length) return <span className="muted" style={{ fontSize: 13 }}>No permissions requested.</span>;
  const box: React.CSSProperties = { background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 8, padding: '2px 7px', fontSize: 11 };
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {perms.map((p) => {
        const s = SENSITIVE_PERMISSIONS.has(p);
        return (
          <span key={p} className="mono" style={{ ...box, color: s ? 'var(--bad)' : 'var(--text-dim)', borderColor: s ? 'rgba(255,92,114,0.35)' : 'var(--border)' }}
            title={s ? 'sensitive — triggers manual review' : undefined}>
            {s ? '⚠ ' : ''}{p.replace('android.permission.', '')}
          </span>
        );
      })}
    </div>
  );
}

function VerdictCard({ build, verdict, pinnedCert }: { build: any; verdict: any; pinnedCert: string | null }) {
  const note = REVIEW_NOTE[build.status] ?? { color: 'var(--text-dim)', text: build.status };
  const perms: string[] = Array.isArray(build.permissionsJson) ? build.permissionsJson : [];
  const certMismatch = !!pinnedCert && build.certSha256 && build.certSha256 !== pinnedCert;
  return (
    <div className="panel" style={{ borderColor: note.color }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <StatusChip status={build.status} />
        <b style={{ fontSize: 15 }}>v{build.versionName || '—'}</b>
        <span className="muted" style={{ fontSize: 12.5 }}>versionCode {build.versionCode} · {build.channel}</span>
      </div>
      <p style={{ color: note.color, fontSize: 13.5, margin: '10px 0 0' }}>{note.text}</p>

      {Array.isArray(verdict?.reasons) && verdict.reasons.length > 0 && (
        <ul style={{ color: 'var(--bad)', fontSize: 13, margin: '10px 0 0', paddingLeft: 18 }}>
          {verdict.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}
        </ul>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 14, fontSize: 13 }}>
        <div style={{ display: 'flex', gap: 8 }}><span style={rowLabel}>package</span><code className="inline">{build.packageName}</code></div>
        <div style={{ display: 'flex', gap: 8 }}><span style={rowLabel}>size · SDK</span><span className="muted">{fmtBytes(build.sizeBytes)} · min {build.minSdk} / target {build.targetSdk}</span></div>
        <div style={{ display: 'flex', gap: 8 }}><span style={rowLabel}>sha3-256</span><span className="mono" style={{ fontSize: 12, color: 'var(--text-dim)' }} title={build.sha3}>{shortHash(build.sha3, 28)}</span></div>
        <div style={{ display: 'flex', gap: 8 }}>
          <span style={rowLabel}>signer cert</span>
          <span className="mono" style={{ fontSize: 12, color: certMismatch ? 'var(--bad)' : 'var(--text-dim)' }} title={build.certSha256}>
            {shortHash(build.certSha256, 28)}{certMismatch ? ' — differs from pinned signer' : ''}
          </span>
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <div style={{ ...rowLabel, marginBottom: 6 }}>permissions ({perms.length})</div>
        <PermList perms={perms} />
      </div>

      {build.reviewNote && (
        <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--text-dim)' }}>
          <span style={rowLabel}>review note</span> {build.reviewNote}
        </div>
      )}
    </div>
  );
}

function Uploader({ slug, onUploaded }: { slug: string; onUploaded: (res: any) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [channel, setChannel] = useState('stable');
  const [notes, setNotes] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  function choose(f: File | undefined | null) {
    setErr('');
    if (!f) return;
    if (!/\.apk$/i.test(f.name)) { setErr('Please choose an .apk file.'); return; }
    setFile(f);
  }

  async function upload() {
    if (!file) return;
    setBusy(true); setErr('');
    try {
      const headers: Record<string, string> = { 'x-anm-channel': channel.trim() || 'stable' };
      if (notes.trim()) headers['x-anm-release-notes'] = encodeURIComponent(notes.trim());
      const res = await uploadRaw(`/api/mkt/v1/store/apps/${encodeURIComponent(slug)}/builds`, file, headers);
      setFile(null);
      setNotes('');
      onUploaded(res);
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  return (
    <section className="panel">
      <h3 style={{ margin: '0 0 4px', fontSize: 17 }}>Upload a release</h3>
      <p className="muted" style={{ fontSize: 13, margin: '0 0 14px' }}>
        A signed APK (v2/v3 scheme, release keystore — debug certs are rejected). Up to 512 MB. versionCode must exceed the last build on the channel; the signer cert is pinned to this listing on the first accepted build.
      </p>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); choose(e.dataTransfer.files?.[0]); }}
        onClick={() => ref.current?.click()}
        style={{
          border: `1.5px dashed ${dragOver ? 'var(--accent)' : 'var(--border-bright)'}`,
          background: dragOver ? 'rgba(108,92,255,0.06)' : 'var(--bg-elev)',
          borderRadius: 12, padding: '30px 20px', textAlign: 'center', cursor: 'pointer', transition: 'all .15s',
        }}
      >
        {file ? (
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{file.name}</div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>{fmtBytes(file.size)} · click to choose a different file</div>
          </div>
        ) : (
          <div>
            <div style={{ fontSize: 15 }}>Drag & drop an APK here</div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>or click to browse</div>
          </div>
        )}
      </div>
      <input ref={ref} type="file" accept=".apk,application/vnd.android.package-archive" onChange={(e) => choose(e.target.files?.[0])} style={{ display: 'none' }} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 14, marginTop: 14 }}>
        <div>
          <label style={labelStyle}>Channel</label>
          <input style={inputStyle} value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="stable" spellCheck={false} />
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>Release notes <span className="muted">(optional)</span></label>
          <textarea style={{ ...inputStyle, minHeight: 70, resize: 'vertical' }} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What changed in this version" />
        </div>
      </div>

      <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn primary" onClick={upload} disabled={!file || busy}>
          {busy ? 'Uploading & verifying…' : 'Upload & verify'}
        </button>
        {busy && <span className="muted" style={{ fontSize: 12.5 }}>Streaming the APK and running apksigner + aapt2 — large files take a moment.</span>}
        {err && <span style={{ color: 'var(--bad)', fontSize: 13 }}>{err}</span>}
      </div>
    </section>
  );
}

export default function BuildsPage() {
  const params = useParams();
  const slug = String(params.slug ?? '');
  const [data, setData] = useState<{ builds: any[]; packageName: string | null; pinnedCertSha256: string | null } | null>(null);
  const [err, setErr] = useState('');
  const [lastResult, setLastResult] = useState<any>(null);

  const load = useCallback(async () => {
    setErr('');
    try {
      const d = await api(`/api/mkt/v1/store/apps/${encodeURIComponent(slug)}/builds`);
      setData({ builds: d.builds ?? [], packageName: d.packageName ?? null, pinnedCertSha256: d.pinnedCertSha256 ?? null });
    } catch (e: any) { setErr(e.message); }
  }, [slug]);

  useEffect(() => { if (slug) load(); }, [slug, load]);

  return (
    <div>
      <a className="muted" href={`/dev/apps/${slug}`} style={{ fontSize: 13 }}>← {slug}</a>
      <h1 style={{ fontSize: 26, letterSpacing: '-0.03em', margin: '10px 0 4px' }}>Builds</h1>
      <p className="muted" style={{ fontSize: 14, margin: '0 0 20px' }}>
        {data?.packageName ? <>package <code className="inline">{data.packageName}</code></> : 'Package name is claimed at your first build.'}
        {data?.pinnedCertSha256 ? <> · signer pinned <span className="mono">{shortHash(data.pinnedCertSha256, 16)}</span></> : null}
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Uploader slug={slug} onUploaded={(res) => { setLastResult(res); load(); }} />

        {lastResult?.build && (
          <div>
            <h3 style={{ fontSize: 15, margin: '0 0 10px' }}>Verification result</h3>
            <VerdictCard build={lastResult.build} verdict={lastResult.verdict} pinnedCert={data?.pinnedCertSha256 ?? null} />
          </div>
        )}

        <section>
          <h3 style={{ fontSize: 15, margin: '4px 0 10px' }}>Release history</h3>
          {err && <div className="panel" style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)' }}>{err}</div>}
          {!data ? (
            <div className="empty">Loading…</div>
          ) : data.builds.length === 0 ? (
            <div className="empty">No builds yet — upload your first APK above.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {data.builds.map((b) => {
                const note = REVIEW_NOTE[b.status];
                const perms: string[] = Array.isArray(b.permissionsJson) ? b.permissionsJson : [];
                const sensitive = perms.filter((p) => SENSITIVE_PERMISSIONS.has(p));
                return (
                  <div key={b.id} className="panel" style={{ padding: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                      <StatusChip status={b.status} />
                      <b style={{ fontSize: 14.5 }}>v{b.versionName || '—'}</b>
                      <span className="muted" style={{ fontSize: 12.5 }}>versionCode {b.versionCode} · {b.channel}</span>
                      {sensitive.length > 0 && (
                        <span className="badge" style={{ background: 'rgba(255,92,114,0.14)', color: 'var(--bad)' }}>⚠ {sensitive.length} sensitive</span>
                      )}
                      <span className="muted" style={{ marginLeft: 'auto', fontSize: 12 }}>{fmtDate(b.createdAt)}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginTop: 8, fontSize: 12.5 }} className="muted">
                      <span>{fmtBytes(b.sizeBytes)}</span>
                      <span>min {b.minSdk} / target {b.targetSdk}</span>
                      <span className="mono" title={b.sha3}>sha3 {shortHash(b.sha3, 14)}</span>
                      <span className="mono" title={b.certSha256}>cert {shortHash(b.certSha256, 14)}</span>
                    </div>
                    {b.releaseNotes && <p className="muted" style={{ fontSize: 12.5, margin: '8px 0 0', whiteSpace: 'pre-wrap' }}>{b.releaseNotes}</p>}
                    {b.reviewNote && <div style={{ fontSize: 12.5, marginTop: 6, color: note?.color ?? 'var(--text-dim)' }}>{b.reviewNote}</div>}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
