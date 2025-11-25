/**
 * Verify slice — submit source+manifest for verification and track job status/result.
 *
 * Integrates with studio-services verify endpoints:
 *  - POST /verify                      → Services.verifySubmit({ source, manifest, address? })
 *  - GET  /verify/{address}           → Services.getVerifyByAddress(address)
 *  - GET  /verify/{txHash}            → Services.getVerifyByTx(txHash)
 *  - GET  /verify?jobId=...           → Services.verifyStatus(jobId)   (shape may vary; handled defensively)
 *
 * This slice manages a single active job at a time. It supports:
 *  - submit(inputs)      → start a job, store jobId
 *  - attachFromDeploy()  → seed txHash/address from the Deploy slice (if available)
 *  - poll()              → poll current job once
 *  - startPolling()      → start a short-lived polling loop with backoff
 *  - verifyAddress()     → direct lookup by address (no job)
 *  - verifyTx()          → direct lookup by tx hash (no job)
 *  - reset()             → clear state and stop polling
 */

import { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';
import * as Services from '../services/servicesApi';

export type VerifyPhase =
  | 'idle'
  | 'submitting'
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'error';

export interface VerifyInputs {
  source: string;     // contract source code (Python)
  manifest: any;      // ABI + metadata
  address?: string;   // optional already-deployed address to bind to
}

export interface VerifyResult {
  verified: boolean;
  address?: string;
  txHash?: string;
  codeHash?: string;
  manifestHash?: string;
  compiler?: string;
  diagnostics?: string[];
  artifactsId?: string;        // content-addressed id (if stored)
  verifiedAt?: string;         // ISO time
  raw?: any;                   // provider-specific payload
}

export interface VerifySlice {
  phase: VerifyPhase;
  jobId?: string;
  address?: string;
  txHash?: string;
  result?: VerifyResult;
  error?: string;
  lastUpdateAt?: number;

  // internal
  _reqId: number;
  _pollTimer?: any;
  _pollIntervalMs: number;

  // actions
  reset(): void;

  attachFromDeploy(): void;

  submit(inputs?: Partial<VerifyInputs>): Promise<boolean>;

  poll(): Promise<'continue' | 'stop'>;

  startPolling(): void;
  stopPolling(): void;

  verifyAddress(addr: string): Promise<VerifyResult | undefined>;
  verifyTx(hash: string): Promise<VerifyResult | undefined>;
}

function now(): number {
  return Date.now();
}

function safeServices<T extends keyof typeof Services>(k: T): (typeof Services)[T] | undefined {
  const fn = (Services as any)[k];
  return typeof fn === 'function' ? fn : undefined;
}

const verifySlice: SliceCreator<VerifySlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => ({
  phase: 'idle',
  jobId: undefined,
  address: undefined,
  txHash: undefined,
  result: undefined,
  error: undefined,
  lastUpdateAt: undefined,

  _reqId: 0,
  _pollTimer: undefined,
  _pollIntervalMs: 1000,

  reset() {
    const timer = (get() as unknown as VerifySlice)._pollTimer;
    if (timer) clearTimeout(timer);
    set({
      phase: 'idle',
      jobId: undefined,
      address: undefined,
      txHash: undefined,
      result: undefined,
      error: undefined,
      lastUpdateAt: now(),
      _pollTimer: undefined,
      _reqId: (get() as unknown as VerifySlice)._reqId + 1,
      _pollIntervalMs: 1000,
    } as Partial<StoreState>);
  },

  attachFromDeploy() {
    const s: any = get();
    const deploy = s?.deploy ?? s; // tolerate different slice layouts
    const address = deploy?.address;
    const txHash = deploy?.txHash;
    if (address || txHash) {
      set({ address, txHash } as Partial<StoreState>);
    }
  },

  async submit(inputs?: Partial<VerifyInputs>): Promise<boolean> {
    const myId = (get() as unknown as VerifySlice)._reqId + 1;
    const svcSubmit = safeServices('verifySubmit');
    if (!svcSubmit) {
      set({
        phase: 'error',
        error: 'Verification service not configured',
        lastUpdateAt: now(),
      } as Partial<StoreState>);
      return false;
    }

    // Resolve defaults from project/editor state if available
    let source = inputs?.source;
    let manifest = inputs?.manifest;
    let address = inputs?.address ?? (get() as unknown as VerifySlice).address;

    if (!source || !manifest) {
      const s: any = get();
      const files: Record<string, { path: string; content: string }> =
        s?.files ?? s?.project?.files ?? {};
      const manifestFile = Object.values(files).find((f) => f.path.endsWith('manifest.json'));
      const codeFile =
        Object.values(files).find((f) => f.path.endsWith('/contract.py')) ||
        Object.values(files).find((f) => f.path.endsWith('.py'));
      if (!manifest && manifestFile) {
        try { manifest = JSON.parse(manifestFile.content); } catch { /* ignore */ }
      }
      if (!source && codeFile) source = codeFile.content;
    }

    if (!source || !manifest) {
      set({
        phase: 'error',
        error: 'Missing source or manifest for verification',
        lastUpdateAt: now(),
      } as Partial<StoreState>);
      return false;
    }

    set({
      phase: 'submitting',
      error: undefined,
      result: undefined,
      _reqId: myId,
      lastUpdateAt: now(),
    } as Partial<StoreState>);

    try {
      const res: any = await (svcSubmit as any)({ source, manifest, address });
      const jobId: string | undefined = res?.jobId ?? res?.id ?? res;
      if (!jobId) throw new Error('Verify service did not return a job id');

      if ((get() as unknown as VerifySlice)._reqId !== myId) return false;

      set({
        jobId,
        phase: 'queued',
        lastUpdateAt: now(),
      } as Partial<StoreState>);

      (get() as unknown as VerifySlice).startPolling();
      return true;
    } catch (e: any) {
      if ((get() as unknown as VerifySlice)._reqId === myId) {
        set({
          phase: 'error',
          error: String(e?.message ?? e),
          lastUpdateAt: now(),
        } as Partial<StoreState>);
      }
      return false;
    }
  },

  async poll(): Promise<'continue' | 'stop'> {
    const jobId = (get() as unknown as VerifySlice).jobId;
    if (!jobId) return 'stop';

    const myId = (get() as unknown as VerifySlice)._reqId;
    const svcStatus = safeServices('verifyStatus');
    if (!svcStatus) return 'stop';

    try {
      const st: any = await (svcStatus as any)(jobId);
      if ((get() as unknown as VerifySlice)._reqId !== myId) return 'stop';

      const status: string = (st?.status ?? st?.phase ?? '').toString().toLowerCase();

      // Map status
      let phase: VerifyPhase = (get() as unknown as VerifySlice).phase;
      if (status.includes('queue')) phase = 'queued';
      else if (status.includes('run') || status.includes('work') || status === 'pending') phase = 'running';
      else if (status.includes('fail') || status.includes('error')) phase = 'failed';
      else if (status.includes('done') || status.includes('complete') || status === 'ok') phase = 'completed';

      // Build result if present
      let result: VerifyResult | undefined;
      const r = st?.result ?? st?.data ?? undefined;
      if (r) {
        result = {
          verified: !!(r.verified ?? r.ok ?? (r.match === true)),
          address: r.address ?? (get() as unknown as VerifySlice).address,
          txHash: r.txHash,
          codeHash: r.codeHash ?? r.bytecodeHash ?? r.code_hash,
          manifestHash: r.manifestHash ?? r.manifest_hash,
          compiler: r.compiler,
          diagnostics: r.diagnostics ?? r.errors ?? r.notes,
          artifactsId: r.artifactsId ?? r.artifact_id,
          verifiedAt: r.verifiedAt ?? r.timestamp,
          raw: r,
        };
      }

      set({
        phase,
        result,
        lastUpdateAt: now(),
      } as Partial<StoreState>);

      if (phase === 'completed' || phase === 'failed' || phase === 'error') {
        return 'stop';
      }
      return 'continue';
    } catch (e: any) {
      set({
        phase: 'error',
        error: `Verify poll failed: ${String(e?.message ?? e)}`,
        lastUpdateAt: now(),
      } as Partial<StoreState>);
      return 'stop';
    }
  },

  startPolling() {
    const slice = (get() as unknown as VerifySlice);
    const myId = slice._reqId;
    const run = async () => {
      const res = await (get() as unknown as VerifySlice).poll();
      if ((get() as unknown as VerifySlice)._reqId !== myId) return;
      if (res === 'continue') {
        // simple backoff up to 5s
        const next = Math.min((get() as unknown as VerifySlice)._pollIntervalMs * 1.4, 5000);
        set({ _pollIntervalMs: next } as Partial<StoreState>);
        const t = setTimeout(run, next);
        set({ _pollTimer: t } as Partial<StoreState>);
      } else {
        const t = (get() as unknown as VerifySlice)._pollTimer;
        if (t) clearTimeout(t);
        set({ _pollTimer: undefined } as Partial<StoreState>);
      }
    };
    // reset backoff and kick
    const prevTimer = slice._pollTimer;
    if (prevTimer) clearTimeout(prevTimer);
    set({ _pollIntervalMs: 1000, _pollTimer: undefined } as Partial<StoreState>);
    const t = setTimeout(run, 50);
    set({ _pollTimer: t } as Partial<StoreState>);
  },

  stopPolling() {
    const t = (get() as unknown as VerifySlice)._pollTimer;
    if (t) clearTimeout(t);
    set({ _pollTimer: undefined } as Partial<StoreState>);
  },

  async verifyAddress(addr: string): Promise<VerifyResult | undefined> {
    const svc = safeServices('getVerifyByAddress');
    if (!svc) {
      set({ phase: 'error', error: 'Verify-by-address not available', lastUpdateAt: now() } as Partial<StoreState>);
      return undefined;
    }
    set({ phase: 'running', address: addr, error: undefined } as Partial<StoreState>);
    try {
      const r: any = await (svc as any)(addr);
      const result: VerifyResult = {
        verified: !!(r?.verified ?? r?.ok ?? (r?.match === true)),
        address: r?.address ?? addr,
        txHash: r?.txHash,
        codeHash: r?.codeHash ?? r?.bytecodeHash ?? r?.code_hash,
        manifestHash: r?.manifestHash ?? r?.manifest_hash,
        compiler: r?.compiler,
        diagnostics: r?.diagnostics ?? r?.errors ?? r?.notes,
        artifactsId: r?.artifactsId ?? r?.artifact_id,
        verifiedAt: r?.verifiedAt ?? r?.timestamp,
        raw: r,
      };
      set({ phase: 'completed', result, lastUpdateAt: now() } as Partial<StoreState>);
      return result;
    } catch (e: any) {
      set({ phase: 'error', error: String(e?.message ?? e), lastUpdateAt: now() } as Partial<StoreState>);
      return undefined;
    }
  },

  async verifyTx(hash: string): Promise<VerifyResult | undefined> {
    const svc = safeServices('getVerifyByTx');
    if (!svc) {
      set({ phase: 'error', error: 'Verify-by-tx not available', lastUpdateAt: now() } as Partial<StoreState>);
      return undefined;
    }
    set({ phase: 'running', txHash: hash, error: undefined } as Partial<StoreState>);
    try {
      const r: any = await (svc as any)(hash);
      const result: VerifyResult = {
        verified: !!(r?.verified ?? r?.ok ?? (r?.match === true)),
        address: r?.address,
        txHash: r?.txHash ?? hash,
        codeHash: r?.codeHash ?? r?.bytecodeHash ?? r?.code_hash,
        manifestHash: r?.manifestHash ?? r?.manifest_hash,
        compiler: r?.compiler,
        diagnostics: r?.diagnostics ?? r?.errors ?? r?.notes,
        artifactsId: r?.artifactsId ?? r?.artifact_id,
        verifiedAt: r?.verifiedAt ?? r?.timestamp,
        raw: r,
      };
      set({ phase: 'completed', result, lastUpdateAt: now() } as Partial<StoreState>);
      return result;
    } catch (e: any) {
      set({ phase: 'error', error: String(e?.message ?? e), lastUpdateAt: now() } as Partial<StoreState>);
      return undefined;
    }
  },
});

registerSlice<VerifySlice>(verifySlice);

export default undefined;
