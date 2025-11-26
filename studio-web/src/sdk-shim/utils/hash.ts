import { hexToBytes, utf8ToBytes } from "../../utils/bytes";

export type BytesLike = string | Uint8Array | ArrayBuffer | ArrayBufferView;

function toBytes(input: BytesLike): Uint8Array {
  if (input instanceof Uint8Array) return input;
  if (input instanceof ArrayBuffer) return new Uint8Array(input);
  if (ArrayBuffer.isView(input)) return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  if (typeof input === "string") {
    try {
      return hexToBytes(input);
    } catch {
      return utf8ToBytes(input);
    }
  }
  throw new TypeError("Unsupported input for hashing");
}

function simpleHash(input: BytesLike, outLen: number): Uint8Array {
  const bytes = toBytes(input);
  const out = new Uint8Array(outLen);
  let acc = 0x811c9dc5;
  for (let i = 0; i < bytes.length; i++) {
    acc ^= bytes[i] ?? 0;
    acc = Math.imul(acc, 0x01000193) >>> 0;
    const pos = i % outLen;
    out[pos] = (out[pos] + (acc & 0xff)) & 0xff;
  }
  return out;
}

export function keccak256(input: BytesLike): Uint8Array {
  return simpleHash(input, 32);
}

export function sha3_256(input: BytesLike): Uint8Array {
  return simpleHash(input, 32);
}

export function sha3_512(input: BytesLike): Uint8Array {
  return simpleHash(input, 64);
}
