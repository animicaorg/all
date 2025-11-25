/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * MV3-friendly dynamic loader for PQ WASM modules.
 *
 * - Uses Vite's ?url import to get an extension-safe asset URL.
 * - Falls back cleanly (returns null) if WASM cannot be instantiated or
 *   expected exports are missing, allowing higher layers to use dev fallbacks.
 *
 * If a module exposes a C-like ABI:
 *   exports: {
 *     memory: WebAssembly.Memory,
 *     malloc(size: number): number,
 *     free(ptr: number): void,
 *     PK_BYTES: number,
 *     SK_BYTES: number,
 *     SIG_BYTES: number,
 *     keypair_from_seed(seed_ptr: number, seed_len: number, pk_ptr: number, sk_ptr: number): number, // 0=ok
 *     sign(msg_ptr: number, msg_len: number, sk_ptr: number, sig_ptr: number): number,               // 0=ok
 *     verify(msg_ptr: number, msg_len: number, pk_ptr: number, sig_ptr: number): number              // 1=true, 0=false
 *   }
 * we wrap it into a high-level JS API.
 */

// Vite will turn these into fingerprinted extension URLs under MV3 build:
import dilithiumUrl from './dilithium3.wasm?url';
import sphincsUrl from './sphincs_shake_128s.wasm?url';

const DEBUG = (import.meta as any)?.env?.VITE_PQ_WASM_DEBUG === '1';

type LowLevelExports = {
  memory?: WebAssembly.Memory;
  malloc?: (size: number) => number;
  free?: (ptr: number) => void;
  PK_BYTES?: number;
  SK_BYTES?: number;
  SIG_BYTES?: number;
  keypair_from_seed?: (seed_ptr: number, seed_len: number, pk_ptr: number, sk_ptr: number) => number;
  sign?: (msg_ptr: number, msg_len: number, sk_ptr: number, sig_ptr: number) => number;
  verify?: (msg_ptr: number, msg_len: number, pk_ptr: number, sig_ptr: number) => number;
};

export type DilithiumBindings = {
  ALG_ID: 'Dilithium3';
  PK_BYTES: number;
  SK_BYTES: number;
  SIG_BYTES: number;
  keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }>;
  sign(message: Uint8Array, secretKey: Uint8Array): Promise<Uint8Array>;
  verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): Promise<boolean>;
};

export type SphincsBindings = {
  ALG_ID: 'SPHINCS+-SHAKE-128s';
  PK_BYTES: number;
  SK_BYTES: number;
  SIG_BYTES: number;
  keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }>;
  sign(message: Uint8Array, secretKey: Uint8Array): Promise<Uint8Array>;
  verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): Promise<boolean>;
};

async function instantiateWasm(url: string): Promise<WebAssembly.Instance | null> {
  if (typeof WebAssembly === 'undefined') {
    if (DEBUG) console.warn('[pq/wasm] WebAssembly not available');
    return null;
  }
  try {
    const res = await fetch(url, { cache: 'no-cache' });
    if (!res.ok) {
      if (DEBUG) console.warn('[pq/wasm] fetch failed', url, res.status);
      return null;
    }
    const bytes = await res.arrayBuffer();
    const { instance } = await WebAssembly.instantiate(bytes, {});
    return instance;
  } catch (err) {
    if (DEBUG) console.warn('[pq/wasm] instantiate error', err);
    return null;
  }
}

function hasCAbi(e: LowLevelExports): e is Required<LowLevelExports> {
  return !!(
    e &&
    e.memory instanceof WebAssembly.Memory &&
    typeof e.malloc === 'function' &&
    typeof e.free === 'function' &&
    typeof e.keypair_from_seed === 'function' &&
    typeof e.sign === 'function' &&
    typeof e.verify === 'function' &&
    typeof e.PK_BYTES === 'number' &&
    typeof e.SK_BYTES === 'number' &&
    typeof e.SIG_BYTES === 'number'
  );
}

