import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/purchases/[id] -> buyer polls a wallet purchase through its states
// (broadcast -> included -> 12 confs -> license issued). Returns the purchase, the payment
// intent (incl. failReason from the watcher) and the License + anchor once ACTIVE.
export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const purchase = await prisma.purchase.findUnique({
      where: { id: params.id },
      include: {
        listing: { select: { slug: true, name: true, type: true } },
        paymentIntent: true,
        license: {
          include: {
            anchor: { select: { seq: true, merkleRoot: true, leafCount: true, txid: true, blockHeight: true, status: true } },
            build: { select: { versionCode: true, versionName: true, channel: true, sha3: true, certSha256: true, sizeBytes: true } },
          },
        },
      },
    });
    // 404 (not 403) for foreign purchases — don't leak row existence.
    if (!purchase || purchase.buyerId !== ctx.accountId) throw new ApiError(404, 'not_found', 'purchase not found');

    const intent = purchase.paymentIntent;
    const license = purchase.license;
    return ok({
      purchase: jsonSafe({
        id: purchase.id,
        status: purchase.status,
        priceModel: purchase.priceModel,
        amountNanm: purchase.amountNanm,
        source: purchase.source,
        txid: purchase.txid,
        expiresAt: purchase.expiresAt,
        autoRenew: purchase.autoRenew,
        graceUntil: purchase.graceUntil,
        createdAt: purchase.createdAt,
        listing: purchase.listing,
      }),
      intent: intent ? jsonSafe({
        payTo: intent.payTo,
        amountNanm: intent.amountNanm,
        memoHex: intent.memoHex,
        expiresAt: intent.expiresAt,
        submittedTxid: intent.submittedTxid,
        verifiedAt: intent.verifiedAt,
        failReason: intent.failReason,
        expired: !intent.verifiedAt && intent.expiresAt < new Date(),
      }) : null,
      license: license ? jsonSafe({
        id: license.id,
        kind: license.kind,
        buyerAddress: license.buyerAddress,
        listingId: license.listingId,
        buildId: license.buildId,
        buildSha3: license.buildSha3,
        certSha256: license.certSha256,
        issuedAt: license.issuedAt,
        expiresAt: license.expiresAt,
        revokedAt: license.revokedAt,
        leafHash: license.leafHash,
        merkleProof: license.merkleProofJson,
        anchor: license.anchor,
        build: license.build,
      }) : null,
    });
  } catch (e) {
    return err(e);
  }
}
