// Minimal SHA3-256/SHA3-512 implementation using Keccak-f[1600].
// This avoids Node.js builtins so it can run in MV3 service workers.

const RC: bigint[] = [
  0x0000000000000001n, 0x0000000000008082n, 0x800000000000808an, 0x8000000080008000n,
  0x000000000000808bn, 0x0000000080000001n, 0x8000000080008081n, 0x8000000000008009n,
  0x000000000000008an, 0x0000000000000088n, 0x0000000080008009n, 0x000000008000000an,
  0x000000008000808bn, 0x800000000000008bn, 0x8000000000008089n, 0x8000000000008003n,
  0x8000000000008002n, 0x8000000000000080n, 0x000000000000800an, 0x800000008000000an,
  0x8000000080008081n, 0x8000000000008080n, 0x0000000080000001n, 0x8000000080008008n,
];

const ROT: number[][] = [
  [0, 36, 3, 41, 18],
  [1, 44, 10, 45, 2],
  [62, 6, 43, 15, 61],
  [28, 55, 25, 21, 56],
  [27, 20, 39, 8, 14],
];

const MASK64 = 0xffffffffffffffffn;

function rotl(x: bigint, n: number): bigint {
  const s = BigInt(n % 64);
  return ((x << s) | (x >> (64n - s))) & MASK64;
}

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

function laneToBytes(value: bigint, out: Uint8Array, offset: number) {
  for (let i = 0; i < 8; i++) {
    out[offset + i] = Number((value >> BigInt(8 * i)) & 0xffn);
  }
}

function bytesToLane(bytes: Uint8Array, offset: number): bigint {
  let value = 0n;
  for (let i = 0; i < 8; i++) {
    value |= BigInt(bytes[offset + i]) << BigInt(8 * i);
  }
  return value;
}

function keccakF1600(state: bigint[]) {
  const c = new Array<bigint>(5);
  const d = new Array<bigint>(5);
  const b = new Array<bigint>(25);

  for (let round = 0; round < 24; round++) {
    // Theta
    for (let x = 0; x < 5; x++) {
      c[x] = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20];
    }
    for (let x = 0; x < 5; x++) {
      d[x] = c[(x + 4) % 5] ^ rotl(c[(x + 1) % 5], 1);
    }
    for (let i = 0; i < 25; i++) state[i] ^= d[i % 5];

    // Rho + Pi
    for (let x = 0; x < 5; x++) {
      for (let y = 0; y < 5; y++) {
        const idx = x + 5 * y;
        const newX = y;
        const newY = (2 * x + 3 * y) % 5;
        b[newX + 5 * newY] = rotl(state[idx], ROT[y][x]);
      }
    }

    // Chi
    for (let x = 0; x < 5; x++) {
      for (let y = 0; y < 5; y++) {
        const idx = x + 5 * y;
        state[idx] = b[idx] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]);
      }
    }

    // Iota
    state[0] ^= RC[round];
  }
}

function absorbBlock(state: bigint[], block: Uint8Array, rateBytes: number) {
  for (let offset = 0, lane = 0; offset < rateBytes; offset += 8, lane++) {
    state[lane] ^= bytesToLane(block, offset);
  }
  keccakF1600(state);
}

function squeeze(state: bigint[], rateBytes: number, outputLength: number): Uint8Array {
  const out = new Uint8Array(outputLength);
  let outPos = 0;
  const block = new Uint8Array(rateBytes);

  while (outPos < outputLength) {
    for (let offset = 0, lane = 0; lane < rateBytes / 8; lane++, offset += 8) {
      laneToBytes(state[lane], block, offset);
    }
    const take = Math.min(rateBytes, outputLength - outPos);
    out.set(block.subarray(0, take), outPos);
    outPos += take;
    if (outPos < outputLength) keccakF1600(state);
  }
  return out;
}

class Sha3 {
  private state = new Array<bigint>(25).fill(0n);
  private buffer: Uint8Array;
  private pos = 0;
  private finished = false;

  constructor(private rateBytes: number, private suffix: number, private outputLen: number) {
    this.buffer = new Uint8Array(rateBytes);
  }

  update(data: Uint8Array | string): this {
    const bytes = utf8ToBytes(data);
    let offset = 0;

    while (offset < bytes.length) {
      const space = this.rateBytes - this.pos;
      const take = Math.min(space, bytes.length - offset);
      this.buffer.set(bytes.subarray(offset, offset + take), this.pos);
      this.pos += take;
      offset += take;

      if (this.pos === this.rateBytes) {
        absorbBlock(this.state, this.buffer, this.rateBytes);
        this.buffer.fill(0);
        this.pos = 0;
      }
    }
    return this;
  }

  private finish() {
    if (this.finished) return;
    this.buffer[this.pos] ^= this.suffix;
    this.buffer[this.rateBytes - 1] ^= 0x80;
    absorbBlock(this.state, this.buffer, this.rateBytes);
    this.buffer.fill(0);
    this.pos = 0;
    this.finished = true;
  }

  digest(): Uint8Array {
    this.finish();
    return squeeze(this.state.slice(), this.rateBytes, this.outputLen);
  }
}

type HashFactory = ((msg: Uint8Array | string) => Uint8Array) & {
  create: () => { update: (data: Uint8Array | string) => any; digest: () => Uint8Array };
  algorithm: string;
};

function makeHash(bits: 256 | 512): HashFactory {
  const rateBytes = bits === 256 ? 136 : 72;
  const suffix = 0x06;
  const outputLen = bits / 8;

  const fn = ((msg: Uint8Array | string) => {
    const h = new Sha3(rateBytes, suffix, outputLen);
    h.update(msg);
    return h.digest();
  }) as HashFactory;

  fn.algorithm = `sha3-${bits}`;
  fn.create = () => {
    const h = new Sha3(rateBytes, suffix, outputLen);
    return {
      update(data: Uint8Array | string) {
        h.update(data);
        return this;
      },
      digest() {
        return h.digest();
      },
    };
  };
  return fn;
}

export const sha3_256 = makeHash(256);
export const sha3_512 = makeHash(512);
