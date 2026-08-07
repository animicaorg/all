'use client';

// The /cloud/functions/new editor client: write -> validate (real findings, inline at their
// line numbers) -> estimate (real pricing policy + anchor readiness) -> deploy -> live
// deployment status from the actual CloudDeployStatus -> endpoint + test invoke.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fmtAnm, anmToNanm, nanmToAnmInput } from '@/app/dev/ui';
import PyEditor, { type PyEditorHandle, type EditorFinding } from '@/components/cloud/PyEditor';
import TestInvoke from '@/components/cloud/TestInvoke';
import {
  api, type CloudApiError, ApiErrBox, ErrBox, OkBox, CopyButton, CloudStatusPill,
  CAPABILITY_INFO, inputStyle, labelStyle,
} from '@/components/cloud/ui';

export interface EditorBootstrap {
  ownerSegment: string;
  publicBase: string;
  plan: {
    key: string;
    maxFunctions: number;
    functionsUsed: number;
    privateDeployments: boolean;
    marketplacePublishing: boolean;
    feeBps: number | null; // founding-dev override, null => policy rate
  };
  limits: {
    maxSourceBytes: number;
    minTimeoutMs: number;
    maxTimeoutMs: number;
    minMemoryMb: number;
    maxMemoryMb: number;
  };
  anchorConfirmations: number;
  upgradeHint: { needed: number; developerAllows: number; proAllows: number } | null;
  prefill: {
    mode: 'create' | 'redeploy';
    functionId: string | null;
    name: string;
    slug: string;
    description: string;
    entrypoint: string;
    source: string;
    timeoutMs: number;
    memoryMb: number;
    capabilities: string[];
    visibility: string;
    requiresAuth: boolean;
    perCallNanm: string;
    loadedVersion: number | null;
  };
}

interface ValidationDto {
  ok: boolean;
  findings: EditorFinding[];
  functions: string[];
  imports: string[];
  capabilities: string[];
}

interface EstimateDto {
  perCallTypicalNanm: string;
  perCallMaxNanm: string;
  feeBps: number;
  policyVersion: number;
  anchor: {
    enabled: boolean;
    walletAddress: string | null;
    walletBalanceNanm: string | null;
    willBroadcast: boolean;
    reason: string | null;
  };
}

interface DeploymentDto {
  id: string;
  status: string;
  endpoint: string | null;
  anchorTxid: string | null;
  anchorHeight: number | null;
  anchorConfirms: number;
  daBlobId: string | null;
  error: string | null;
  logs: { ts?: string; level?: string; message: string }[];
}

const STAGES = ['VALIDATING', 'BUILDING', 'AWAITING_SIGNATURE', 'BROADCASTING', 'CONFIRMING', 'ACTIVE'];

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48);
}

function parseLogs(raw: unknown): DeploymentDto['logs'] {
  if (Array.isArray(raw)) return raw as DeploymentDto['logs'];
  if (typeof raw === 'string') {
    try {
      const p = JSON.parse(raw);
      return Array.isArray(p) ? p : [];
    } catch { return []; }
  }
  return [];
}

function normalizeDeployment(j: any): DeploymentDto | null {
  const d = j?.deployment ?? j;
  if (!d?.id && !d?.deploymentId) return null;
  return {
    id: d.id ?? d.deploymentId,
    status: d.status ?? 'DRAFT',
    endpoint: d.endpoint ?? null,
    anchorTxid: d.anchorTxid ?? null,
    anchorHeight: d.anchorHeight ?? null,
    anchorConfirms: Number(d.anchorConfirms ?? 0),
    daBlobId: d.daBlobId ?? null,
    error: d.error ?? null,
    logs: parseLogs(d.logsJson ?? d.logs),
  };
}

function unanchoredReason(logs: DeploymentDto['logs']): string | null {
  for (let i = logs.length - 1; i >= 0; i--) {
    const m = logs[i]?.message ?? '';
    if (/anchor|DA put|unanchored/i.test(m) && (logs[i].level === 'warn' || logs[i].level === 'error')) return m;
  }
  return null;
}

function devShareNanm(priceNanm: string, feeBps: number): string {
  try {
    return ((BigInt(priceNanm) * BigInt(10_000 - feeBps)) / 10_000n).toString();
  } catch { return '0'; }
}

