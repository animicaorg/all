import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate, requireScope } from '@/lib/api';
import { requireWorkspaceRole, WS_MEMBER_SELECT } from '@/lib/workspaces';
import { getAccountPlan, planLimitError } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Workspace membership. Seats (team_members) are a PER-WORKSPACE limit on the workspace
// OWNER's plan — an invited ADMIN spends the owner's seats, not their own.

// GET /api/mkt/v1/workspaces/[id]/members -> member list, for any active member.
export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    await requireWorkspaceRole(params.id, ctx.accountId, 'MEMBER');
    const members = await prisma.workspaceMember.findMany({
      where: { workspaceId: params.id },
      orderBy: { createdAt: 'asc' },
      select: WS_MEMBER_SELECT,
    });
    return ok({ members });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/workspaces/[id]/members {address, role: 'ADMIN'|'MEMBER'} -> ADMIN+.
// The invitee must already have a wallet-keyed Account (that IS the identity system —
// there is nothing to invite by email). OWNER is never grantable here.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');
    await requireWorkspaceRole(params.id, ctx.accountId, 'ADMIN');
    let body: any = {};
    try { body = await req.json(); } catch {}
    const address = String(body?.address ?? '').trim().toLowerCase();
    if (!address) throw new ApiError(400, 'bad_request', 'address required');
    if (body?.role !== undefined && !['ADMIN', 'MEMBER'].includes(body.role)) {
      throw new ApiError(400, 'bad_request', 'role must be ADMIN | MEMBER');
    }
    const role = body?.role === 'ADMIN' ? 'ADMIN' : 'MEMBER';

    const account = await prisma.account.findUnique({ where: { address }, select: { id: true } });
    if (!account) {
      throw new ApiError(404, 'no_account', 'no such account; they must connect a wallet on animica.dev first');
    }

    const ws = await prisma.workspace.findUnique({ where: { id: params.id }, select: { ownerId: true } });
    if (!ws) throw new ApiError(404, 'not_found', 'workspace not found');
    const ownerPlan = await getAccountPlan(ws.ownerId);
    const limit = ownerPlan.limits.team_members;
    // Every non-OWNER row (active or shelved) holds a seat — otherwise adding while some
    // members are shelved would overshoot the limit the moment they restore.
    const used = await prisma.workspaceMember.count({ where: { workspaceId: params.id, role: { not: 'OWNER' } } });
    if (limit !== -1 && used >= limit) {
      throw planLimitError('team_members', {
        plan: ownerPlan.effectiveKey,
        limit,
        used,
        needed: used + 1,
        message:
          limit === 0
            ? 'Team members are available with Animica Pro.'
            : `This workspace has reached its ${ownerPlan.effectiveKey} team size (${limit}).`,
      });
    }

    try {
      const member = await prisma.workspaceMember.create({
        data: { workspaceId: params.id, accountId: account.id, role },
        select: WS_MEMBER_SELECT,
      });
      return ok({ member }, { status: 201 });
    } catch (e: any) {
      if (e?.code === 'P2002') throw new ApiError(409, 'already_member', 'that account is already a member');
      throw e;
    }
  } catch (e) {
    return err(e);
  }
}

// DELETE /api/mkt/v1/workspaces/[id]/members?memberId= -> remove a member.
// ADMIN+ removes anyone but the OWNER; any member (even a shelved one) may remove
// THEMSELVES (leave). The OWNER row is the workspace's anchor — delete the workspace instead.
export async function DELETE(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');
    const memberId = req.nextUrl.searchParams.get('memberId')?.trim() || '';
    if (!memberId) throw new ApiError(400, 'bad_request', 'memberId required');
    const row = await prisma.workspaceMember.findUnique({
      where: { id: memberId },
      select: { id: true, workspaceId: true, accountId: true, role: true },
    });
    if (!row || row.workspaceId !== params.id) throw new ApiError(404, 'not_found', 'member not found');
    // Authorization BEFORE the owner-row rule, so an outsider probing ids gets the same
    // 404 as for any workspace they cannot see.
    if (row.accountId !== ctx.accountId) await requireWorkspaceRole(params.id, ctx.accountId, 'ADMIN');
    if (row.role === 'OWNER') throw new ApiError(403, 'forbidden', 'the workspace owner cannot be removed');
    await prisma.workspaceMember.delete({ where: { id: row.id } });
    return ok({ removed: true });
  } catch (e) {
    return err(e);
  }
}
