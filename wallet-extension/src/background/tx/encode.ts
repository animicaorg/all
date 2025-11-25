/**
 * Canonical CBOR encoding for transactions and sign-bytes domain separation.
 *
 * Notes
 * -----
 * • We encode a *signable* view of the TxBody using a stable, canonical CBOR.
 * • Amount-like values remain as decimal strings (no JS number widening).
 * • Bytes are encoded as CBOR byte strings (Uint8Array).
 * • SignBytes = CBOR.encode(["animica:tx:sign/v1", <SignableBody>])
 * • Envelope = CBOR.encode(["animica:tx:v1", <SignableBody>, <Signature>])
 *
 * This mirrors core/types/tx.py shapes at a semantic level. Exact field ordering
 * is enforced by canonical CBOR (sorted map keys) and stable array positions.
 */

import { encodeCanonical } from "../../utils/cbor";
import { concatBytes, toHex } from "../../utils/bytes";
import type {
  TxBody,
  TxTransfer,
  TxCall,
  TxDeploy,
  SignedTx,
  TxSignature,
  Bytes,
} from "./types";
import { sha3_256 } from "@noble/hashes/sha3";

/* --------------------------------- domains -------------------------------- */

export const SIGN_DOMAIN = "animica:tx:sign/v1";
export const TX_ENVELOPE_TAG = "animica:tx:v1";

/* --------------------------- signable body shapes -------------------------- */

/**
 * A SignableBody is a minimal, JSON/CBOR-friendly projection of TxBody that
 * preserves semantic meaning while avoiding JS number pitfalls.
 * Keys are intentionally lowercase and stable for canonical map ordering.
 */
type SignableBody =
  | {
      kind: "transfer";
      chainId: number;
      from: string;
      nonce: number;
      gasLimit: number;
      maxFee: string;
      to: string;
      value: string;
      memo?: string;
    }
  | {
      kind: "call";
      chainId: number;
      from: string;
      nonce: number;
      gasLimit: number;
      maxFee: string;
      to: string;
      data: Bytes;
      value?: string;
      memo?: string;
    }
  | {
      kind: "deploy";
      chainId: number;
      from: string;
      nonce: number;
      gasLimit: number;
      maxFee: string;
      code: Bytes;
      init?: Bytes;
      memo?: string;
    };

/** Internal: convert TxBody to a SignableBody, dropping undefineds. */
function toSignable(body: TxBody): SignableBody {
  const base = {
    chainId: body.chainId,
    from: body.from,
    nonce: body.nonce,
    gasLimit: body.gasLimit,
    maxFee: body.maxFee,
    memo: body.memo,
  };

  switch (body.kind) {
    case "transfer": {
      const b = body as TxTransfer;
      const out: SignableBody = {
        kind: "transfer",
        ...base,
        to: b.to,
        value: b.value,
      };
      if (base.memo == null) delete (out as any).memo;
      return out;
    }
    case "call": {
      const b = body as TxCall;
      const out: SignableBody = {
        kind: "call",
        ...base,
        to: b.to,
        data: b.data,
      };
      if (b.value != null) (out as any).value = b.value;
      if (base.memo == null) delete (out as any).memo;
      return out;
    }
    case "deploy": {
      const b = body as TxDeploy;
      const out: SignableBody = {
        kind: "deploy",
        ...base,
        code: b.code,
      };
      if (b.init != null) (out as any).init = b.init;
      if (base.memo == null) delete (out as any).memo;
      return out;
    }
  }
}

/* ------------------------------- encoders --------------------------------- */

/** Encode only the TxBody (signable view) to canonical CBOR bytes. */
export function encodeTxBody(body: TxBody): Uint8Array {
  const signable = toSignable(body);
  return encodeCanonical(signable);
}

/** Build domain-separated SignBytes = CBOR([SIGN_DOMAIN, <SignableBody>]). */
export function buildSignBytes(body: TxBody): Uint8Array {
  const signable = toSignable(body);
  return encodeCanonical([SIGN_DOMAIN, signable]);
}

/**
 * Compute deterministic txHash = sha3_256(SignBytes).
 * Returned as 0x-prefixed hex string.
 */
export function computeTxHash(signBytes: Uint8Array): `0x${string}` {
  const digest = sha3_256.create().update(signBytes).digest();
  return ("0x" + toHex(digest)) as `0x${string}`;
}

/** Encode a full signed tx envelope for submission. */
export function encodeSignedTx(body: TxBody, signature: TxSignature): Uint8Array {
  const signable = toSignable(body);
  const sig = {
    scheme: signature.scheme,
    pubkey: signature.pubkey,
    sig: signature.sig,
  };
  // Envelope: ["animica:tx:v1", {body}, {signature}]
  return encodeCanonical([TX_ENVELOPE_TAG, signable, sig]);
}

/* --------------------------------- helpers -------------------------------- */

/** Convenience: attach txHash (derived from sign-bytes) to a SignedTx object. */
export function finalizeSignedTx(body: TxBody, signature: TxSignature): SignedTx {
  const signBytes = buildSignBytes(body);
  const txHash = computeTxHash(signBytes);
  return {
    body,
    signature,
    txHash,
  };
}

/**
 * Build the submission payload bytes for RPC:
 * - If your RPC expects raw CBOR bytes: call `encodeSignedTx`.
 * - If it expects hex string: wrap with `toHex` in your submitter.
 */
export function buildSubmissionBytes(tx: SignedTx): Uint8Array {
  return encodeSignedTx(tx.body, tx.signature);
}

/* --------------------------------- debug ---------------------------------- */

/** Human-friendly debug hex of SignBytes (not used in consensus). */
export function debugSignBytesHex(body: TxBody): string {
  return "0x" + toHex(buildSignBytes(body));
}

/** Concatenate domain + body CBOR for ad-hoc viewers. */
export function debugPrettyDump(body: TxBody): Uint8Array {
  const dom = encodeCanonical(SIGN_DOMAIN);
  const b = encodeTxBody(body);
  return concatBytes(dom, b);
}
