import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { PUBLIC_CORS } from '@/lib/api';
import { jsonSafe } from '@/lib/nanm';
import {
  canonicalLicenseRecord, licenseLeafHash, verifyLicenseProof, type MerkleProofStep,
} from '@/lib/license';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/licenses/verify { licenseId | leaf } -> PUBLIC license check:
// validity (revocation/expiry — the API-checked side anchors cannot express), the anchor
// txid + Merkle proof, and the canonical record so any third party can recompute the leaf
// and verify inclusion against the on-chain ANMLIC1 root WITHOUT trusting this server.
// Public + CORS (read-only, no secrets) so .anm sites and wallets can call it directly.

const VERIFY_CORS: Record<string, string> = {
  ...PUBLIC_CORS,
  'access-control-allow-methods': 'POST, OPTIONS',
};

function respond(data: any, status = 200) {
  return NextResponse.json(jsonSafe(data), { status, headers: VERIFY_CORS });
}

export function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: VERIFY_CORS });
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const licenseId = typeof body.licenseId === 'string' ? body.licenseId.trim() : '';
    const leaf = typeof body.leaf === 'string' ? body.leaf.trim().toLowerCase() : '';
    if (!licenseId && !/^[0-9a-f]{64}$/.test(leaf)) {
      return respond({ error: { code: 'bad_request', message: 'pass licenseId or leaf (64-hex leafHash)' } }, 400);
    }

    const license = licenseId
      ? await prisma.license.findUnique({ where: { id: licenseId }, include: VERIFY_INCLUDE })
      : await prisma.license.findFirst({ where: { leafHash: leaf }, include: VERIFY_INCLUDE });
    if (!license) return respond({ valid: false, reasons: ['not_found'] }, 404);

    const now = new Date();
    const reasons: string[] = [];
    if (license.revokedAt) reasons.push('revoked');
    if (license.expiresAt && license.expiresAt <= now) reasons.push('expired');
    if (license.purchase.status === 'REFUNDED') reasons.push('refunded');

    // Integrity: recompute the leaf from the canonical record — a DB row that no longer
    // hashes to its own leafHash is tampered/corrupt and must not verify.
    const record = canonicalLicenseRecord({
      licenseId: license.id,
      purchaseTxid: license.purchase.txid,
      buyerAddress: license.buyerAddress,
      listingId: license.listingId,
      buildId: license.buildId,
      buildSha3: license.buildSha3,
      certSha256: license.certSha256,
      kind: license.kind,
      issuedAt: license.issuedAt,
      expiresAt: license.expiresAt,
    });
    const recomputedLeaf = licenseLeafHash({
      licenseId: license.id,
      purchaseTxid: license.purchase.txid,
      buyerAddress: license.buyerAddress,
      listingId: license.listingId,
      buildId: license.buildId,
      buildSha3: license.buildSha3,
      certSha256: license.certSha256,
      kind: license.kind,
      issuedAt: license.issuedAt,
      expiresAt: license.expiresAt,
    });
    if (recomputedLeaf !== license.leafHash) reasons.push('leaf_mismatch');

    // Server-side proof check against the anchor root (fail-closed: an anchored license
    // whose stored proof does not verify is NOT valid). Unanchored-yet licenses stay
    // valid — anchoring is batched (every 10 min / 500 licenses). merkleProofJson may be
    // a bare step array or the anchor worker's envelope { leafHash, root, seq, steps }.
    const rawProof = license.merkleProofJson as any;
    const proof: MerkleProofStep[] | null = Array.isArray(rawProof)
      ? rawProof
      : rawProof && Array.isArray(rawProof.steps) ? rawProof.steps : null;
    let proofOk: boolean | null = null;
    if (license.anchor) {
      proofOk = proof != null && verifyLicenseProof(license.leafHash, proof, license.anchor.merkleRoot);
      if (!proofOk) reasons.push('proof_mismatch');
    }

    return respond({
      valid: reasons.length === 0,
      reasons,
      anchored: !!(license.anchor && license.anchor.txid),
      license: {
        id: license.id,
        listingId: license.listingId,
        buyerAddress: license.buyerAddress,
        kind: license.kind,
        issuedAt: license.issuedAt,
        expiresAt: license.expiresAt,
        revokedAt: license.revokedAt,
        buildId: license.buildId,
        buildSha3: license.buildSha3,
        certSha256: license.certSha256,
        leafHash: license.leafHash,
      },
      purchaseTxid: license.purchase.txid,
      canonicalRecord: record,
      merkleProof: proof,
      proofOk,
      anchor: license.anchor,
    });
  } catch {
    return respond({ error: { code: 'internal', message: 'verification failed' } }, 500);
  }
}

const VERIFY_INCLUDE = {
  anchor: { select: { seq: true, merkleRoot: true, leafCount: true, prevTxid: true, txid: true, blockHeight: true, status: true } },
  purchase: { select: { txid: true, status: true } },
} as const;
