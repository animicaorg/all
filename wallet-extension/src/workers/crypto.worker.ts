/* eslint-disable no-restricted-globals */
/**
 * crypto.worker.ts
 * Off-thread PQ crypto for MV3.
 * - Dilithium3 & SPHINCS+-SHAKE-128s via WASM wrappers
 * - HKDF-SHA3-256 for deterministic seed expansion
 * - Optional random byte generation using WebCrypto (non-deterministic)
 *
 * Message protocol:
 *  Request:
 *    { id, op, ...args }
 *  Response:
 *    { id, ok: true, result } | { id, ok: false, error }
 *
 * Supported ops:
 *  - "dl3.keygen"         { seed?: Uint8Array }
 *  - "dl3.sign"           { sk: Uint8Array, msg: Uint8Array }
 *  - "dl3.verify"         { pk: Uint8Array, msg: Uint8Array, sig: Uint8Array }
 *  - "sphincs.keygen"     { seed?: Uint8Array }
 *  - "sphincs.sign"       { sk: Uint8Array, msg: Uint8Array }
 *  - "sphincs.verify"     { pk: Uint8Array, msg: Uint8Array, sig: Uint8Array }
 *  - "hkdf"               { ikm: Uint8Array, salt?: Uint8Array, info?: Uint8Array, length: number }
 *  - "rng"                { length: number }
 *  - "wasm.info"          {}
 */

export {};

type U8 = Uint8Array;

type Req =
  | { id: string; op: 'dl3.keygen'; seed?: U8 }
  | { id: string; op: 'dl3.sign'; sk: U8; msg: U8 }
  | { id: string; op: 'dl3.verify'; pk: U8; msg: U8; sig: U8 }
  | { id: string; op: 'sphincs.keygen'; seed?: U8 }
  | { id: string; op: 'sphincs.sign'; sk: U8; msg: U8 }
  | { id: string; op: 'sphincs.verify'; pk: U8; msg: U8; sig: U8 }
  | { id: string; op: 'hkdf'; ikm: U8; salt?: U8; info?: U8; length: number }
  | { id: string; op: 'rng'; length: number }
  | { id: string; op: 'wasm.info' };

type ResOk = { id: string; ok: true; result: unknown; transfer?: Transferable[] };
type ResErr = { id: string; ok: false; error: string };

declare const self: DedicatedWorkerGlobalScope;

function toU8(x: ArrayBuffer | U8): U8 {
  return x instanceof Uint8Array ? x : new Uint8Array(x);
}

function ok(id: string, result: unknown, transfer?: Transferable[]): void {
  const msg: ResOk = { id, ok: true, result, transfer };
  if (transfer && transfer.length) {
    // @ts-expect-error: TS doesn't know we're in a worker
    self.postMessage(msg, transfer);
  } else {
    // @ts-expect-error: TS doesn't know we're in a worker
    self.postMessage(msg);
  }
}

function err(id: string, e: unknown): void {
  const message =
    e instanceof Error ? `${e.name}: ${e.message}` : typeof e === 'string' ? e : JSON.stringify(e);
  const msg: ResErr = { id, ok: false, error: message };
  // @ts-expect-error: TS doesn't know we're in a worker
  self.postMessage(msg);
}

/* Lazy-loaded PQ backends (WASM-enabled) */
type DL3 = {
  keypair(seed?: U8): Promise<{ pk: U8; sk: U8 }>;
  sign(sk: U8, msg: U8): Promise<U8>;
  verify(pk: U8, msg: U8, sig: U8): Promise<boolean>;
};

type SPX = {
  keypair(seed?: U8): Promise<{ pk: U8; sk: U8 }>;
  sign(sk: U8, msg: U8): Promise<U8>;
  verify(pk: U8, msg: U8, sig: U8): Promise<boolean>;
};

let dl3: DL3 | null = null;
let spx: SPX | null = null;

async function getDL3(): Promise<DL3> {
  if (!dl3) {
    const mod = await import('../background/pq/dilithium3');
    // expected default export is a loader or named loadDilithium3()
    dl3 =
      (mod.loadDilithium3 ? await mod.loadDilithium3() : await (mod.default as () => Promise<DL3>)());
  }
  return dl3!;
}

async function getSPX(): Promise<SPX> {
  if (!spx) {
    const mod = await import('../background/pq/sphincs_shake_128s');
    spx =
      (mod.loadSphincsShake128s
        ? await mod.loadSphincsShake128s()
        : await (mod.default as () => Promise<SPX>)());
  }
  return spx!;
}

async function hkdfSHA3(ikm: U8, salt?: U8, info?: U8, length = 32): Promise<U8> {
  const { hkdfSha3_256 } = await import('../background/pq/hkdf');
  return hkdfSha3_256(ikm, { salt, info, length });
}

function rngBytes(length: number): U8 {
  const out = new Uint8Array(length);
  // WebCrypto is available in workers
  crypto.getRandomValues(out);
  return out;
}

async function handle(msg: Req): Promise<void> {
  const { id, op } = msg;
  try {
    switch (op) {
      case 'dl3.keygen': {
        const m = await getDL3();
        const { pk, sk } = await m.keypair(msg.seed ? toU8(msg.seed) : undefined);
        // Transfer buffers to avoid copies
        ok(id, { pk, sk }, [pk.buffer, sk.buffer]);
        return;
      }
      case 'dl3.sign': {
        const m = await getDL3();
        const sig = await m.sign(toU8(msg.sk), toU8(msg.msg));
        ok(id, { sig }, [sig.buffer]);
        return;
      }
      case 'dl3.verify': {
        const m = await getDL3();
        const valid = await m.verify(toU8(msg.pk), toU8(msg.msg), toU8(msg.sig));
        ok(id, { valid });
        return;
      }
      case 'sphincs.keygen': {
        const m = await getSPX();
        const { pk, sk } = await m.keypair(msg.seed ? toU8(msg.seed) : undefined);
        ok(id, { pk, sk }, [pk.buffer, sk.buffer]);
        return;
      }
      case 'sphincs.sign': {
        const m = await getSPX();
        const sig = await m.sign(toU8(msg.sk), toU8(msg.msg));
        ok(id, { sig }, [sig.buffer]);
        return;
      }
      case 'sphincs.verify': {
        const m = await getSPX();
        const valid = await m.verify(toU8(msg.pk), toU8(msg.msg), toU8(msg.sig));
        ok(id, { valid });
        return;
      }
      case 'hkdf': {
        const out = await hkdfSHA3(toU8(msg.ikm), msg.salt ? toU8(msg.salt) : undefined, msg.info ? toU8(msg.info) : undefined, msg.length);
        ok(id, { ok: true, bytes: out }, [out.buffer]);
        return;
      }
      case 'rng': {
        const out = rngBytes(msg.length);
        ok(id, { bytes: out }, [out.buffer]);
        return;
      }
      case 'wasm.info': {
        // Ask the loader for features if available; otherwise return minimal info
        try {
          const { getWasmFeatures } = await import('../background/pq/wasm/loader');
          const info = await getWasmFeatures?.();
          ok(id, { wasm: true, ...info });
        } catch {
          ok(id, { wasm: true });
        }
        return;
      }
      default:
        throw new Error(`Unsupported op: ${op as string}`);
    }
  } catch (e) {
    err(id, e);
  }
}

// MV3 worker listener
self.addEventListener('message', (ev: MessageEvent<Req>) => {
  // Fire and forget; each handler responds with matching id
  void handle(ev.data);
});
