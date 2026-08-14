import { NextRequest } from 'next/server';
import { ok, err } from '@/lib/api';
import { prisma } from '@/lib/db';
import { requireAdmin } from '@/lib/adminAuth';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/admin/builds?status=PENDING_REVIEW -> review queue.
// Admin only (MKT_ADMIN_TOKEN header or role ADMIN account). Shows everything the
// review decision needs: verdict badging, sensitive permissions, publisher pin state.
const STATUSES = ['UPLOADED', 'CHECKS_FAILED', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'DELISTED'] as const;

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const sp = req.nextUrl.searchParams;
    const status = (sp.get('status') ?? 'PENDING_REVIEW').toUpperCase();
    if (!(STATUSES as readonly string[]).includes(status)) {
      return ok({ builds: [], statuses: STATUSES });
    }
    const take = Math.min(Number(sp.get('limit') ?? 50), 200);
    const builds = await prisma.appBuild.findMany({
      where: { status: status as any },
      orderBy: { createdAt: 'asc' }, // oldest waiting first
      take,
      select: {
        id: true, versionCode: true, versionName: true, channel: true, packageName: true,
        sizeBytes: true, sha3: true, certSha256: true, minSdk: true, targetSdk: true,
        permissionsJson: true, badgingJson: true, releaseNotes: true, status: true,
        reviewNote: true, createdAt: true, approvedAt: true,
        listing: {
          select: {
            slug: true, name: true, type: true, verified: true, packageName: true, pinnedCertSha256: true,
            owner: { select: { address: true, displayName: true } },
          },
        },
      },
    });
    return ok({ builds: jsonSafe(builds) });
  } catch (e) {
    return err(e);
  }
}
