import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';
import { allocateIp } from '@/lib/vpn';

export const dynamic = 'force-dynamic';

function isUniqueViolation(e: unknown): boolean {
  return !!e && typeof e === 'object' && (e as any).code === 'P2002';
}

// POST /api/mkt/v1/vpn/exits/[id]/peer { clientWgPub, address }  (scope: vpn)
// Client requests a session lease on this exit: allocate the next free /32 in 10.99.0.0/16, open a
// VpnSession, and return the tunnel config. The caller's account is the session's client.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');
    const exit = await prisma.vpnExit.findUnique({ where: { id: params.id } });
    if (!exit) throw new ApiError(404, 'not_found', 'no such exit');
    const body = await req.json().catch(() => ({}));

    return await withIdempotency(req, ctx, body, async () => {
      if (!exit.online) throw new ApiError(409, 'exit_offline', 'exit is offline');
      const clientWgPub = String(body.clientWgPub ?? '').trim();
      if (!clientWgPub) throw new ApiError(400, 'bad_peer', 'clientWgPub required');

      // Refuse denylisted peer keys.
      const denied = await prisma.vpnDenylistEntry.findFirst({ where: { kind: 'peer', value: clientWgPub } });
      if (denied) throw new ApiError(403, 'peer_denied', 'this peer key is denylisted');

      // Allocate + create with a small retry loop; @@unique([exitId, allocatedIp]) protects races.
      let session = null as null | Awaited<ReturnType<typeof prisma.vpnSession.create>>;
      for (let attempt = 0; attempt < 5 && !session; attempt += 1) {
        const ip = await allocateIp(exit.id);
        try {
          session = await prisma.vpnSession.create({
            data: {
              exitId: exit.id, clientAccountId: ctx.accountId,
              clientWgPub, allocatedIp: ip, status: 'ACTIVE',
            },
          });
        } catch (e) {
          if (isUniqueViolation(e)) continue; // raced on that IP — pick the next free one
          throw e;
        }
      }
      if (!session) throw new ApiError(503, 'pool_contended', 'could not allocate an IP, retry');

      await prisma.vpnExit.update({
        where: { id: exit.id }, data: { openSessions: { increment: 1 } },
      }).catch(() => {});

      return {
        status: 201,
        data: {
          sessionId: session.id,
          allocatedIp: session.allocatedIp,
          exitWgPubkey: exit.wgPubkey,
          exitEndpoint: exit.endpoint,
          dns: '1.1.1.1',
        },
      };
    });
  } catch (e) {
    return err(e);
  }
}

// GET /api/mkt/v1/vpn/exits/[id]/peer  (scope: vpn, owner-only)
// The exit operator lists its ACTIVE sessions' granted peers (to program WireGuard).
export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');
    const exit = await prisma.vpnExit.findUnique({ where: { id: params.id } });
    if (!exit) throw new ApiError(404, 'not_found', 'no such exit');
    if (exit.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the exit owner');

    const sessions = await prisma.vpnSession.findMany({
      where: { exitId: exit.id, status: 'ACTIVE' },
      select: { id: true, clientWgPub: true, allocatedIp: true },
      orderBy: { startedAt: 'asc' },
    });
    return ok({
      peers: sessions.map((s) => ({ clientWgPub: s.clientWgPub, allocatedIp: s.allocatedIp, sessionId: s.id })),
      count: sessions.length,
    });
  } catch (e) {
    return err(e);
  }
}

// DELETE /api/mkt/v1/vpn/exits/[id]/peer { sessionId }  (scope: vpn)
// Close a session (either the client that owns it or the exit operator may close it).
export async function DELETE(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');
    const exit = await prisma.vpnExit.findUnique({ where: { id: params.id } });
    if (!exit) throw new ApiError(404, 'not_found', 'no such exit');
    const body = await req.json().catch(() => ({}));
    const sessionId = String(body.sessionId ?? '').trim();
    if (!sessionId) throw new ApiError(400, 'bad_request', 'sessionId required');

    const session = await prisma.vpnSession.findUnique({ where: { id: sessionId } });
    if (!session || session.exitId !== exit.id) throw new ApiError(404, 'not_found', 'no such session on this exit');
    if (session.clientAccountId !== ctx.accountId && exit.ownerId !== ctx.accountId) {
      throw new ApiError(403, 'forbidden', 'only the session client or the exit owner may close it');
    }

    if (session.status !== 'CLOSED') {
      await prisma.vpnSession.update({
        where: { id: session.id }, data: { status: 'CLOSED', endedAt: new Date() },
      });
      await prisma.vpnExit.update({
        where: { id: exit.id }, data: { openSessions: { decrement: 1 } },
      }).catch(() => {});
    }
    return ok({ closed: true, sessionId: session.id });
  } catch (e) {
    return err(e);
  }
}
