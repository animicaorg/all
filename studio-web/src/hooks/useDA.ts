import { useCallback, useMemo, useRef, useState } from "react";
import * as DA from "../services/da";

/**
 * useDA — convenience React hook for Data Availability actions:
 *  - pin (post) a blob -> commitment (NMT root)
 *  - get blob bytes/text/json by commitment
 *  - get availability proof (optional)
 *
 * Depends on ../services/da, which is expected to export:
 *   - postBlob(ns: number, data: Uint8Array, opts?: { mime?: string }): Promise<PinResult>
 *   - getBlob(commitment: string): Promise<Uint8Array>
 *   - getProof(commitment: string, opts?: { indices?: number[], samples?: number }): Promise<ProofResult>
 */

export interface PinResult {
  commitment: string;     // hex commitment (NMT root)
  size: number;           // bytes
  namespace: number;      // numeric namespace id
  mime?: string;
  receipt?: unknown;      // DA receipt (if service returns one)
}

export interface ProofResult {
  commitment: string;
  root: string;
  samples: unknown[];
  verified?: boolean;
  [k: string]: unknown;
}

export interface BlobMeta extends PinResult {
  createdAt: string; // ISO time saved when we pinned
}

export interface UseDA {
  /** Map of known pins: commitment -> meta */
  pins: Record<string, BlobMeta>;
  /** True if any pin/get/proof is in-flight */
  busy: boolean;
  /** Last info / error messages (non-throwing) */
  lastMessage: string | null;
  lastError: string | null;

  /** Pin (post) a blob to DA, normalizing inputs to bytes. */
  pinBlob: (
    params:
      | { namespace: number; data: Uint8Array | ArrayBuffer | Blob | File; mime?: string }
      | { namespace: number; data: string; encoding?: "utf8" | "hex" | "base64"; mime?: string }
      | { namespace: number; json: unknown; mime?: string }
  ) => Promise<PinResult>;

  /** Fetch blob bytes for a commitment. */
  getBlob: (commitment: string) => Promise<Uint8Array>;

  /** Fetch as UTF-8 string. */
  getBlobText: (commitment: string) => Promise<string>;

  /** Fetch and JSON.parse it safely. */
  getBlobJson: <T = unknown>(commitment: string) => Promise<T>;

  /** Retrieve an availability proof (indices or sample count optional). */
  getProof: (commitment: string, opts?: { indices?: number[]; samples?: number }) => Promise<ProofResult>;

  /** Remove a pin entry (local state only). If omitted, clears all. */
  clear: (commitment?: string) => void;

  /** Reset state (clears pins and messages, aborts nothing). */
  reset: () => void;
}

/* ---------------------------------- Utils --------------------------------- */

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

function isHex(s: string): boolean {
  const v = s.startsWith("0x") ? s.slice(2) : s;
  return v.length % 2 === 0 && /^[0-9a-fA-F]+$/.test(v);
}

function hexToBytes(hex: string): Uint8Array {
  const v = hex.startsWith("0x") ? hex.slice(2) : hex;
  const out = new Uint8Array(v.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(v.substr(i * 2, 2), 16);
  }
  return out;
}

function b64ToBytes(b64: string): Uint8Array {
  // atob is available in browsers; in Node during tests, use Buffer
  if (typeof atob === "function") {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const buf = Buffer.from(b64, "base64");
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
}

async function normalizeToBytes(
  input:
    | { data: Uint8Array | ArrayBuffer | Blob | File; mime?: string }
    | { data: string; encoding?: "utf8" | "hex" | "base64"; mime?: string }
    | { json: unknown; mime?: string }
): Promise<{ bytes: Uint8Array; mime?: string }> {
  // JSON path
  if ("json" in input) {
    const s = JSON.stringify(input.json);
    return { bytes: textEncoder.encode(s), mime: input.mime ?? "application/json" };
  }

  // String path
  if (typeof (input as any).data === "string") {
    const { data, encoding, mime } = input as { data: string; encoding?: "utf8" | "hex" | "base64"; mime?: string };
    if (encoding === "hex" || (encoding === undefined && isHex(data))) {
      return { bytes: hexToBytes(data), mime: mime ?? "application/octet-stream" };
    }
    if (encoding === "base64") {
      return { bytes: b64ToBytes(data), mime: mime ?? "application/octet-stream" };
    }
    return { bytes: textEncoder.encode(data), mime: mime ?? "text/plain;charset=utf-8" };
  }

  // Binary-like path
  const { data, mime } = input as { data: Uint8Array | ArrayBuffer | Blob | File; mime?: string };
  if (data instanceof Uint8Array) return { bytes: data, mime };
  if (data instanceof ArrayBuffer) return { bytes: new Uint8Array(data), mime };
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    const ab = await data.arrayBuffer();
    return { bytes: new Uint8Array(ab), mime: mime ?? (data as Blob).type || undefined };
  }
  // Fallback (shouldn't hit)
  return { bytes: new Uint8Array(0), mime };
}

