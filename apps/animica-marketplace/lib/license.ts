import { createHash } from 'node:crypto';

// License leaf hashing + Merkle root/proof for ANMLIC1 anchors (same tree shape as the ANS
// anchor in lib/ans.ts:50-66: sorted hex-digest leaves, sha3-256 over hex-string concat,
// odd node duplicated). Extends the ans.ts builder with per-leaf inclusion proofs so the
// wallet can verify its license against the on-chain anchor root without trusting us.
// Everything operates on lowercase hex-string digests (deterministic, Buffer-friction-free).

const HEX64_RE = /^[0-9a-f]{64}$/;

function sha3hex(input: string): string {
  return createHash('sha3-256').update(input).digest('hex');
}

// Root of an anchor with zero leaves (never posted on-chain; sentinel for completeness).
export const EMPTY_LICENSE_ROOT = sha3hex('animica/store/licenses/empty');

// The canonical license record the leaf commits to. Timestamps as epoch seconds, absent
// optionals as '' — mirrors domainRecordHash (lib/ans.ts:37-46). The wallet recomputes this
// EXACT string from its stored license, so the field order here is a wire contract.
export interface LicenseLeaf {
  licenseId: string;
  purchaseTxid: string | null; // on-chain ANMSTORE1 tx (null for custodial-balance purchases)
  buyerAddress: string;
  listingId: string;
  buildId: string | null; // exact binary licensed; null = covers all builds
  buildSha3: string | null;
  certSha256: string | null;
  kind: string; // PERPETUAL | SUBSCRIPTION
  issuedAt: Date;
  expiresAt: Date | null;
}

export function canonicalLicenseRecord(l: LicenseLeaf): string {
  return JSON.stringify({
    licenseId: l.licenseId,
    purchaseTxid: l.purchaseTxid ?? '',
    buyer: l.buyerAddress,
    listing: l.listingId,
    buildId: l.buildId ?? '',
    buildSha3: l.buildSha3 ?? '',
    certSha256: l.certSha256 ?? '',
    kind: l.kind,
    issued: Math.floor(l.issuedAt.getTime() / 1000),
    expires: l.expiresAt ? Math.floor(l.expiresAt.getTime() / 1000) : 0,
  });
}

export function licenseLeafHash(l: LicenseLeaf): string {
  return sha3hex(canonicalLicenseRecord(l));
}

// One step of an inclusion proof: the sibling digest and which side IT sits on.
// side 'right' => parent = sha3(current + sibling); side 'left' => sha3(sibling + current).
export interface MerkleProofStep {
  sibling: string; // 64-hex sha3-256 digest
  side: 'left' | 'right';
}

function assertLeaves(leafHashes: string[]) {
  for (const h of leafHashes) {
    if (typeof h !== 'string' || !HEX64_RE.test(h)) throw new Error(`license merkle: bad leaf hash ${JSON.stringify(h)}`);
  }
}

// Root over sorted leaf hashes (leaves ARE level 0 — they are already sha3 digests).
// Odd node pairs with itself, exactly like lib/ans.ts merkleRoot.
export function licenseMerkleRoot(leafHashes: string[]): string {
  return buildLicenseMerkle(leafHashes).root;
}

// Root + per-leaf inclusion proofs. Proofs are keyed by leaf hash (leaf hashes are unique —
// licenseId is baked into the leaf). Stored per-License as merkleProofJson.
export function buildLicenseMerkle(leafHashes: string[]): {
  root: string;
  proofs: Record<string, MerkleProofStep[]>;
} {
  assertLeaves(leafHashes);
  if (!leafHashes.length) return { root: EMPTY_LICENSE_ROOT, proofs: {} };

  const sorted = leafHashes.slice().sort();
  const proofs: Record<string, MerkleProofStep[]> = {};
  const steps: MerkleProofStep[][] = sorted.map(() => []);
  // positions[i] = index of original leaf i within the current level
  const positions = sorted.map((_, i) => i);

  let level = sorted;
  while (level.length > 1) {
    for (let i = 0; i < positions.length; i += 1) {
      const p = positions[i];
      if (p % 2 === 0) {
        steps[i].push({ sibling: p + 1 < level.length ? level[p + 1] : level[p], side: 'right' });
      } else {
        steps[i].push({ sibling: level[p - 1], side: 'left' });
      }
      positions[i] = p >> 1;
    }
    const next: string[] = [];
    for (let i = 0; i < level.length; i += 2) {
      const a = level[i];
      const b = i + 1 < level.length ? level[i + 1] : level[i];
      next.push(sha3hex(a + b));
    }
    level = next;
  }

  for (let i = 0; i < sorted.length; i += 1) proofs[sorted[i]] = steps[i];
  return { root: level[0], proofs };
}

// Verify one leaf's inclusion proof against an anchor root. Fail-closed: malformed steps,
// oversized proofs, or any mismatch => false (never throws on hostile input).
export function verifyLicenseProof(leafHash: string, proof: MerkleProofStep[], root: string): boolean {
  if (typeof leafHash !== 'string' || !HEX64_RE.test(leafHash)) return false;
  if (typeof root !== 'string' || !HEX64_RE.test(root)) return false;
  if (!Array.isArray(proof) || proof.length > 64) return false;
  let h = leafHash;
  for (const step of proof) {
    if (typeof step !== 'object' || step === null) return false;
    if (typeof step.sibling !== 'string' || !HEX64_RE.test(step.sibling)) return false;
    if (step.side === 'right') h = sha3hex(h + step.sibling);
    else if (step.side === 'left') h = sha3hex(step.sibling + h);
    else return false;
  }
  return h === root;
}

