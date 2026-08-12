import { NextRequest } from 'next/server';
import { err, ApiError, publicOk, publicPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { STORE_TYPES, LATEST_BUILD_SELECT } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/apps/[slug]/versions?channel= -> public update-check list.
// APPROVED builds only, newest first: {versionCode, versionName, channel, sha3,
// certSha256, minSdk, sizeBytes, releaseNotes, createdAt}. The wallet compares its
// installed versionCode + re-verifies sha3/certSha256 on device — serve exactly what
// the license leaf binds.
export async function GET(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const channel = req.nextUrl.searchParams.get('channel')?.trim().toLowerCase() || undefined;
    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      select: { id: true, type: true, status: true, packageName: true, pinnedCertSha256: true },
    });
    if (!listing || listing.status === 'DRAFT' || !(STORE_TYPES as readonly string[]).includes(listing.type)) {
      throw new ApiError(404, 'not_found', 'app not found');
    }
    const versions = await prisma.appBuild.findMany({
      where: { listingId: listing.id, status: 'APPROVED', ...(channel ? { channel } : {}) },
      orderBy: { versionCode: 'desc' },
      take: 50,
      select: LATEST_BUILD_SELECT,
    });
    return publicOk({ slug: params.slug, packageName: listing.packageName, versions: jsonSafe(versions) });
  } catch (e) {
    return err(e);
  }
}

// CORS preflight — the wallet's update checker reads this cross-origin.
export async function OPTIONS() {
  return publicPreflight();
}
