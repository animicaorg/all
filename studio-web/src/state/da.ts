/**
 * DA slice — pin blobs to Data Availability service and fetch by commitment.
 *
 * Integrates with studio-web services layer (../services/da.ts), which should provide:
 *  - postBlob(ns: number, data: Uint8Array | ArrayBuffer, mime?: string, onProgress?: (pct:number)=>void)
 *      → Promise<{ commitment: string; receipt?: { root: string; size: number; ns: number; sig?: string } }>
 *  - getBlob(commitment: string) → Promise<ArrayBuffer>
 *  - getProof(commitment: string, params?: { samples?: number })
 *      → Promise<{ ok: boolean; commitment: string; root: string; samples?: number; proof: any }>
 *
 * This slice keeps lightweight metadata about pinned blobs and availability proofs.
 * Raw data is not persisted in the store to avoid memory bloat; callers should hold it or refetch.
 */

import { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';
import * as DASvc from '../services/da';

export type CommitmentHex = `0x${string}`;

export interface DAReceipt {
  root: string;      // NMT root (0x…)
  size: number;      // total blob size (bytes)
  ns: number;        // namespace id
  sig?: string;      // optional service signature / policy binding
}

export type PinStatus = 'pending' | 'pinned' | 'error';

export interface PinRecord {
  commitment: CommitmentHex;
  ns: number;
  size?: number;
  mime?: string;
  createdAt: string;
  status: PinStatus;
  receipt?: DAReceipt;
  error?: string;
  progress?: number;        // 0..100
}

export interface ProofRecord {
  commitment: CommitmentHex;
  root: string;
  ok: boolean;
  samples?: number;
  proof: any;
  verifiedAt: string;
}

export interface DASlice {
  pins: Record<string, PinRecord>;
  proofs: Record<string, ProofRecord>;
  uploading?: boolean;
  fetching?: boolean;
  error?: string;

  reset(): void;

  pin(ns: number, data: Uint8Array | ArrayBuffer | Blob, mime?: string, onProgress?: (pct: number) => void): Promise<CommitmentHex | undefined>;

  fetch(commitment: string): Promise<ArrayBuffer | undefined>;

  prove(commitment: string, params?: { samples?: number }): Promise<ProofRecord | undefined>;

  upsertPinMeta(meta: Partial<PinRecord> & { commitment: string }): void;

  remove(commitment: string): void;
}

// ---------- helpers ----------

function safeSvc<T extends keyof typeof DASvc>(k: T): (typeof DASvc)[T] | undefined {
  const fn = (DASvc as any)[k];
  return typeof fn === 'function' ? fn : undefined;
}

async function toBytes(src: Uint8Array | ArrayBuffer | Blob): Promise<Uint8Array> {
  if (src instanceof Uint8Array) return src;
  if (src instanceof Blob) {
    const buf = await src.arrayBuffer();
    return new Uint8Array(buf);
  }
  if (src instanceof ArrayBuffer) return new Uint8Array(src);
  // @ts-expect-error (compile-time guard)
  throw new TypeError('Unsupported data type for pin()');
}

function asHex(x: unknown): CommitmentHex {
  const s = String(x ?? '');
  return (s.startsWith('0x') ? s : `0x${s}`) as CommitmentHex;
}

// ---------- slice ----------

const createDASlice: SliceCreator<DASlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => ({
  pins: {},
  proofs: {},
  uploading: false,
  fetching: false,
  error: undefined,

  reset() {
    set({ pins: {}, proofs: {}, uploading: false, fetching: false, error: undefined } as Partial<StoreState>);
  },

  async pin(ns, data, mime, onProgress) {
    const svc = safeSvc('postBlob');
    if (!svc) {
      set({ error: 'DA: postBlob service not available' } as Partial<StoreState>);
      return undefined;
    }
    try {
      const bytes = await toBytes(data);
      const size = bytes.byteLength;

      set({ uploading: true, error: undefined } as Partial<StoreState>);

      let lastPct = -1;
      const progress = (pct: number) => {
        // coalesce to reduce state churn
        const rounded = Math.max(0, Math.min(100, Math.round(pct)));
        if (rounded === lastPct) return;
        lastPct = rounded;
        // If we have a temp commitment slot, update its progress
        // (We only know the commitment after the service returns; until then, no-op)
        onProgress?.(rounded);
      };

      const res: any = await (svc as any)(ns, bytes, mime, progress);
      const commitment = asHex(res?.commitment);
      const receipt: DAReceipt | undefined = res?.receipt
        ? {
            root: asHex(res.receipt.root),
            size: Number(res.receipt.size ?? size),
            ns: Number(res.receipt.ns ?? ns),
            sig: res.receipt.sig,
          }
        : undefined;

      const rec: PinRecord = {
        commitment,
        ns,
        size,
        mime,
        createdAt: new Date().toISOString(),
        status: 'pinned',
        receipt,
        progress: 100,
      };

      set((s: any) => ({
        uploading: false,
        pins: { ...s.pins, [commitment]: { ...(s.pins[commitment] ?? {}), ...rec } },
      }));

      return commitment;
    } catch (e: any) {
      set({ uploading: false, error: `DA pin failed: ${String(e?.message ?? e)}` } as Partial<StoreState>);
      return undefined;
    }
  },

  async fetch(commitment: string) {
    const svc = safeSvc('getBlob');
    if (!svc) {
      set({ error: 'DA: getBlob service not available' } as Partial<StoreState>);
      return undefined;
    }
    set({ fetching: true, error: undefined } as Partial<StoreState>);
    try {
      const buf: ArrayBuffer = await (svc as any)(commitment);
      set({ fetching: false } as Partial<StoreState>);
      return buf;
    } catch (e: any) {
      set({ fetching: false, error: `DA fetch failed: ${String(e?.message ?? e)}` } as Partial<StoreState>);
      return undefined;
    }
  },

  async prove(commitment: string, params?: { samples?: number }) {
    const svc = safeSvc('getProof');
    if (!svc) {
      set({ error: 'DA: getProof service not available' } as Partial<StoreState>);
      return undefined;
    }
    try {
      const res: any = await (svc as any)(commitment, params ?? {});
      const proofRec: ProofRecord = {
        commitment: asHex(res?.commitment ?? commitment),
        root: asHex(res?.root),
        ok: Boolean(res?.ok ?? true),
        samples: typeof res?.samples === 'number' ? res.samples : undefined,
        proof: res?.proof,
        verifiedAt: new Date().toISOString(),
      };
      set((s: any) => ({ proofs: { ...s.proofs, [proofRec.commitment]: proofRec } }));
      // enrich existing pin with receipt root if missing
      set((s: any) => {
        const pin = s.pins[proofRec.commitment] as PinRecord | undefined;
        if (!pin) return {};
        if (pin.receipt?.root) return {};
        const receipt: DAReceipt = {
          root: proofRec.root,
          size: pin.size ?? 0,
          ns: pin.ns,
          sig: pin.receipt?.sig,
        };
        return { pins: { ...s.pins, [proofRec.commitment]: { ...pin, receipt } } };
      });
      return proofRec;
    } catch (e: any) {
      set({ error: `DA proof failed: ${String(e?.message ?? e)}` } as Partial<StoreState>);
      return undefined;
    }
  },

  upsertPinMeta(meta) {
    const id = asHex(meta.commitment);
    set((s: any) => {
      const prev: PinRecord | undefined = s.pins[id];
      const merged: PinRecord = {
        commitment: id,
        ns: meta.ns ?? prev?.ns ?? 0,
        size: meta.size ?? prev?.size,
        mime: meta.mime ?? prev?.mime,
        createdAt: prev?.createdAt ?? new Date().toISOString(),
        status: (meta.status ?? prev?.status ?? 'pinned') as PinStatus,
        receipt: meta.receipt ?? prev?.receipt,
        error: meta.error ?? prev?.error,
        progress: meta.progress ?? prev?.progress,
      };
      return { pins: { ...s.pins, [id]: merged } };
    });
  },

  remove(commitment) {
    set((s: any) => {
      const pins = { ...s.pins };
      const proofs = { ...s.proofs };
      delete pins[commitment];
      delete proofs[commitment];
      return { pins, proofs } as Partial<StoreState>;
    });
  },
});

registerSlice<DASlice>(createDASlice);

export default undefined;