export default function NewFunctionClient({ boot }: { boot: EditorBootstrap }) {
  const p = boot.prefill;
  const isRedeploy = p.mode === 'redeploy';

  // form state
  const [name, setName] = useState(p.name);
  const [slug, setSlug] = useState(p.slug);
  const [slugTouched, setSlugTouched] = useState(isRedeploy);
  const [description, setDescription] = useState(p.description);
  const [entrypoint, setEntrypoint] = useState(p.entrypoint);
  const [source, setSource] = useState(p.source);
  const [timeoutS, setTimeoutS] = useState(Math.round(p.timeoutMs / 1000));
  const [memoryMb, setMemoryMb] = useState(p.memoryMb);
  const [caps, setCaps] = useState<string[]>(p.capabilities);
  const [visibility, setVisibility] = useState(p.visibility);
  const [requiresAuth, setRequiresAuth] = useState(p.requiresAuth);
  const [priceAnm, setPriceAnm] = useState(p.perCallNanm === '0' ? '' : nanmToAnmInput(p.perCallNanm));

  // flow state
  const [validation, setValidation] = useState<ValidationDto | null>(null);
  const [validating, setValidating] = useState(false);
  // One-shot handoff from the chat on animica.dev: "Deploy this" stashes the
  // code it produced and sends you here. Consumed once so a refresh doesn't
  // silently overwrite what you have since typed, and only when we are creating
  // (never when redeploying an existing function).
  useEffect(() => {
    if (isRedeploy) return;
    try {
      const raw = sessionStorage.getItem('animica.cloud.draft');
      if (!raw) return;
      sessionStorage.removeItem('animica.cloud.draft');
      const draft = JSON.parse(raw) as { source?: string; name?: string; description?: string };
      if (draft.source && draft.source.trim()) setSource(draft.source);
      if (draft.name && !p.name) setName(draft.name);
      if (draft.description && !p.description) setDescription(draft.description);
    } catch {
      /* a malformed draft must never block the editor */
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const [estimateRes, setEstimateRes] = useState<EstimateDto | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [apiError, setApiError] = useState<CloudApiError | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [deployElapsed, setDeployElapsed] = useState(0);
  const [deployment, setDeployment] = useState<DeploymentDto | null>(null);
  const [deployedFunctionId, setDeployedFunctionId] = useState<string | null>(p.functionId);
  const [deployedVersion, setDeployedVersion] = useState<number | null>(null);
  const [sourceDirtySince, setSourceDirtySince] = useState(false);

  const editorRef = useRef<PyEditorHandle | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const sourceBytes = useMemo(() => new TextEncoder().encode(source).length, [source]);
  const overLimit = sourceBytes > boot.limits.maxSourceBytes;
  const slugValue = slugTouched ? slug : slugify(name);
  const slugOk = /^[a-z0-9][a-z0-9-]{0,62}$/.test(slugValue);
  const entryOk = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(entrypoint);
  let priceNanm = '0';
  let priceOk = true;
  try {
    priceNanm = priceAnm.trim() ? anmToNanm(priceAnm.trim()) : '0';
  } catch { priceOk = false; }

  const findings = validation?.findings ?? [];
  const errorCount = findings.filter((f) => f.severity === 'error').length;
  const atFunctionCap = !isRedeploy && boot.upgradeHint != null;

  // ── validate ───────────────────────────────────────────────────────────────
  const validate = useCallback(async (): Promise<ValidationDto | null> => {
    setApiError(null);
    setValidating(true);
    try {
      const j = await api('/api/cloud/v1/validate', {
        method: 'POST',
        body: JSON.stringify({ source, entrypoint: entrypoint || 'main' }),
      });
      const report: ValidationDto = j?.findings !== undefined ? j : j?.report ?? j;
      setValidation(report);
      setSourceDirtySince(false);
      return report;
    } catch (e) {
      setApiError(e as CloudApiError);
      return null;
    } finally {
      setValidating(false);
    }
  }, [source, entrypoint]);

  // ── estimate ───────────────────────────────────────────────────────────────
  const estimate = useCallback(async () => {
    setApiError(null);
    setEstimating(true);
    try {
      const j = await api('/api/cloud/v1/estimate', {
        method: 'POST',
        body: JSON.stringify({
          timeoutMs: timeoutS * 1000,
          memoryMb,
          surchargeNanm: priceNanm,
          ...(isRedeploy && p.functionId ? { functionId: p.functionId } : {}),
        }),
      });
      setEstimateRes((j?.estimate ?? j) as EstimateDto);
    } catch (e) {
      setApiError(e as CloudApiError);
    } finally {
      setEstimating(false);
    }
  }, [timeoutS, memoryMb, priceNanm, isRedeploy, p.functionId]);

  // ── deploy ─────────────────────────────────────────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);
  useEffect(() => stopPolling, [stopPolling]);

  const pollDeployment = useCallback((id: string) => {
    stopPolling();
    let polls = 0;
    pollRef.current = setInterval(async () => {
      polls++;
      if (polls > 40) return stopPolling();
      try {
        const j = await api(`/api/cloud/v1/deployments/${encodeURIComponent(id)}`);
        const d = normalizeDeployment(j);
        if (d) {
          setDeployment(d);
          const settled =
            d.status === 'FAILED' ||
            (d.status === 'ACTIVE' && (!d.anchorTxid || d.anchorConfirms >= boot.anchorConfirmations));
          if (settled) stopPolling();
        }
      } catch {
        // transient poll failure — keep trying inside the budget
      }
    }, 4000);
  }, [stopPolling, boot.anchorConfirmations]);

  const deploy = useCallback(async () => {
    setApiError(null);
    setDeployment(null);
    setDeployedVersion(null);

    // Always validate right before deploying — the server re-validates authoritatively, but
    // surfacing findings at their lines here beats a 422 round-trip.
    const report = await validate();
    if (!report) return;
    if (!report.ok) return;

    setDeploying(true);
    const started = Date.now();
    const tick = setInterval(() => setDeployElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    try {
      // Once the function exists (redeploy mode, or a create that already succeeded in this
      // session), further deploys append a new immutable version instead of re-creating the slug.
      const existingId = (isRedeploy && p.functionId) || deployedFunctionId;
      const j = existingId
        ? await api(`/api/cloud/v1/functions/${encodeURIComponent(existingId)}/versions`, {
            method: 'POST',
            body: JSON.stringify({ source, entrypoint }),
          })
        : await api('/api/cloud/v1/functions', {
            method: 'POST',
            body: JSON.stringify({
              slug: slugValue,
              name: name.trim() || slugValue,
              description,
              entrypoint,
              source,
              timeoutMs: timeoutS * 1000,
              memoryMb,
              capabilities: caps,
              visibility,
              requiresAuth,
              perCallNanm: priceNanm,
            }),
          });

      const fnId = j?.functionId ?? j?.function?.id ?? existingId ?? null;
      setDeployedFunctionId(fnId);
      setDeployedVersion(typeof j?.version === 'number' ? j.version : j?.version?.version ?? null);
      const depId = j?.deploymentId ?? j?.deployment?.id ?? null;

      if (depId) {
        // First render from the response, then track the REAL status by polling the row.
        const seeded = normalizeDeployment(j.deployment ?? { ...j, id: depId });
        if (seeded) setDeployment(seeded);
        try {
          const live = normalizeDeployment(await api(`/api/cloud/v1/deployments/${encodeURIComponent(depId)}`));
          if (live) setDeployment(live);
          if (live && (live.status !== 'ACTIVE' || (live.anchorTxid && live.anchorConfirms < boot.anchorConfirmations))) {
            pollDeployment(depId);
          }
        } catch {
          // The deployment read endpoint is unavailable — keep the response snapshot visible.
        }
      }
    } catch (e) {
      const err = e as CloudApiError;
      if (err.details?.findings) {
        setValidation((v) => ({
          ok: false,
          findings: err.details.findings,
          functions: v?.functions ?? [],
          imports: v?.imports ?? [],
          capabilities: v?.capabilities ?? [],
        }));
      }
      setApiError(err);
    } finally {
      clearInterval(tick);
      setDeployElapsed(0);
      setDeploying(false);
    }
  }, [validate, isRedeploy, p.functionId, deployedFunctionId, source, entrypoint, slugValue, name, description,
      timeoutS, memoryMb, caps, visibility, requiresAuth, priceNanm, pollDeployment, boot.anchorConfirmations]);

  const toggleCap = (key: string) =>
    setCaps((c) => (c.includes(key) ? c.filter((k) => k !== key) : [...c, key]));

  const suggestedCaps = (validation?.capabilities ?? []).filter((c) => !caps.includes(c));
  const endpointUrl = `${boot.publicBase}/api/cloud/v1/fn/${boot.ownerSegment}/${slugValue || '<slug>'}`;
  const deployBlocked =
    deploying || overLimit || !slugOk || !entryOk || !priceOk || atFunctionCap ||
    (validation !== null && !sourceDirtySince && errorCount > 0);

  const stageIdx = deployment ? STAGES.indexOf(deployment.status) : -1;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26, letterSpacing: '-0.03em' }}>
            {isRedeploy ? `Redeploy ${boot.ownerSegment}/${p.slug}` : 'New function'}
          </h1>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
            {isRedeploy
              ? <>Editing {p.loadedVersion != null ? `version ${p.loadedVersion}` : 'the current version'} — deploying creates an immutable new version.</>
              : <>Write Python, validate, deploy. Deployments are anchored on-chain (source hash + artifact hash + DA blob id inside a signed DEPLOY tx) and executed off-chain in a hardened container.</>}
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <a className="btn ghost" href={isRedeploy && p.functionId ? `/cloud/functions/${p.functionId}` : '/cloud/functions'}>← Back</a>
      </div>

      {atFunctionCap && boot.upgradeHint && (
        <ErrBox>
          Your <b style={{ textTransform: 'capitalize' }}>{boot.plan.key}</b> plan allows{' '}
          {boot.plan.maxFunctions} function{boot.plan.maxFunctions === 1 ? '' : 's'} and you already have{' '}
          {boot.plan.functionsUsed}. Developer allows {boot.upgradeHint.developerAllows}, Pro allows{' '}
          {boot.upgradeHint.proAllows}.{' '}
          <a href="/pricing" style={{ textDecoration: 'underline' }}>View plans →</a>
        </ErrBox>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: 16, marginTop: 18 }} className="nf-cols">
        <style>{`@media (max-width: 980px){ .nf-cols { grid-template-columns: 1fr !important; } }`}</style>

        {/* ── left: editor ─────────────────────────────────────────────── */}
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
            <button className="btn" onClick={validate} disabled={validating || deploying}>
              {validating ? 'Validating…' : '✓ Validate'}
            </button>
            <button className="btn" onClick={estimate} disabled={estimating || deploying}>
              {estimating ? 'Estimating…' : '≈ Estimate cost'}
            </button>
            <span style={{ flex: 1 }} />
            <span className="muted" style={{ fontSize: 12, color: overLimit ? 'var(--bad)' : undefined }}>
              {sourceBytes.toLocaleString()} / {boot.limits.maxSourceBytes.toLocaleString()} bytes
            </span>
          </div>

          <PyEditor
            ref={editorRef}
            value={source}
            onChange={(v) => { setSource(v); setSourceDirtySince(true); }}
            findings={findings}
            onSave={validate}
            height={460}
          />

          {/* findings */}
          {validation && (
            <div style={{ marginTop: 10 }}>
              {validation.ok && findings.length === 0 && (
                <OkBox>
                  Validation passed.
                  {validation.functions.length > 0 && <> Entrypoint candidates: <code className="inline">{validation.functions.join(', ')}</code>.</>}
                  {validation.imports.length > 0 && <> Imports: <code className="inline">{validation.imports.join(', ')}</code>.</>}
                </OkBox>
              )}
              {findings.length > 0 && (
                <div className="panel" style={{ padding: 12, marginTop: 8 }}>
                  <div style={{ fontSize: 12.5, marginBottom: 6 }}>
                    <b style={{ color: errorCount ? 'var(--bad)' : 'var(--warn)' }}>
                      {errorCount ? `${errorCount} error${errorCount === 1 ? '' : 's'}` : `${findings.length} warning${findings.length === 1 ? '' : 's'}`}
                    </b>
                    {errorCount > 0 && <span className="muted"> — fix these before deploying</span>}
                    {sourceDirtySince && <span className="muted"> · source changed since this run — revalidate</span>}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {findings.map((f, i) => (
                      <button
                        key={i}
                        onClick={() => f.line > 0 && editorRef.current?.gotoLine(f.line)}
                        style={{
                          display: 'flex', gap: 8, alignItems: 'baseline', textAlign: 'left', background: 'none',
                          border: 0, cursor: f.line > 0 ? 'pointer' : 'default', padding: '3px 0', fontSize: 12.5, minHeight: 24,
                        }}
                      >
                        <span style={{ color: f.severity === 'error' ? 'var(--bad)' : 'var(--warn)', fontWeight: 700, flexShrink: 0 }}>
                          {f.severity === 'error' ? '✕' : '⚠'} {f.line > 0 ? `L${f.line}` : 'file'}
                        </span>
                        <span style={{ color: 'var(--text-dim)' }}>{f.message} <span className="mono" style={{ color: 'var(--text-faint)', fontSize: 11 }}>[{f.code}]</span></span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {suggestedCaps.length > 0 && (
                <div className="panel" style={{ padding: 12, marginTop: 8, fontSize: 12.5 }}>
                  The validator saw calls that need capabilities you haven&apos;t declared:{' '}
                  {suggestedCaps.map((c) => (
                    <button key={c} className="chip" style={{ marginRight: 6 }} onClick={() => toggleCap(c)}>+ {c}</button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* estimate */}
          {estimateRes && (
            <div className="panel" style={{ padding: 14, marginTop: 10 }}>
              <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Cost estimate (pricing policy v{estimateRes.policyVersion})</h3>
              <div className="cl-kpis" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
                <div><b style={{ fontSize: 16 }}>{fmtAnm(estimateRes.perCallTypicalNanm)} ANM</b><div className="muted" style={{ fontSize: 11.5 }}>typical price / call</div></div>
                <div><b style={{ fontSize: 16 }}>{fmtAnm(estimateRes.perCallMaxNanm)} ANM</b><div className="muted" style={{ fontSize: 11.5 }}>worst case / call</div></div>
                <div><b style={{ fontSize: 16 }}>{fmtAnm(devShareNanm(estimateRes.perCallTypicalNanm, estimateRes.feeBps))} ANM</b><div className="muted" style={{ fontSize: 11.5 }}>your share (fee {estimateRes.feeBps / 100}%)</div></div>
              </div>
              <div style={{ marginTop: 10, fontSize: 12.5 }} className="muted">
                Deploying itself costs you 0 ANM — the anchor transaction gas is paid by the platform wallet.{' '}
                {estimateRes.anchor.willBroadcast ? (
                  <>This deploy <b style={{ color: 'var(--good)' }}>will anchor on-chain</b>
                  {estimateRes.anchor.walletAddress && <> from <span className="mono">{estimateRes.anchor.walletAddress.slice(0, 14)}…</span></>}.</>
                ) : (
                  <>This deploy <b style={{ color: 'var(--warn)' }}>will not anchor</b>: {estimateRes.anchor.reason ?? 'anchoring unavailable'}. It still goes live — honestly marked unanchored.</>
                )}
              </div>
            </div>
          )}

          <ApiErrBox error={apiError} />

          {/* deploy button + live progress */}
          <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="btn primary" onClick={deploy} disabled={deployBlocked} style={{ fontSize: 15, padding: '11px 22px' }}>
              {deploying ? `Deploying… ${deployElapsed}s` : isRedeploy ? '🚀 Deploy new version' : '🚀 Deploy'}
            </button>
            {deploying && (
              <span className="muted" style={{ fontSize: 12.5 }}>
                The server is validating, building the DA bundle, anchoring on-chain, then activating —
                anchoring waits for chain inclusion, so this can take a minute.
              </span>
            )}
          </div>

          {deployment && (
            <div className="panel" style={{ marginTop: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <h3 style={{ margin: 0, fontSize: 15 }}>Deployment {deployedVersion != null ? `— version ${deployedVersion}` : ''}</h3>
                <CloudStatusPill status={deployment.status} />
              </div>

              {/* stage ladder driven by the REAL CloudDeployStatus */}
              <div className="cl-scroll" style={{ display: 'flex', gap: 6, marginTop: 12, overflowX: 'auto', paddingBottom: 4 }}>
                {STAGES.map((s, i) => {
                  const reached = deployment.status === 'FAILED' ? false : stageIdx >= i || deployment.status === 'ACTIVE';
                  const current = deployment.status === s;
                  return (
                    <span key={s} className="pill" style={{
                      fontSize: 11, whiteSpace: 'nowrap', flexShrink: 0,
                      color: current ? 'var(--text)' : reached ? 'var(--good)' : 'var(--text-faint)',
                      borderColor: current ? 'var(--accent)' : reached ? 'var(--good)' : 'var(--border)',
                      background: current ? 'rgba(108,92,255,0.1)' : 'transparent',
                    }}>
                      {reached && !current ? '✓ ' : ''}{s.replace(/_/g, ' ').toLowerCase()}
                    </span>
                  );
                })}
                {deployment.status === 'FAILED' && <CloudStatusPill status="FAILED" />}
              </div>

              {deployment.error && <ErrBox>{deployment.error}</ErrBox>}

              {/* anchor truth */}
              <div style={{ marginTop: 12, fontSize: 12.5 }}>
                {deployment.anchorTxid ? (
                  <div className="cl-code" style={{ whiteSpace: 'normal' }}>
                    ⚓ anchored · tx <span className="mono" style={{ overflowWrap: 'anywhere' }}>{deployment.anchorTxid}</span>
                    {deployment.anchorHeight != null && <> · height {deployment.anchorHeight}</>}
                    {' '}· {Math.min(deployment.anchorConfirms, boot.anchorConfirmations)}/{boot.anchorConfirmations} confirmations
                    {deployment.daBlobId && <> · DA blob <span className="mono" style={{ overflowWrap: 'anywhere' }}>{deployment.daBlobId}</span></>}
                  </div>
                ) : (
                  <div className="muted">
                    Not anchored{unanchoredReason(deployment.logs) ? <>: {unanchoredReason(deployment.logs)}</> : ' — no anchor transaction was recorded for this deployment.'}
                  </div>
                )}
              </div>

              {/* deploy log */}
              {deployment.logs.length > 0 && (
                <details style={{ marginTop: 10 }} open={deployment.status === 'FAILED'}>
                  <summary className="muted" style={{ fontSize: 12, cursor: 'pointer' }}>Deployment log ({deployment.logs.length})</summary>
                  <div className="cl-loglines" style={{ marginTop: 6, maxHeight: 220, overflowY: 'auto' }}>
                    {deployment.logs.map((l, i) => (
                      <div key={i} style={{ color: l.level === 'error' ? 'var(--bad)' : l.level === 'warn' ? 'var(--warn)' : 'var(--text-dim)' }}>
                        {l.ts ? `${new Date(l.ts).toLocaleTimeString()} ` : ''}{l.message}
                      </div>
                    ))}
                  </div>
                </details>
              )}

              {/* live endpoint + test panel */}
              {deployment.status === 'ACTIVE' && (
                <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                  <OkBox>
                    Your function is <b>live</b>. Share the endpoint — every call meters real usage and
                    pays your account its share.
                  </OkBox>
                  <div style={{ marginTop: 12 }}>
                    <TestInvoke ownerSegment={boot.ownerSegment} slug={slugValue} displayBase={boot.publicBase} />
                  </div>
                  {deployedFunctionId && (
                    <div style={{ marginTop: 12, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                      <a className="btn primary" href={`/cloud/functions/${deployedFunctionId}`}>Open function page →</a>
                      <a className="btn ghost" href="/cloud/functions">All functions</a>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── right: configuration ─────────────────────────────────────── */}
        <div style={{ minWidth: 0 }}>
          <div className="panel" style={{ padding: 16 }}>
            <h3 style={{ margin: '0 0 12px', fontSize: 14.5 }}>Function</h3>

            <label style={labelStyle}>Name</label>
            <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} placeholder="Sum of squares" disabled={isRedeploy} />

            <label style={{ ...labelStyle, marginTop: 12 }}>Slug (public URL)</label>
            <input
              style={{ ...inputStyle, borderColor: slugValue && !slugOk ? 'var(--bad)' : undefined }}
              value={slugValue}
              onChange={(e) => { setSlugTouched(true); setSlug(slugify(e.target.value)); }}
              placeholder="sum-of-squares"
              disabled={isRedeploy}
              spellCheck={false}
            />
            <div className="mono muted" style={{ fontSize: 10.5, marginTop: 5, overflowWrap: 'anywhere' }}>{endpointUrl}</div>

            <label style={{ ...labelStyle, marginTop: 12 }}>Description</label>
            <textarea className="cl-input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} disabled={isRedeploy} style={{ fontFamily: 'inherit', fontSize: 13 }} />

            <label style={{ ...labelStyle, marginTop: 12 }}>Entrypoint (module-level function)</label>
            <input
              style={{ ...inputStyle, fontFamily: 'var(--mono)', borderColor: entrypoint && !entryOk ? 'var(--bad)' : undefined }}
              value={entrypoint}
              onChange={(e) => setEntrypoint(e.target.value)}
              spellCheck={false}
            />

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12 }}>
              <div>
                <label style={labelStyle}>Timeout (s)</label>
                <input type="number" style={inputStyle} value={timeoutS}
                  min={Math.ceil(boot.limits.minTimeoutMs / 1000)} max={Math.floor(boot.limits.maxTimeoutMs / 1000)}
                  onChange={(e) => setTimeoutS(Math.min(Math.floor(boot.limits.maxTimeoutMs / 1000), Math.max(1, Number(e.target.value) || 1)))}
                  disabled={isRedeploy} />
              </div>
              <div>
                <label style={labelStyle}>Memory (MB)</label>
                <input type="number" step={64} style={inputStyle} value={memoryMb}
                  min={boot.limits.minMemoryMb} max={boot.limits.maxMemoryMb}
                  onChange={(e) => setMemoryMb(Math.min(boot.limits.maxMemoryMb, Math.max(boot.limits.minMemoryMb, Number(e.target.value) || boot.limits.minMemoryMb)))}
                  disabled={isRedeploy} />
              </div>
            </div>

            {isRedeploy && (
              <p className="muted" style={{ fontSize: 11.5, marginTop: 10 }}>
                Name, slug and limits are edited on the function&apos;s Settings tab — this page deploys new source.
              </p>
            )}
          </div>

          {!isRedeploy && (
            <>
              <div className="panel" style={{ padding: 16, marginTop: 12 }}>
                <h3 style={{ margin: '0 0 10px', fontSize: 14.5 }}>Monetization</h3>
                <label style={labelStyle}>Price per call (ANM, on top of metered cost)</label>
                <input
                  style={{ ...inputStyle, borderColor: priceOk ? undefined : 'var(--bad)' }}
                  value={priceAnm}
                  onChange={(e) => setPriceAnm(e.target.value)}
                  placeholder="0 — pure metered"
                  inputMode="decimal"
                />
                {!priceOk && <div style={{ color: 'var(--bad)', fontSize: 11.5, marginTop: 4 }}>enter a plain ANM decimal, e.g. 0.001</div>}
                <label style={{ ...labelStyle, marginTop: 12 }}>Visibility</label>
                <select className="cl-input" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
                  <option value="PUBLIC">Public — listed, anyone can call</option>
                  <option value="UNLISTED">Unlisted — anyone with the URL</option>
                  <option value="PRIVATE" disabled={!boot.plan.privateDeployments}>
                    Private — only you{boot.plan.privateDeployments ? '' : ' (Business plan)'}
                  </option>
                </select>
                <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, fontSize: 13, cursor: 'pointer', minHeight: 40 }}>
                  <input type="checkbox" checked={requiresAuth} onChange={(e) => setRequiresAuth(e.target.checked)} style={{ width: 16, height: 16 }} />
                  Require an API key to call (no anonymous free-tier calls)
                </label>
              </div>

              <div className="panel" style={{ padding: 16, marginTop: 12 }}>
                <h3 style={{ margin: '0 0 4px', fontSize: 14.5 }}>Capabilities</h3>
                <p className="muted" style={{ fontSize: 11.5, margin: '0 0 10px' }}>
                  The sandbox only brokers what you declare. Sensitive ones additionally require each
                  caller&apos;s explicit grant.
                </p>
                {CAPABILITY_INFO.map((c) => (
                  <label key={c.key} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '6px 0', fontSize: 12.5, cursor: 'pointer' }}>
                    <input type="checkbox" checked={caps.includes(c.key)} onChange={() => toggleCap(c.key)} style={{ width: 15, height: 15, marginTop: 2 }} />
                    <span>
                      <b>{c.label}</b>
                      {c.sensitive && <span className="pill" style={{ marginLeft: 6, fontSize: 9.5, color: 'var(--warn)', borderColor: 'var(--warn)' }}>grant required</span>}
                      <span className="muted" style={{ display: 'block', fontSize: 11.5 }}>{c.blurb}</span>
                    </span>
                  </label>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
