import { ml_dsa65 } from '@noble/post-quantum/ml-dsa';
import { sha3_256 } from '@noble/hashes/sha3';
import { decodeAnimicaAddress } from './address';
import { ML_DSA_65_ALG_ID, SIGN_MESSAGE_DOMAIN } from './config';

// Verify a wallet-login signature the way the node does, with NO forgeable shortcuts:
//   1. address must be ml_dsa_65 (0x1003); reject stub/stranded schemes.
//   2. the presented pubkey must bind to the address: sha3_256(pubkey) == address digest.
//   3. ml_dsa65.verify(sig, UTF8("animica:signMessage:" + message), pubkey) in pure mode.

function hexToBytes(hex: string): Uint8Array {
  const h = hex.startsWith('0x') ? hex.slice(2) : hex;
  if (h.length % 2 !== 0) throw new Error('odd hex length');
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i += 1) out[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function eqBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
}

export interface VerifyInput {
  address: string;   // anim1...
  message: string;   // the challenge string that was signed
  signatureHex: string; // 0x... ml_dsa_65 detached signature
  publicKeyHex: string; // 0x... ml_dsa_65 public key (1952 bytes)
}

export function verifyWalletLogin(input: VerifyInput): { ok: boolean; reason?: string } {
  let decoded;
  try {
    decoded = decodeAnimicaAddress(input.address);
  } catch (e: any) {
    return { ok: false, reason: `bad address: ${e.message}` };
  }
  if (decoded.algId !== ML_DSA_65_ALG_ID) {
    return { ok: false, reason: `unsupported scheme 0x${decoded.algId.toString(16)} (only ml_dsa_65 accepted)` };
  }

  let pk: Uint8Array, sig: Uint8Array;
  try {
    pk = hexToBytes(input.publicKeyHex);
    sig = hexToBytes(input.signatureHex);
  } catch (e: any) {
    return { ok: false, reason: `bad hex: ${e.message}` };
  }
  if (pk.length !== 1952) return { ok: false, reason: `bad pubkey length ${pk.length}` };

  // Address binding: the pubkey must hash to the address digest, else anyone could present
  // a valid signature under their OWN key and claim someone else's address.
  const pkDigest = sha3_256(pk);
  if (!eqBytes(pkDigest, decoded.digest)) {
    return { ok: false, reason: 'pubkey does not match address' };
  }

  const msgBytes = new TextEncoder().encode(SIGN_MESSAGE_DOMAIN + input.message);
  let ok = false;
  try {
    // @noble/post-quantum signature is verify(publicKey, message, signature).
    ok = ml_dsa65.verify(pk, msgBytes, sig);
  } catch (e: any) {
    return { ok: false, reason: `verify error: ${e.message}` };
  }
  return ok ? { ok: true } : { ok: false, reason: 'signature verification failed' };
}
