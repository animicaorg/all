import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/vpn/exits/[id]/heartbeat { load, openSessions }  (scope: vpn, owner-only)
// Keeps the exit marked online + refreshes lastSeenAt so reward accrual continues.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');
    const exit = await prisma.vpnExit.findUnique({ where: { id: params.id } });
    if (!exit) throw new ApiError(404, 'not_found', 'no such exit');
    if (exit.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the exit owner');

    const body = await req.json().catch(() => ({}));
    return await withIdempotency(req, ctx, body, async () => {
      const rawLoad = Number(body.load);
      const load = Number.isFinite(rawLoad) ? Math.min(1, Math.max(0, rawLoad)) : exit.load;
      const openSessions = Math.max(0, Math.floor(Number(body.openSessions ?? exit.openSessions)) || 0);
      const upd = await prisma.vpnExit.update({
        where: { id: exit.id },
        data: { lastSeenAt: new Date(), online: true, load, openSessions },
      });
      return {
        status: 200,
        data: {
          ok: true, exitId: exit.id, online: true,
          lastSeenAt: upd.lastSeenAt, load: upd.load, openSessions: upd.openSessions,
          note: 'accrual continues while heartbeats are fresh; rewards accrue (pending settlement) as IOUs',
        },
      };
    });
  } catch (e) {
    return err(e);
  }
}
