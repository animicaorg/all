import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate, requireScope } from '@/lib/api';
import { getAccountPlan, requireEntitlement, planLimitError, billingHoldError } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Teams/workspaces (Pro+). A workspace groups Workers for a team or an agency client.
// Access is granted only by WorkspaceMember rows (lib/workspaces.ts) — the OWNER row is
// created with the workspace, so "mine" == my active memberships.

// GET /api/mkt/v1/workspaces -> workspaces I own or belong to, with my role.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const memberships = await prisma.workspaceMember.findMany({
      where: { accountId: ctx.accountId, status: 'active' },
      orderBy: { createdAt: 'asc' },
      select: {
        role: true,
        workspace: {
          select: {
            id: true, name: true, kind: true, ownerId: true,
            brandName: true, brandLogoUrl: true, brandColor: true, createdAt: true,
            owner: { select: { address: true, displayName: true } },
            _count: { select: { members: true, workers: true } },
          },
        },
      },
    });
    const workspaces = memberships.map((m) => ({
      id: m.workspace.id,
      name: m.workspace.name,
      kind: m.workspace.kind,
      role: m.role,
      owner: m.workspace.owner,
      isOwner: m.workspace.ownerId === ctx.accountId,
      members: m.workspace._count.members,
      workers: m.workspace._count.workers,
      brandName: m.workspace.brandName,
      brandLogoUrl: m.workspace.brandLogoUrl,
      brandColor: m.workspace.brandColor,
      createdAt: m.workspace.createdAt,
    }));
    return ok({ workspaces });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/workspaces {name, kind?} -> create (caller becomes OWNER).
// kind 'client' (agency client separation) additionally requires reseller_features.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');
    let body: any = {};
    try { body = await req.json(); } catch {}
    const name = String(body?.name ?? '').trim().slice(0, 80);
    if (!name) throw new ApiError(400, 'bad_request', 'name required');
    if (body?.kind !== undefined && !['team', 'client'].includes(body.kind)) {
      throw new ApiError(400, 'bad_request', 'kind must be team | client');
    }
    const kind = body?.kind === 'client' ? 'client' : 'team';

    const plan = await requireEntitlement(ctx.accountId, 'workspaces');
    if (plan.blockNewPaidResources) throw billingHoldError(plan.effectiveKey);
    const used = await prisma.workspace.count({ where: { ownerId: ctx.accountId } });
    const limit = plan.limits.workspaces;
    if (limit !== -1 && used >= limit) {
      throw planLimitError('workspaces', {
        plan: plan.effectiveKey,
        limit,
        used,
        needed: used + 1,
        message: `You’ve reached your ${plan.effectiveKey} workspace limit (${limit}).`,
      });
    }
    if (kind === 'client') await requireEntitlement(ctx.accountId, 'reseller_features');

    // Workspace + OWNER membership are one atomic unit — a workspace without its OWNER row
    // would be unreachable by everyone (access is membership-row-only).
    const workspace = await prisma.$transaction(async (tx) => {
      const ws = await tx.workspace.create({ data: { ownerId: ctx.accountId, name, kind } });
      await tx.workspaceMember.create({ data: { workspaceId: ws.id, accountId: ctx.accountId, role: 'OWNER' } });
      return ws;
    });
    return ok({ workspace: { ...workspace, role: 'OWNER' } }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
