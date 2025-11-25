/**
 * PQ signing helpers for transactions.
 *
 * - Uses domain-separated sign-bytes from ./encode (includes chainId + kind).
 * - Supports Dilithium3 and SPHINCS+-SHAKE-128s (feature-gated WASM under the hood).
 * - Produces a canonical Signature object that matches tx/types.ts.
 */

import type { Bytes, SigAlgo, Signature, TxBody } from "./types";
import { buildSignBytes } from "./encode";

// PQ alg wrappers (each provides sign/verify and key sizes)
import * as dilithium3 from "../pq/dilithium3";
import * as sphincs from "../pq/sphincs_shake_128s";

/** Generic PQ signer contract supplied by the keyring. */
export interface PqSigner {
  alg: SigAlgo;                  // 'dilithium3' | 'sphincs_shake_128s'
  publicKey: Bytes;              // raw bytes
  sign(message: Bytes): Promise<Bytes>;
}

/** Build canonical Signature object from raw PQ signature + pubkey. */
function makeSignature(alg: SigAlgo, pubkey: Bytes, sig: Bytes): Signature {
  if (!(pubkey instanceof Uint8Array)) throw new Error("pubkey must be Uint8Array");
  if (!(sig instanceof Uint8Array)) throw new Error("sig must be Uint8Array");
  return { alg, pubkey, sig };
}

/**
 * Sign a TxBody using a provided PQ signer.
 * Returns an envelope-like object { body, signature }.
 */
export async function signTxBody(
  body: TxBody,
  signer: PqSigner
): Promise<{ body: TxBody; signature: Signature }> {
  if (!body) throw new Error("TxBody required");
  if (!signer) throw new Error("signer required");

  // Domain-separated bytes; includes chainId + kind (transfer/call/deploy) deterministically.
  const signBytes = buildSignBytes(body);

  // Sign with the active PQ algorithm.
  const sig = await signer.sign(signBytes);
  const signature = makeSignature(signer.alg, signer.publicKey, sig);
  return { body, signature };
}

/**
 * Verify a signed tx envelope against its body using embedded signature.
 * Useful for sanity checks in background or tests.
 */
export async function verifyTxSignature(
  env: { body: TxBody; signature: Signature }
): Promise<boolean> {
  const { body, signature } = env;
  const msg = buildSignBytes(body);

  switch (signature.alg) {
    case "dilithium3":
      return dilithium3.verify(msg, signature.sig, signature.publicKey ?? signature.pubkey);

    case "sphincs_shake_128s":
      return sphincs.verify(msg, signature.sig, signature.publicKey ?? signature.pubkey);

    default:
      // Exhaustiveness guard; TS won't let unknown algs through if SigAlgo is maintained.
      throw new Error(`unsupported signature algorithm: ${(signature as any).alg}`);
  }
}

/**
 * Convenience guard: ensure the signature's public key matches the expected account public key.
 * This is optional — address binding typically happens at submit-time by the node,
 * but checking early helps catch key mismatch bugs in the extension.
 */
export function assertSignaturePubkey(
  expectedPubkey: Bytes,
  signature: Signature
): void {
  const a = expectedPubkey, b = signature.publicKey ?? signature.pubkey;
  if (!(a instanceof Uint8Array) || !(b instanceof Uint8Array)) {
    throw new Error("pubkeys must be Uint8Array");
  }
  if (a.length !== b.length) {
    throw new Error("pubkey length mismatch");
  }
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) throw new Error("pubkey mismatch");
  }
}

/* ------------------------------ Type Helpers ------------------------------ */

/** Narrow a runtime string to SigAlgo (throws if unsupported). */
export function asSigAlgo(s: string): SigAlgo {
  if (s === "dilithium3" || s === "sphincs_shake_128s") return s;
  throw new Error(`unsupported SigAlgo: ${s}`);
}

/* ------------------------------- Re-exports ------------------------------- */
/**
 * Expose alg metadata so UI can present sizes or labels without importing PQ modules.
 */
export const PQ_ALG_INFO = {
  dilithium3: {
    name: "Dilithium3",
    sigLen: dilithium3.SIG_LEN,
    pkLen: dilithium3.PK_LEN,
  },
  sphincs_shake_128s: {
    name: "SPHINCS+ SHAKE-128s",
    sigLen: sphincs.SIG_LEN,
    pkLen: sphincs.PK_LEN,
  },
} as const;