/* ---------------------------------- Hook ---------------------------------- */

export function useDA(): UseDA {
  const [pins, setPins] = useState<Record<string, BlobMeta>>({});
  const [busyCount, setBusyCount] = useState(0);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const incBusy = useRef(() => setBusyCount((n) => n + 1));
  const decBusy = useRef(() => setBusyCount((n) => Math.max(0, n - 1)));

  const busy = useMemo(() => busyCount > 0, [busyCount]);

  const pinBlob: UseDA["pinBlob"] = useCallback(
    async (params) => {
      setLastError(null);
      setLastMessage("Pinning blob to DA…");
      incBusy.current();
      try {
        const { namespace } = params as any;
        if (typeof namespace !== "number" || !Number.isFinite(namespace) || namespace < 0) {
          throw new Error("namespace must be a non-negative number");
        }

        const norm = await normalizeToBytes(
          "json" in params
            ? { json: params.json, mime: params.mime }
            : "data" in params
            ? { data: (params as any).data, mime: (params as any).mime, encoding: (params as any).encoding }
            : // exhaustive guard
              (params as any)
        );

        if (norm.bytes.byteLength === 0) {
          throw new Error("Refusing to pin empty payload");
        }

        const res = (await DA.postBlob(namespace, norm.bytes, { mime: norm.mime })) as PinResult;
        const createdAt = new Date().toISOString();
        const meta: BlobMeta = { ...res, mime: norm.mime ?? res.mime, createdAt };

        setPins((p) => ({ ...p, [res.commitment]: meta }));
        setLastMessage(`Pinned ${res.size} bytes (ns=${res.namespace}) as ${res.commitment.slice(0, 18)}…`);
        return res;
      } catch (e: any) {
        const msg = e?.message ?? String(e);
        setLastError(msg);
        throw e;
      } finally {
        decBusy.current();
      }
    },
    []
  );

  const getBlob: UseDA["getBlob"] = useCallback(async (commitment: string) => {
    setLastError(null);
    setLastMessage(`Fetching blob ${commitment.slice(0, 18)}…`);
    incBusy.current();
    try {
      const bytes = (await DA.getBlob(commitment)) as Uint8Array;
      setLastMessage(`Fetched ${bytes.byteLength} bytes from DA`);
      return bytes;
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      setLastError(msg);
      throw e;
    } finally {
      decBusy.current();
    }
  }, []);

  const getBlobText: UseDA["getBlobText"] = useCallback(async (commitment: string) => {
    const bytes = await getBlob(commitment);
    return textDecoder.decode(bytes);
  }, [getBlob]);

  const getBlobJson: UseDA["getBlobJson"] = useCallback(async <T,>(commitment: string) => {
    const s = await getBlobText(commitment);
    try {
      return JSON.parse(s) as T;
    } catch (e: any) {
      const msg = `Failed to parse JSON for ${commitment}: ${e?.message ?? e}`;
      setLastError(msg);
      throw new Error(msg);
    }
  }, [getBlobText]);

  const getProof: UseDA["getProof"] = useCallback(async (commitment: string, opts?: { indices?: number[]; samples?: number }) => {
    setLastError(null);
    setLastMessage(`Requesting availability proof for ${commitment.slice(0, 18)}…`);
    incBusy.current();
    try {
      const proof = (await DA.getProof(commitment, opts)) as ProofResult;
      setLastMessage(`Proof contains ${Array.isArray((proof as any).samples) ? (proof as any).samples.length : 0} samples`);
      return proof;
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      setLastError(msg);
      throw e;
    } finally {
      decBusy.current();
    }
  }, []);

  const clear: UseDA["clear"] = useCallback((commitment?: string) => {
    if (!commitment) {
      setPins({});
      return;
    }
    setPins((p) => {
      const n = { ...p };
      delete n[commitment];
      return n;
    });
  }, []);

  const reset: UseDA["reset"] = useCallback(() => {
    setPins({});
    setLastMessage(null);
    setLastError(null);
    setBusyCount(0);
  }, []);

  return {
    pins,
    busy,
    lastMessage,
    lastError,
    pinBlob,
    getBlob,
    getBlobText,
    getBlobJson,
    getProof,
    clear,
    reset,
  };
}

export default useDA;
