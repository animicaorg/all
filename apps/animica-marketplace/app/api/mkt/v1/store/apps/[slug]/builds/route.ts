import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { StreamTooLargeError } from '@/lib/mediaStore';
import { APK_MAX_BYTES, storeIncomingApk, removeBuildFile, registerBuild } from '@/lib/appStore';
import { verifyApk, type ApkVerdict } from '@/lib/apkVerify';
import { asApiError } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// APK release upload + listing. RAW binary body streamed to the durable builds store
// (never JSON, never base64); apksigner/aapt2 verification runs inline and the verdict
// decides BuildStatus. All release policy (packageName claim, cert pinning, versionCode
// monotonicity, quota, review status) is lib/appStore.ts registerBuild — this route is
// transport + auth only. nginx grants this path a 512m body via the ^~
// /api/mkt/v1/store/ location (deploy/animica.dev-marketplace.nginx.conf); the generic
// /api/mkt/ cap is 24m.

// Fields safe to return to the owner (never the disk path).
const BUILD_SELECT = {
  id: true, versionCode: true, versionName: true, channel: true, packageName: true,
  sizeBytes: true, sha3: true, certSha256: true, minSdk: true, targetSdk: true,
  permissionsJson: true, releaseNotes: true, status: true, reviewNote: true,
  createdAt: true, approvedAt: true,
} as const;

async function requireOwnedAppListing(req: NextRequest, slug: string) {
  const ctx = await authenticate(req);
  if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
  requireScope(ctx, 'publish');
  const listing = await prisma.listing.findUnique({
    where: { slug },
    select: { id: true, ownerId: true, type: true, packageName: true, pinnedCertSha256: true },
  });
  if (!listing) throw new ApiError(404, 'not_found', 'listing not found');
  if (listing.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
  return { ctx, listing };
}

// GET /api/mkt/v1/store/apps/[slug]/builds -> owner lists releases + statuses.
export async function GET(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const { listing } = await requireOwnedAppListing(req, params.slug);
    const builds = await prisma.appBuild.findMany({
      where: { listingId: listing.id },
      orderBy: { createdAt: 'desc' },
      take: 100,
      select: BUILD_SELECT,
    });
    return ok({ builds: jsonSafe(builds), packageName: listing.packageName, pinnedCertSha256: listing.pinnedCertSha256 });
  } catch (e) {
    return err(asApiError(e));
  }
}

// POST /api/mkt/v1/store/apps/[slug]/builds -> upload one APK release.
// RAW binary body. Headers: x-anm-channel (default "stable"), x-anm-release-notes
// (optional, URI-encoded ok).
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const { ctx, listing } = await requireOwnedAppListing(req, params.slug);
    if (listing.type !== 'APP') throw new ApiError(409, 'not_an_app', 'builds can only be uploaded to APP listings');

    const channel = req.headers.get('x-anm-channel') ?? undefined;
    const rawNotes = req.headers.get('x-anm-release-notes') || '';
    let releaseNotes: string | null = null;
    if (rawNotes) {
      try { releaseNotes = decodeURIComponent(rawNotes); } catch { releaseNotes = rawNotes; }
    }

    // Stream the raw APK into the builds store (disk floor + 512MB cap + sha3 inside).
    let staged: { tmpPath: string; bytes: number; sha3: string };
    try {
      staged = await storeIncomingApk(req.body);
    } catch (e) {
      if (e instanceof StreamTooLargeError) {
        throw new ApiError(413, 'too_large', `max ${Math.round(APK_MAX_BYTES / 1048576)} MB per APK`);
      }
      throw e;
    }

    // Inline verification (apksigner + aapt2); registerBuild owns file cleanup from here.
    let verdict: ApkVerdict;
    try {
      verdict = await verifyApk(staged.tmpPath);
    } catch (e) {
      await removeBuildFile(staged.tmpPath);
      throw e;
    }

    const { build, checksFailed } = await registerBuild({
      listingId: listing.id,
      actorAccountId: ctx.accountId,
      staged,
      verdict,
      channel,
      releaseNotes,
    });
    const full = await prisma.appBuild.findUnique({ where: { id: build.id }, select: BUILD_SELECT });
    return ok({
      build: jsonSafe(full ?? build),
      verdict: { ok: !checksFailed, reasons: checksFailed ? [build.reviewNote ?? 'checks failed'] : [] },
    }, { status: 201 });
  } catch (e) {
    return err(asApiError(e));
  }
}
