import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { checkEntitlement } from '@/lib/entitlement';
import { mintDownloadToken } from '@/lib/downloadToken';
import { CHANNEL_RE, STORE_TYPES } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/apps/[slug]/download-token { buildId?, channel? } -> entitlement-
// gated, short-lived (10 min) HMAC token + download URL for an APPROVED build, plus the
// sha3/certSha256 the wallet MUST re-verify against the downloaded bytes before install.
// Defaults to the latest APPROVED build on the 'stable' channel.
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'use');
    const body = await req.json().catch(() => ({}));

    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      select: { id: true, ownerId: true, type: true, status: true, packageName: true },
    });
    if (!listing || !(STORE_TYPES as readonly string[]).includes(listing.type)) {
      throw new ApiError(404, 'not_found', 'app not found');
    }
    // DRAFT listings are visible only to their owner (who is always entitled).
    if (listing.status === 'DRAFT' && listing.ownerId !== ctx.accountId) {
      throw new ApiError(404, 'not_found', 'app not found');
    }

    const ent = await checkEntitlement(ctx.accountId, listing.id);
    if (!ent.entitled) throw new ApiError(403, 'not_entitled', 'purchase required');

    const channel = String(body.channel ?? 'stable');
    if (!CHANNEL_RE.test(channel)) throw new ApiError(400, 'bad_channel', 'invalid channel');
    const build = body.buildId
      ? await prisma.appBuild.findFirst({
          where: { id: String(body.buildId), listingId: listing.id, status: 'APPROVED' },
        })
      : await prisma.appBuild.findFirst({
          where: { listingId: listing.id, status: 'APPROVED', channel },
          orderBy: { versionCode: 'desc' },
        });
    if (!build) throw new ApiError(404, 'no_build', 'no approved build available');

    const { token, expiresAt } = mintDownloadToken(ctx.accountId, build.id);
    return ok(jsonSafe({
      token,
      url: `/api/mkt/v1/store/download/${token}`,
      expiresAt,
      buildId: build.id,
      versionCode: build.versionCode,
      versionName: build.versionName,
      channel: build.channel,
      packageName: build.packageName,
      sizeBytes: build.sizeBytes,
      sha3: build.sha3,
      certSha256: build.certSha256,
      minSdk: build.minSdk,
    }));
  } catch (e) {
    return err(e);
  }
}