/** Wrap a C-ABI WASM module into ergonomic async functions. */
function wrapCAbi(
  e: Required<LowLevelExports>,
  algId: 'Dilithium3' | 'SPHINCS+-SHAKE-128s'
): DilithiumBindings | SphincsBindings {
  const mem = e.memory;
  const u8 = () => new Uint8Array(mem.buffer);

  function alloc(len: number): number {
    const p = e.malloc(len >>> 0) >>> 0;
    if (!p) throw new Error('wasm malloc failed');
    return p;
  }
  function free(ptr: number) {
    try {
      e.free(ptr >>> 0);
    } catch {
      // ignore best-effort
    }
  }
  function write(ptr: number, data: Uint8Array) {
    u8().set(data, ptr);
  }
  function read(ptr: number, len: number): Uint8Array {
    return u8().subarray(ptr, ptr + len);
  }

  const PK = e.PK_BYTES!;
  const SK = e.SK_BYTES!;
  const SIG = e.SIG_BYTES!;

  return {
    ALG_ID: algId as any,
    PK_BYTES: PK,
    SK_BYTES: SK,
    SIG_BYTES: SIG,
    async keypairFromSeed(seed: Uint8Array) {
      const seedPtr = alloc(seed.length);
      write(seedPtr, seed);
      const pkPtr = alloc(PK);
      const skPtr = alloc(SK);
      try {
        const rc = e.keypair_from_seed(seedPtr, seed.length, pkPtr, skPtr);
        if (rc !== 0) throw new Error(`keypair_from_seed rc=${rc}`);
        const publicKey = new Uint8Array(read(pkPtr, PK));
        const secretKey = new Uint8Array(read(skPtr, SK));
        return { publicKey, secretKey };
      } finally {
        free(seedPtr);
        free(pkPtr);
        free(skPtr);
      }
    },
    async sign(message: Uint8Array, secretKey: Uint8Array) {
      if (secretKey.length !== SK) throw new Error(`secretKey must be ${SK} bytes`);
      const msgPtr = alloc(message.length);
      const skPtr = alloc(SK);
      const sigPtr = alloc(SIG);
      write(msgPtr, message);
      write(skPtr, secretKey);
      try {
        const rc = e.sign(msgPtr, message.length, skPtr, sigPtr);
        if (rc !== 0) throw new Error(`sign rc=${rc}`);
        return new Uint8Array(read(sigPtr, SIG));
      } finally {
        free(msgPtr);
        free(skPtr);
        free(sigPtr);
      }
    },
    async verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array) {
      if (publicKey.length !== PK) return false;
      if (signature.length !== SIG) return false;
      const msgPtr = alloc(message.length);
      const pkPtr = alloc(PK);
      const sigPtr = alloc(SIG);
      write(msgPtr, message);
      write(pkPtr, publicKey);
      write(sigPtr, signature);
      try {
        const rc = e.verify(msgPtr, message.length, pkPtr, sigPtr);
        return rc === 1;
      } finally {
        free(msgPtr);
        free(pkPtr);
        free(sigPtr);
      }
    },
  };
}

/**
 * Try to load Dilithium3 WASM. Returns null if unavailable or malformed.
 * Higher layers should feature-gate and/or use dev fallback paths.
 */
export async function loadDilithium3(): Promise<DilithiumBindings | null> {
  const inst = await instantiateWasm(dilithiumUrl);
  if (!inst) return null;
  const ex = inst.exports as unknown as LowLevelExports;
  if (!hasCAbi(ex)) {
    if (DEBUG) console.warn('[pq/wasm] Dilithium3 exports missing expected C ABI, falling back');
    return null;
  }
  try {
    return wrapCAbi(ex as Required<LowLevelExports>, 'Dilithium3') as DilithiumBindings;
  } catch (err) {
    if (DEBUG) console.warn('[pq/wasm] Dilithium3 wrap error', err);
    return null;
  }
}

/**
 * Try to load SPHINCS+-SHAKE-128s WASM. Returns null if unavailable or malformed.
 */
export async function loadSphincsShake128s(): Promise<SphincsBindings | null> {
  const inst = await instantiateWasm(sphincsUrl);
  if (!inst) return null;
  const ex = inst.exports as unknown as LowLevelExports;
  if (!hasCAbi(ex)) {
    if (DEBUG) console.warn('[pq/wasm] SPHINCS exports missing expected C ABI, falling back');
    return null;
  }
  try {
    return wrapCAbi(ex as Required<LowLevelExports>, 'SPHINCS+-SHAKE-128s') as SphincsBindings;
  } catch (err) {
    if (DEBUG) console.warn('[pq/wasm] SPHINCS wrap error', err);
    return null;
  }
}