// ── ANMLIC1 anchor payload ───────────────────────────────────────────────────
// Data bytes of the on-chain anchor tx: `ANMLIC1|` magic + strict compact JSON
// {v, seq, root, prev, n} — same fail-closed build/parse discipline as lib/storeMemo.ts
// (exact key set, canonical re-encode equality, builder strict-re-parses its output).
// prev = txid of the previous anchor ('' for the first), forming the hash chain.

export const LICENSE_ANCHOR_MAGIC = 'ANMLIC1|';
export const LICENSE_ANCHOR_MAX_BYTES = 300;
const TXID_RE = /^[0-9a-f]{64}$/;
const MAX_ANCHOR_SEQ = 1_000_000_000;
const MAX_ANCHOR_LEAVES = 1_000_000;

export interface LicenseAnchorPayload {
  v: 1;
  seq: number;
  root: string; // 64-hex merkle root
  prev: string; // 64-hex txid of previous anchor, or '' for the first
  n: number; // leaf count
}

export type LicenseAnchorParse =
  | { ok: true; anchor: LicenseAnchorPayload }
  | { ok: false; reason: string };

function canonicalAnchorJson(a: { seq: number; root: string; prev: string; n: number }): string {
  return `{"v":1,"seq":${a.seq},"root":"${a.root}","prev":"${a.prev}","n":${a.n}}`;
}

export function buildLicenseAnchorPayload(a: { seq: number; root: string; prev: string | null; n: number }): Uint8Array {
  const prev = (a.prev ?? '').toLowerCase().replace(/^0x/, '');
  const root = a.root.toLowerCase();
  if (!Number.isInteger(a.seq) || a.seq < 1 || a.seq > MAX_ANCHOR_SEQ) throw new Error(`anchor build: bad seq ${a.seq}`);
  if (!HEX64_RE.test(root)) throw new Error('anchor build: bad root');
  if (prev !== '' && !TXID_RE.test(prev)) throw new Error('anchor build: bad prev txid');
  if (!Number.isInteger(a.n) || a.n < 1 || a.n > MAX_ANCHOR_LEAVES) throw new Error(`anchor build: bad leaf count ${a.n}`);
  const bytes = Buffer.from(LICENSE_ANCHOR_MAGIC + canonicalAnchorJson({ seq: a.seq, root, prev, n: a.n }), 'utf8');
  if (bytes.length > LICENSE_ANCHOR_MAX_BYTES) throw new Error('anchor build: oversize');
  const reparsed = parseLicenseAnchorPayload(bytes);
  if (!reparsed.ok) throw new Error(`anchor build: encoded payload failed strict re-parse: ${reparsed.reason}`);
  return new Uint8Array(bytes);
}

export function parseLicenseAnchorPayload(data: Uint8Array): LicenseAnchorParse {
  if (!(data instanceof Uint8Array)) return { ok: false, reason: 'not bytes' };
  if (data.length > LICENSE_ANCHOR_MAX_BYTES) return { ok: false, reason: 'oversize' };
  if (data.length <= LICENSE_ANCHOR_MAGIC.length) return { ok: false, reason: 'too short' };
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(data);
  } catch {
    return { ok: false, reason: 'not utf-8' };
  }
  if (!text.startsWith(LICENSE_ANCHOR_MAGIC)) return { ok: false, reason: 'bad magic' };
  let obj: any;
  try {
    obj = JSON.parse(text.slice(LICENSE_ANCHOR_MAGIC.length));
  } catch {
    return { ok: false, reason: 'bad json' };
  }
  if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) return { ok: false, reason: 'not an object' };
  const keys = Object.keys(obj);
  const expected = ['v', 'seq', 'root', 'prev', 'n'];
  if (keys.length !== expected.length || expected.some((k) => !(k in obj))) return { ok: false, reason: 'wrong key set' };
  if (obj.v !== 1 || typeof obj.v !== 'number') return { ok: false, reason: 'bad version' };
  if (typeof obj.seq !== 'number' || !Number.isInteger(obj.seq) || obj.seq < 1 || obj.seq > MAX_ANCHOR_SEQ) return { ok: false, reason: 'bad seq' };
  if (typeof obj.root !== 'string' || !HEX64_RE.test(obj.root)) return { ok: false, reason: 'bad root' };
  if (typeof obj.prev !== 'string' || (obj.prev !== '' && !TXID_RE.test(obj.prev))) return { ok: false, reason: 'bad prev' };
  if (typeof obj.n !== 'number' || !Number.isInteger(obj.n) || obj.n < 1 || obj.n > MAX_ANCHOR_LEAVES) return { ok: false, reason: 'bad leaf count' };
  if (text !== LICENSE_ANCHOR_MAGIC + canonicalAnchorJson({ seq: obj.seq, root: obj.root, prev: obj.prev, n: obj.n })) {
    return { ok: false, reason: 'non-canonical encoding' };
  }
  return { ok: true, anchor: { v: 1, seq: obj.seq, root: obj.root, prev: obj.prev, n: obj.n } };
}
