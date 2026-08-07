import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/licenses -> the caller's licenses with Merkle proof + anchor info.
// The wallet stores {license, merkleProof, anchor.txid} in its encrypted vault and can
// verify inclusion against the on-chain ANMLIC1 anchor tx without trusting this server.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const licenses = await prisma.license.findMany({
      where: { purchase: { buyerId: ctx.accountId } },
      orderBy: { issuedAt: 'desc' },
      include: {
        anchor: { select: { seq: true, merkleRoot: true, leafCount: true, prevTxid: true, txid: true, blockHeight: true, status: true } },
        build: { select: { versionCode: true, versionName: true, channel: true, sha3: true, certSha256: true, sizeBytes: true, packageName: true } },
        purchase: {
          select: {
            txid: true, status: true, priceModel: true, amountNanm: true,
            listing: { select: { slug: true, name: true, type: true } },
          },
        },
      },
    });
    return ok({
      licenses: jsonSafe(licenses.map((l) => ({
        id: l.id,
        listingId: l.listingId,
        listing: l.purchase.listing,
        purchaseId: l.purchaseId,
        purchaseTxid: l.purchase.txid,
        purchaseStatus: l.purchase.status,
        buyerAddress: l.buyerAddress,
        kind: l.kind,
        issuedAt: l.issuedAt,
        expiresAt: l.expiresAt,
        revokedAt: l.revokedAt,
        buildId: l.buildId,
        buildSha3: l.buildSha3,
        certSha256: l.certSha256,
        build: l.build,
        leafHash: l.leafHash,
        merkleProof: l.merkleProofJson,
        anchor: l.anchor,
      }))),
    });
  } catch (e) {
    return err(e);
  }
}
