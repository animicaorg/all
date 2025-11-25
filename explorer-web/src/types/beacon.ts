/* -----------------------------------------------------------------------------
 * Randomness Beacon Types for Explorer
 *
 * These types model:
 *  - Round lifecycle/metadata ("Rounds")
 *  - Finalized beacon outputs ("Beacon")
 *  - A compact light inclusion proof ("LightProof") that anchors the output
 *    to a specific block header without requiring a full node.
 *
 * They are intentionally generic so different beacon mechanisms
 * (VRF, commit-reveal, drand-style, etc.) can be represented uniformly.
 * -------------------------------------------------------------------------- */

import type { Hash, Hex, Address } from './core';

/** Numeric identifier of a beacon round. Kept as number for UI convenience. */
export type RoundId = number;

/** Coarse phase for a round's lifecycle. */
export type RoundPhase = 'commit' | 'reveal' | 'finalizing' | 'finalized' | 'unknown';

/** Timing window (unix seconds) for round periods, if available. */
export interface RoundWindow {
  /** Round start (usually when commit opens). */
  start?: number;
  /** Last moment to submit commits (inclusive/exclusive depends on backend). */
  commitDeadline?: number;
  /** Last moment to submit reveals. */
  revealDeadline?: number;
  /** Estimated time when the round will finalize. */
  finalizeEta?: number;
}

/** Aggregate activity stats for a round. */
export interface RoundStats {
  commits: number;
  reveals: number;
  /** Number of participants who committed but failed to reveal (if known). */
  missed?: number;
}

/** Minimal on-chain anchor context for an event/result. */
export interface ChainAnchor {
  height?: number;
  blockHash?: Hash;
  timestamp?: number; // unix seconds
}

/* -----------------------------------------------------------------------------
 * Rounds
 * -------------------------------------------------------------------------- */

export interface BeaconRound {
  /** Round identifier (monotonic). */
  id: RoundId;

  /** Current coarse phase. */
  phase: RoundPhase;

  /** Optional timing window for commit/reveal/finalization. */
  window?: RoundWindow;

  /** Participation counters. */
  stats?: RoundStats;

  /** Optional preimage/seed for commit-reveal beacons. */
  seed?: Hex;

  /**
   * Final randomness for this round (present when phase === 'finalized').
   * Some systems also expose intermediate "randomness candidates" earlier—those
   * should not be placed here unless finalized.
   */
  randomness?: Hex;

  /** Block context where the round transitioned/finalized. */
  anchor?: ChainAnchor;

  /** Optional compact proof anchoring this round’s result to a header. */
  proof?: LightProof;
}

/* -----------------------------------------------------------------------------
 * Beacon (final outputs)
 * -------------------------------------------------------------------------- */

export interface Beacon {
  /** The finalized round id. */
  round: RoundId;
  /** Final randomness value (uniform bytes encoded as hex). */
  randomness: Hex;

  /**
   * Optional mix summary (useful if the chain exposes PoIES-like breakdowns).
   * These fields are advisory and may not be present on all networks.
   */
  mix?: {
    participants?: number;
    commits?: number;
    reveals?: number;
    /** Fairness/dispersion indicators (domain-specific). */
    gini?: number;
    herfindahl?: number;
  };

  /** The on-chain anchor of this finalized output. */
  anchor?: ChainAnchor;

  /** Optional compact proof anchoring this output to a header. */
  proof?: LightProof;
}

/* -----------------------------------------------------------------------------
 * LightProof
 *
 * A format-agnostic proof that a particular leaf/value was included in
 * a specific block header. The explorer can show structural validity and
 * header alignment; cryptographic verification lives in a light client.
 * -------------------------------------------------------------------------- */

/** Merkle-like inclusion witness (scheme-agnostic). */
export interface InclusionPath {
  /**
   * Sibling hashes from leaf to root, ordered bottom-up.
   * The hash function is chain-specific and not encoded here.
   */
  path: Hex[];

  /** Optional leaf hash (after leaf hashing), if provided. */
  leafHash?: Hex;

  /** Index of the leaf within the tree (0-based), if known. */
  leafIndex?: number;

  /** Total leaves in the tree at commitment time (for sanity checks). */
  total?: number;

  /** Claimed root reconstructed by the path (should match header root). */
  root?: Hex;
}

/** Minimal header summary sufficient for UI cross-checks & caching. */
export interface HeaderSummary {
  height: number;
  blockHash: Hash;
  parentHash?: Hash;
  timestamp?: number; // unix seconds
  /** Optional state/receipts root depending on the chain. */
  stateRoot?: Hex;
  receiptsRoot?: Hex;
  /** Optional DA commitment root referenced by the header. */
  daRoot?: Hex;
}

/**
 * A compact proof that anchors an object (e.g., beacon result log/receipt)
 * to a specific header using an inclusion path into the relevant commitment.
 */
export interface LightProof {
  /** Header we claim to anchor to. */
  header: HeaderSummary;

  /**
   * Inclusion witness from the object to the header commitment root.
   * The exact commitment (logs trie, receipts trie, DA root, etc.) is
   * chain-specific; the UI only performs structural checks.
   */
  inclusion?: InclusionPath;

  /** Optional independent attestations (signatures, node ids, etc.). */
  attestations?: Array<{
    signer: Address | string;
    signature?: Hex;
    height?: number;
  }>;
}

/* -----------------------------------------------------------------------------
 * Type guards & small helpers
 * -------------------------------------------------------------------------- */

/** True if the round is finalized and has a randomness value. */
export const isFinalizedRound = (r: BeaconRound): boolean =>
  r.phase === 'finalized' && typeof r.randomness === 'string' && r.randomness.length > 0;

/** Quick equality check that the proof anchors to the provided block. */
export function lightProofAnchorsBlock(
  proof: LightProof | undefined,
  blockHash: Hash,
  height?: number
): boolean {
  if (!proof) return false;
  if (proof.header.blockHash.toLowerCase() !== blockHash.toLowerCase()) return false;
  if (typeof height === 'number' && proof.header.height !== height) return false;
  return true;
}

/** Convenience: returns a human-friendly status line for a round. */
export function roundStatusLabel(r: BeaconRound): string {
  const phase = r.phase ?? 'unknown';
  if (phase === 'finalized' && r.randomness) return `Round #${r.id} finalized`;
  if (phase === 'commit') return `Round #${r.id} — commit open`;
  if (phase === 'reveal') return `Round #${r.id} — reveal open`;
  if (phase === 'finalizing') return `Round #${r.id} — finalizing`;
  return `Round #${r.id} — ${phase}`;
}
