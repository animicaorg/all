import { NextRequest } from 'next/server';
import { err, ApiError, publicOk, publicPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { STORE_TYPES, LATEST_BUILD_SELECT } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/apps/[slug]/versions/latest?channel=stable -> single newest
// APPROVED build (public update-check fast path for the wallet library screen).
export async function GET(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const channel = req.nextUrl.searchParams.get('channel')?.trim().toLowerCase() || 'stable';
    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      select: { id: true, type: true, status: true, packageName: true },
    });
    if (!listing || listing.status === 'DRAFT' || !(STORE_TYPES as readonly string[]).includes(listing.type)) {
      throw new ApiError(404, 'not_found', 'app not found');
    }
    const latest = await prisma.appBuild.findFirst({
      where: { listingId: listing.id, status: 'APPROVED', channel },
      orderBy: { versionCode: 'desc' },
      select: LATEST_BUILD_SELECT,
    });
    if (!latest) throw new ApiError(404, 'no_build', `no approved build on channel ${channel}`);
    return publicOk({ slug: params.slug, packageName: listing.packageName, latest: jsonSafe(latest) });
  } catch (e) {
    return err(e);
  }
}

// CORS preflight — the wallet's update checker reads this cross-origin.
export async function OPTIONS() {
  return publicPreflight();
}
