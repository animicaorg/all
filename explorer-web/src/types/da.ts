/* -----------------------------------------------------------------------------
 * Data Availability (DA) Types for Explorer
 *
 * These shapes are intentionally generic so the UI can interop with multiple
 * commitment/availability schemes (NMT, KZG, plain Merkle) and various RPCs.
 * -------------------------------------------------------------------------- */

import type { Address, Hash, Hex } from './core';

/** Supported commitment schemes. */
export type DAScheme = 'nmt' | 'kzg' | 'merkle';

/** Optional codec information for blobs. */
export type DACodec = 'raw' | 'gzip' | 'cbor' | 'json' | 'bytes';

/** Namespace identifier (scheme-dependent). For NMT this is usually 8/20 bytes. */
export type Namespace = Hex;

/* -----------------------------------------------------------------------------
 * Blob & metadata
 * -------------------------------------------------------------------------- */

export interface DABlobRef {
  /** Content-addressed ID (e.g. hash of the blob). */
  id: Hash;
}

export interface DABlobMeta extends DABlobRef {
  /** Size in bytes of the original payload (pre-encoding/compression). */
  size: number;
  /** Optional encoding/format hint for rendering or decoding. */
  codec?: DACodec;
  /** Who submitted/published this blob (if known). */
  submitter?: Address;
  /** First-seen block information (if known). */
  firstSeen?: {
    height?: number;
    blockHash?: Hash;
    timestamp?: number; // unix seconds
  };
  /** Associated commitment, if already included on-chain/DA-layer. */
  commitment?: DACommitment;
}

/** A byte-range for partial retrievals (HTTP range-like). */
export interface ByteRange {
  offset: number; // inclusive
  length: number;
}

/* -----------------------------------------------------------------------------
 * Commitments
 * -------------------------------------------------------------------------- */

export interface BaseCommitment {
  /** Commitment scheme used. */
  scheme: DAScheme;
  /** Root digest of the commitment (scheme-specific meaning). */
  root: Hex;
  /** Height or polynomial degree / tree depth depending on scheme. */
  height?: number;
  /** Total leaves used to build the commitment (if applicable). */
  leafCount?: number;
  /** Version string for the scheme implementation (optional). */
  version?: string;
}

export interface NMTCommitment extends BaseCommitment {
  scheme: 'nmt';
  /** Namespace under which the blob was committed. */
  namespace: Namespace;
}

export interface KZGCommitment extends BaseCommitment {
  scheme: 'kzg';
  /** KZG-specific parameters reference (e.g., "bls12-381:trusted-setup-1"). */
  paramsRef?: string;
}

export interface MerkleCommitment extends BaseCommitment {
  scheme: 'merkle';
  /** Hash function used to build the tree (e.g., "sha256", "keccak256"). */
  hashFn?: 'sha256' | 'keccak256' | 'blake3' | string;
}

export type DACommitment = NMTCommitment | KZGCommitment | MerkleCommitment;

/* -----------------------------------------------------------------------------
 * Proofs
 * -------------------------------------------------------------------------- */

export interface BaseInclusionProof {
  /** Scheme must match the commitment's scheme. */
  scheme: DAScheme;
  /** Index of the leaf containing the blob (or the first leaf for multi-leaf). */
  leafIndex: number;
  /** Total leaves in the committed structure (helps sanity checks). */
  totalLeaves: number;
  /** Hash/digest of the leaf payload after leaf-hash function. */
  leafHash: Hex;
  /**
   * Sibling path from leaf to root, ordered from leaf-level upward.
   * For NMT this is the NMT sibling path; for KZG this may be empty and the
   * proof lives in `kzg` below.
   */
  path?: Hex[];
  /** Root the proof claims to reconstruct. Should equal commitment.root. */
  commitmentRoot: Hex;
}

export interface NMTInclusionProof extends BaseInclusionProof {
  scheme: 'nmt';
  /** Namespace asserted by the proof (must match commitment.namespace). */
  namespace: Namespace;
}

export interface KZGInclusionProof extends BaseInclusionProof {
  scheme: 'kzg';
  /** Serialized KZG proof bytes (hex). */
  kzg: Hex;
  /** Optional multi-opening detail if blob spans several cells. */
  multi?: {
    cellIndices: number[];
    proof: Hex;
  };
}

export interface MerkleInclusionProof extends BaseInclusionProof {
  scheme: 'merkle';
  /** Hash function used for inclusion (must match commitment.hashFn). */
  hashFn?: 'sha256' | 'keccak256' | 'blake3' | string;
}

export type DAInclusionProof =
  | NMTInclusionProof
  | KZGInclusionProof
  | MerkleInclusionProof;

/** Optional independent attestations (e.g., multiple nodes confirming the proof). */
export interface DAAttestation {
  /** Verifier identity (nodeId, address, or URL). */
  verifier: string;
  /** Signed statement over (blobId, commitment.root, leafIndex) if available. */
  signature?: Hex;
  /** Height/context where verification was performed. */
  height?: number;
  timestamp?: number; // unix seconds
  /** Free-form info (latency, region, etc.) */
  meta?: Record<string, unknown>;
}

/** A full proof package linking a blob to a specific commitment and chain context. */
export interface DABlobProof {
  blob: DABlobRef;
  commitment: DACommitment;
  proof: DAInclusionProof;

  /** Chain context for the commitment (helps cross-checking by the UI). */
  context?: {
    /** Block that referenced/anchored the commitment. */
    blockHash?: Hash;
    height?: number;
    /** Transaction that published the commitment (if any). */
    txHash?: Hash;
  };

  /** Optional corroborating attestations. */
  attestations?: DAAttestation[];
}

/* -----------------------------------------------------------------------------
 * Type guards & helpers (structural checks only; no cryptography here)
 * -------------------------------------------------------------------------- */

export const isNMTCommitment = (c: DACommitment): c is NMTCommitment =>
  c.scheme === 'nmt';

export const isKZGCommitment = (c: DACommitment): c is KZGCommitment =>
  c.scheme === 'kzg';

export const isMerkleCommitment = (c: DACommitment): c is MerkleCommitment =>
  c.scheme === 'merkle';

export const isNMTProof = (p: DAInclusionProof): p is NMTInclusionProof =>
  p.scheme === 'nmt';

export const isKZGProof = (p: DAInclusionProof): p is KZGInclusionProof =>
  p.scheme === 'kzg';

export const isMerkleProof = (p: DAInclusionProof): p is MerkleInclusionProof =>
  p.scheme === 'merkle';

/** Quick structural sanity: proof-commitment alignment (root & scheme). */
export function proofMatchesCommitment(p: DABlobProof): boolean {
  if (p.commitment.scheme !== p.proof.scheme) return false;
  if (p.commitment.root.toLowerCase() !== p.proof.commitmentRoot.toLowerCase()) {
    return false;
  }
  if (isNMTCommitment(p.commitment) && isNMTProof(p.proof)) {
    return (
      p.commitment.namespace.toLowerCase() === p.proof.namespace.toLowerCase()
    );
  }
  return true;
}

/** Minimal check that a byte range is well-formed within a blob size. */
export function rangeValid(range: ByteRange, blobSize: number): boolean {
  if (range.offset < 0 || range.length < 0) return false;
  if (range.offset + range.length > blobSize) return false;
  return true;
}
