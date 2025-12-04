import type { sha3_256, sha3_512 } from './sha3';

const BLOCK_SIZES: Record<string, number> = {
  'sha3-256': 136,
  'sha3-512': 72,
};

type HashFn = typeof sha3_256 | typeof sha3_512;

type HmacInstance = {
  update: (data: Uint8Array | string) => HmacInstance;
  digest: () => Uint8Array;
};

function utf8ToBytes(data: Uint8Array | ArrayBuffer | ArrayBufferView | ArrayLike<number> | string): Uint8Array {
  if (typeof data === 'string') return new TextEncoder().encode(data);
  if (data instanceof Uint8Array) return data;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  // @ts-ignore Buffer in some environments
  if (typeof Buffer !== 'undefined' && Buffer.isBuffer?.(data)) {
    // @ts-ignore
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  if (typeof (data as any).length === 'number') return Uint8Array.from(data as ArrayLike<number>);
  throw new TypeError('Expected Uint8Array or string');
}

function initPads(hash: HashFn, key: Uint8Array | string) {
  const blockSize = BLOCK_SIZES[(hash as any).algorithm];
  if (!blockSize) throw new Error('Unsupported hash algorithm for HMAC');

  const keyBytes = utf8ToBytes(key);
  const preparedKey = keyBytes.length > blockSize ? hash(keyBytes) : keyBytes;
  const ipad = new Uint8Array(blockSize);
  const opad = new Uint8Array(blockSize);

  for (let i = 0; i < blockSize; i++) {
    const b = preparedKey[i] || 0;
    ipad[i] = b ^ 0x36;
    opad[i] = b ^ 0x5c;
  }
  return { ipad, opad };
}

function makeHmac(hash: HashFn, key: Uint8Array | string, firstData?: Uint8Array | string): HmacInstance {
  const { ipad, opad } = initPads(hash, key);
  const inner = (hash as any).create();
  inner.update(ipad);
  if (firstData !== undefined) inner.update(utf8ToBytes(firstData));

  const inst: HmacInstance = {
    update(data: Uint8Array | string) {
      inner.update(utf8ToBytes(data));
      return inst;
    },
    digest() {
      const innerDigest = inner.digest();
      const outer = (hash as any).create();
      outer.update(opad);
      outer.update(innerDigest);
      return outer.digest();
    },
  };
  return inst;
}

export function hmac(hash: HashFn, key: Uint8Array | string, msg?: Uint8Array | string): Uint8Array | HmacInstance {
  if (msg !== undefined) {
    const h = makeHmac(hash, key, msg);
    return h.digest();
  }
  return makeHmac(hash, key);
}

hmac.create = function create(hash: HashFn, key: Uint8Array | string) {
  return makeHmac(hash, key);
};
