import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { requireAdmin } from '@/lib/adminAuth';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/admin/builds/[id]/review {action: approve|reject|delist, note?}
// Admin only (MKT_ADMIN_TOKEN header or role ADMIN account). Allowed transitions:
//   approve: UPLOADED | PENDING_REVIEW | REJECTED -> APPROVED  (pins the listing cert
//            if not yet pinned, marks the listing verified)
//   reject:  UPLOADED | PENDING_REVIEW | APPROVED -> REJECTED
//   delist:  APPROVED -> DELISTED  (kill-switch for a shipped release)
// CHECKS_FAILED rows are terminal — their binary was deleted at upload; there is
// nothing servable to approve (the publisher re-uploads instead).
const TRANSITIONS: Record<string, { from: string[]; to: 'APPROVED' | 'REJECTED' | 'DELISTED' }> = {
  approve: { from: ['UPLOADED', 'PENDING_REVIEW', 'REJECTED'], to: 'APPROVED' },
  reject: { from: ['UPLOADED', 'PENDING_REVIEW', 'APPROVED'], to: 'REJECTED' },
  delist: { from: ['APPROVED'], to: 'DELISTED' },
};

export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const admin = await requireAdmin(req);
    const body = await req.json().catch(() => ({}));
    const action = String(body.action ?? '').toLowerCase();
    const t = TRANSITIONS[action];
    if (!t) throw new ApiError(400, 'bad_action', 'action must be approve | reject | delist');
    const note = typeof body.note === 'string' ? body.note.slice(0, 1000) : null;

    const build = await prisma.appBuild.findUnique({
      where: { id: params.id },
      include: { listing: { select: { id: true, pinnedCertSha256: true, verified: true } } },
    });
    if (!build) throw new ApiError(404, 'not_found', 'build not found');
    if (!t.from.includes(build.status)) {
      throw new ApiError(409, 'bad_transition', `cannot ${action} a ${build.status} build`);
    }
    if (action === 'approve' && !build.path) {
      throw new ApiError(409, 'no_binary', 'this build has no stored binary (checks failed at upload) — publisher must re-upload');
    }
    // Cert-continuity backstop: approving a build whose signer differs from the pin is
    // an explicit cert ROTATION — require the caller to say so.
    if (action === 'approve' && build.listing.pinnedCertSha256 &&
        build.certSha256 !== build.listing.pinnedCertSha256 && body.rotateCert !== true) {
      throw new ApiError(409, 'cert_mismatch',
        'signer cert differs from the pinned publisher cert — pass {rotateCert: true} to rotate the pin to this cert');
    }

    const stamp = `${action}${note ? `: ${note}` : ''} (by ${admin.via === 'token' ? 'admin-token' : admin.accountId})`;
    const ops: any[] = [
      prisma.appBuild.update({
        where: { id: build.id },
        data: {
          status: t.to,
          reviewNote: stamp,
          approvedAt: t.to === 'APPROVED' ? new Date() : build.approvedAt,
        },
      }),
    ];
    if (t.to === 'APPROVED') {
      ops.push(prisma.listing.update({
        where: { id: build.listing.id },
        data: {
          verified: true,
          // First approval pins; {rotateCert:true} re-pins to this build's signer.
          ...(!build.listing.pinnedCertSha256 || body.rotateCert === true
            ? { pinnedCertSha256: build.certSha256 }
            : {}),
        },
      }));
    }
    const [updated] = await prisma.$transaction(ops);
    return ok({ build: jsonSafe({ ...updated, path: undefined }) });
  } catch (e) {
    return err(e);
  }
}
