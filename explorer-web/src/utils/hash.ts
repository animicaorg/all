import { keccak_256 } from '@noble/hashes/sha3';

/**
 * Keccak-256 hash of input bytes, returned as 0x-prefixed hex string.
 * Used for computing contract event signatures and other Ethereum-style hashes.
 */
export function keccak256Hex(data: Uint8Array | string): string {
  const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : data;
  const hash = keccak_256(bytes);
  return '0x' + Array.from(hash).map((b: number) => b.toString(16).padStart(2, '0')).join('');
}

export function shortHash(h: string, left = 6, right = 4): string {
  if (!h) return "";
  const s = h.startsWith("0x") ? h.slice(2) : h;
  if (s.length <= left + right) return "0x" + s;
  return "0x" + s.slice(0, left) + "…"+ s.slice(-right);
}
