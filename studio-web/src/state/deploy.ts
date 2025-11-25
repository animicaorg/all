/**
 * Deploy slice — orchestrates packaging (manifest+code), optional preflight,
 * wallet signature via provider, submission, and awaiting the on-chain receipt.
 *
 * It integrates with:
 *  - ../services/provider  (detect window.animica / AIP-1193-like)
 *  - ../services/servicesApi (optional /preflight and /deploy relay of *signed* tx)
 *  - ../services/rpc (wait for receipt if wallet only returns tx hash)
 *  - ../state/project (to locate manifest.json and contract source)
 *  - ../state/compile (optionally provide compiled IR/code hash if needed)
 *
 * The slice is defensive: it supports several provider method shapes:
 *   - animica_deployPackage({ manifest, code, gas, maxFee, value })
 *   - animica_sendTransaction({ kind: "deploy", manifest, code, gas, maxFee, value })
 *   - animica_signTransaction({ ...deploy fields... }) → { raw } and then submit via RPC or studio-services
 *   - animica_sendRawTransaction({ raw })
 *
 * No server-side signing: if we use studio-services /deploy, we still send a signed CBOR blob.
 */

import { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';
import * as Provider from '../services/provider';
import * as Services from '../services/servicesApi';
import * as RPC from '../services/rpc';

export interface DeployInputs {
  manifest: any;
  code: string | Uint8Array;   // Python source or packaged bytes; service/provider should handle form
  value?: string;              // optional value (as decimal string)
  gasLimit?: number;
  maxFee?: string;             // price per gas unit (decimal string)
}

export type DeployStatus =
  | 'idle'
  | 'preflighting'
  | 'building'
  | 'awaiting_signature'
  | 'submitting'
  | 'pending'
  | 'success'
  | 'error';

export interface DeployReceipt {
  transactionHash: string;
  blockNumber?: number;
  gasUsed?: number;
  status?: 'SUCCESS' | 'REVERT' | 'OOG' | 'FAILED';
  contractAddress?: string;
  raw?: any;
}

export interface DeploySlice {
  // user-editable inputs
  gasLimit?: number;
  maxFee?: string;
  value?: string;

  // derived from project
  manifest?: any;
  code?: string | Uint8Array;

  // progress & outputs
  status: DeployStatus;
  txHash?: string;
  address?: string;
  receipt?: DeployReceipt;
  error?: string;
  lastRunAt?: number;

  // internal request id to cancel stale updates
  _reqId: number;

  // actions
  setGasLimit(v?: number): void;
  setMaxFee(v?: string): void;
  setValue(v?: string): void;

  /**
   * Pull manifest+code from the Project slice (active folder), falling back to root.
   * Stores them in this slice for preview and downstream actions.
   */
  prepareFromProject(): void;

  /**
   * Preflight via studio-services (optional). Returns estimated gas & notes if available.
   */
  preflight(): Promise<{ gasEstimate?: number; notes?: string }>;

  /**
   * Main deploy flow. Uses wallet provider; falls back to provider+RPC; never server-signs.
   */
  deploy(opts?: Partial<DeployInputs>): Promise<boolean>;
}

function now(): number {
  return Date.now();
}

function readProjectPackage(get: GetState<StoreState>): { manifest?: any; code?: string } {
  // Expect Project slice to expose files map and active file path
  const s: any = get();
  const files: Record<string, { path: string; content: string }> = s?.files ?? s?.project?.files ?? {};
  const active: string | undefined = s?.active ?? s?.project?.active;

  const tryPaths: string[] = [];
  if (active) {
    const dir = active.includes('/') ? active.split('/').slice(0, -1).join('/') : '';
    if (dir) {
      tryPaths.push(`${dir}/manifest.json`);
      // heuristic: look for first *.py in that dir
      const inDir = Object.keys(files).filter(p => p.startsWith(dir + '/') && p.endsWith('.py'));
      if (inDir.length) {
        // prefer a file literally named contract.py
        const pref = inDir.find(p => p.endsWith('/contract.py')) ?? inDir[0];
        tryPaths.push(pref);
      }
    }
  }
  // root fallbacks
  tryPaths.push('manifest.json');
  // choose first .py in root if any
  const rootPy = Object.keys(files).filter(p => p.endsWith('.py'));
  if (rootPy.length) {
    const pref = rootPy.find(p => p === 'contract.py') ?? rootPy[0];
    tryPaths.push(pref);
  }

  let manifest: any | undefined;
  let code: string | undefined;

  for (const p of tryPaths) {
    const f = files[p];
    if (!f) continue;
    if (p.endsWith('manifest.json') && !manifest) {
      try {
        manifest = JSON.parse(f.content);
      } catch { /* ignore parse error */ }
    } else if (p.endsWith('.py') && !code) {
      code = f.content;
    }
    if (manifest && code) break;
  }

  return { manifest, code };
}

async function requestProviderTx(methods: string[], params: any): Promise<any> {
  const prov = await Provider.getProvider();
  let lastErr: any;
  for (const m of methods) {
    try {
      // eslint-disable-next-line no-await-in-loop
      const res = await prov.request({ method: m, params: [params] });
      return res;
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr ?? new Error('No supported provider method for deployment');
}

const deploySlice: SliceCreator<DeploySlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => ({
  gasLimit: undefined,
  maxFee: undefined,
  value: undefined,

  manifest: undefined,
  code: undefined,

  status: 'idle',
  txHash: undefined,
  address: undefined,
  receipt: undefined,
  error: undefined,
  lastRunAt: undefined,
  _reqId: 0,

  setGasLimit(v?: number) { set({ gasLimit: v } as Partial<StoreState>); },
  setMaxFee(v?: string) { set({ maxFee: v } as Partial<StoreState>); },
  setValue(v?: string) { set({ value: v } as Partial<StoreState>); },

  prepareFromProject() {
    const { manifest, code } = readProjectPackage(get);
    set({ manifest, code } as Partial<StoreState>);
  },

  async preflight(): Promise<{ gasEstimate?: number; notes?: string }> {
    const myId = (get() as unknown as DeploySlice)._reqId + 1;
    set({ status: 'preflighting', error: undefined, _reqId: myId } as Partial<StoreState>);

    const manifest = (get() as unknown as DeploySlice).manifest ?? readProjectPackage(get).manifest;
    const code = (get() as unknown as DeploySlice).code ?? readProjectPackage(get).code;

    if (!manifest || !code) {
      if ((get() as unknown as DeploySlice)._reqId === myId) {
        set({ status: 'error', error: 'Missing manifest or contract source', lastRunAt: now() } as Partial<StoreState>);
      }
      return { notes: 'Provide manifest.json and contract source' };
    }

    try {
      if (typeof Services.preflight !== 'function') {
        // Services not configured; return neutral
        if ((get() as unknown as DeploySlice)._reqId === myId) {
          set({ status: 'idle' } as Partial<StoreState>);
        }
        return { notes: 'Preflight unavailable; proceeding without it' };
      }
      const res = await Services.preflight({ manifest, code });
      if ((get() as unknown as DeploySlice)._reqId !== myId) return {};
      const gasEstimate: number | undefined = res?.gasEstimate ?? res?.estimate;
      set({ status: 'idle' } as Partial<StoreState>);
      return { gasEstimate, notes: res?.notes };
    } catch (e: any) {
      if ((get() as unknown as DeploySlice)._reqId === myId) {
        set({ status: 'idle', error: `Preflight failed: ${String(e?.message ?? e)}` } as Partial<StoreState>);
      }
      return {};
    }
  },

  async deploy(opts?: Partial<DeployInputs>): Promise<boolean> {
    const myId = (get() as unknown as DeploySlice)._reqId + 1;
    set({
      status: 'building',
      txHash: undefined,
      address: undefined,
      receipt: undefined,
      error: undefined,
      _reqId: myId,
    } as Partial<StoreState>);

    // Resolve inputs
    let manifest = opts?.manifest ?? (get() as unknown as DeploySlice).manifest;
    let code = opts?.code ?? (get() as unknown as DeploySlice).code;
    const gasLimit = opts?.gasLimit ?? (get() as unknown as DeploySlice).gasLimit;
    const maxFee = opts?.maxFee ?? (get() as unknown as DeploySlice).maxFee;
    const value = opts?.value ?? (get() as unknown as DeploySlice).value;

    if (!manifest || !code) {
      const fromProj = readProjectPackage(get);
      manifest = manifest ?? fromProj.manifest;
      code = code ?? fromProj.code;
    }

    if (!manifest || !code) {
      if ((get() as unknown as DeploySlice)._reqId === myId) {
        set({
          status: 'error',
          error: 'Missing manifest or contract source',
          lastRunAt: now(),
        } as Partial<StoreState>);
      }
      return false;
    }

    // Build params object for provider
    const txParams: any = { kind: 'deploy', manifest, code };
    if (typeof gasLimit === 'number') txParams.gas = gasLimit;
    if (typeof maxFee === 'string') txParams.maxFee = maxFee;
    if (typeof value === 'string') txParams.value = value;

    // Ask wallet to sign/deploy
    try {
      if ((get() as unknown as DeploySlice)._reqId !== myId) return false;
      set({ status: 'awaiting_signature' } as Partial<StoreState>);

      // Try direct deploy first
      const directMethods = ['animica_deployPackage', 'animica_sendTransaction'];
      let response: any | undefined;
      let usedDirect = false;
      try {
        response = await requestProviderTx(directMethods, txParams);
        usedDirect = true;
      } catch (directErr) {
        // Fall back to sign-only then submit
        const signRes = await requestProviderTx(['animica_signTransaction'], txParams);
        const raw = signRes?.raw ?? signRes?.signed ?? signRes?.tx ?? signRes;
        if (!raw) throw new Error('Wallet did not return signed transaction');
        set({ status: 'submitting' } as Partial<StoreState>);

        // Prefer provider sendRaw if present, else via RPC or studio-services
        try {
          const sendRawRes = await requestProviderTx(['animica_sendRawTransaction'], { raw });
          response = sendRawRes;
        } catch {
          // Submit via RPC or services
          if (typeof RPC.sendRawTransaction === 'function') {
            response = await RPC.sendRawTransaction(raw);
          } else if (typeof Services.deploySigned === 'function') {
            response = await Services.deploySigned({ raw });
          } else {
            throw new Error('No submission path available for signed transaction');
          }
        }
      }

      // Normalize response → txHash
      const txHash: string =
        response?.txHash ?? response?.transactionHash ?? response?.hash ?? response;

      if (!txHash || typeof txHash !== 'string') {
        throw new Error('Wallet did not return a transaction hash');
      }

      if ((get() as unknown as DeploySlice)._reqId !== myId) return false;
      set({ status: 'pending', txHash } as Partial<StoreState>);

      // Wait for receipt
      let receipt: DeployReceipt | undefined;

      // Prefer RPC helper
      if (typeof RPC.waitForReceipt === 'function') {
        const r = await RPC.waitForReceipt(txHash, { timeoutMs: 180_000 });
        receipt = {
          transactionHash: r?.transactionHash ?? txHash,
          blockNumber: r?.blockNumber,
          gasUsed: r?.gasUsed ?? r?.receipt?.gasUsed,
          status: r?.status ?? r?.receipt?.status,
          contractAddress: r?.contractAddress ?? r?.receipt?.contractAddress,
          raw: r,
        };
      } else {
        // Try provider request or polling
        const prov = await Provider.getProvider().catch(() => null as any);
        if (prov?.request) {
          try {
            const r = await prov.request({ method: 'animica_getTransactionReceipt', params: [txHash] });
            if (r) {
              receipt = {
                transactionHash: r?.transactionHash ?? txHash,
                blockNumber: r?.blockNumber,
                gasUsed: r?.gasUsed,
                status: r?.status,
                contractAddress: r?.contractAddress,
                raw: r,
              };
            }
          } catch { /* ignore; will keep pending */ }
        }
      }

      if ((get() as unknown as DeploySlice)._reqId !== myId) return true;

      if (receipt) {
        set({
          status: 'success',
          receipt,
          address: receipt.contractAddress,
          lastRunAt: now(),
        } as Partial<StoreState>);
      } else {
        // If we cannot fetch a receipt yet, keep pending but record the hash
        set({
          status: 'pending',
          txHash,
          lastRunAt: now(),
        } as Partial<StoreState>);
      }

      return true;
    } catch (e: any) {
      if ((get() as unknown as DeploySlice)._reqId === myId) {
        set({
          status: 'error',
          error: String(e?.message ?? e),
          lastRunAt: now(),
        } as Partial<StoreState>);
      }
      return false;
    }
  },
});

registerSlice<DeploySlice>(deploySlice);

export default undefined;
