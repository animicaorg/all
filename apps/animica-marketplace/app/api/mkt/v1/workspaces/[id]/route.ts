import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate, requireScope } from '@/lib/api';
import { requireWorkspaceRole, WS_MEMBER_SELECT } from '@/lib/workspaces';
import { getAccountPlan, planLimitError } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Single workspace: detail (any member), edits (ADMIN+), delete (OWNER).
// Branding + client separation are entitlements of the workspace OWNER — the paying
// account — never of the invited member doing the edit.

// GET /api/mkt/v1/workspaces/[id] -> detail incl. members, for any active member.
export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const me = await requireWorkspaceRole(params.id, ctx.accountId, 'MEMBER');
    const workspace = await prisma.workspace.findUnique({
      where: { id: params.id },
      select: {
        id: true, name: true, kind: true, ownerId: true,
        brandName: true, brandLogoUrl: true, brandColor: true, createdAt: true,
        owner: { select: { address: true, displayName: true } },
        members: { orderBy: { createdAt: 'asc' }, select: WS_MEMBER_SELECT },
        _count: { select: { workers: true } },
      },
    });
    if (!workspace) throw new ApiError(404, 'not_found', 'workspace not found');
    const { _count, ...ws } = workspace;
    return ok({ workspace: { ...ws, workers: _count.workers, myRole: me.role } });
  } catch (e) {
    return err(e);
  }
}

// PATCH /api/mkt/v1/workspaces/[id] {name?, kind?, brandName?, brandLogoUrl?, brandColor?}
// ADMIN+. Setting branding requires the OWNER's plan: brandName -> white_label,
// brandLogoUrl/brandColor -> custom_branding. Clearing (null/'') is always allowed so a
// downgraded owner's team can still remove stale branding.
export async function PATCH(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');
    await requireWorkspaceRole(params.id, ctx.accountId, 'ADMIN');
    const ws = await prisma.workspace.findUnique({ where: { id: params.id }, select: { id: true, ownerId: true } });
    if (!ws) throw new ApiError(404, 'not_found', 'workspace not found');
    let body: any = {};
    try { body = await req.json(); } catch {}

    const data: Record<string, unknown> = {};
    if (typeof body.name === 'string' && body.name.trim()) data.name = body.name.trim().slice(0, 80);
    if (body.kind !== undefined) {
      if (!['team', 'client'].includes(body.kind)) throw new ApiError(400, 'bad_request', 'kind must be team | client');
      data.kind = body.kind;
    }

    const settingBrandName = body.brandName !== undefined && body.brandName != null && body.brandName !== '';
    const settingBrandMedia =
      (body.brandLogoUrl !== undefined && body.brandLogoUrl != null && body.brandLogoUrl !== '') ||
      (body.brandColor !== undefined && body.brandColor != null && body.brandColor !== '');
    if (data.kind === 'client' || settingBrandName || settingBrandMedia) {
      const ownerPlan = await getAccountPlan(ws.ownerId);
      if (data.kind === 'client' && !ownerPlan.limits.reseller_features) {
        throw planLimitError('reseller_features', {
          plan: ownerPlan.effectiveKey, limit: false,
          message: 'Client workspaces are available with Animica Business.',
        });
      }
      if (settingBrandName && !ownerPlan.limits.white_label) {
        throw planLimitError('white_label', {
          plan: ownerPlan.effectiveKey, limit: false,
          message: 'White-label branding is available with Animica Operator.',
        });
      }
      if (settingBrandMedia && !ownerPlan.limits.custom_branding) {
        throw planLimitError('custom_branding', {
          plan: ownerPlan.effectiveKey, limit: false,
          message: 'Custom branding is available with Animica Operator.',
        });
      }
    }

    if (body.brandName !== undefined) {
      data.brandName = settingBrandName ? String(body.brandName).trim().slice(0, 80) : null;
    }
    if (body.brandLogoUrl !== undefined) {
      if (body.brandLogoUrl == null || body.brandLogoUrl === '') data.brandLogoUrl = null;
      else {
        const u = String(body.brandLogoUrl);
        if (!/^https:\/\//i.test(u) || u.length > 500) {
          throw new ApiError(400, 'bad_request', 'brandLogoUrl must be an https URL (max 500 chars)');
        }
        data.brandLogoUrl = u;
      }
    }
    if (body.brandColor !== undefined) {
      if (body.brandColor == null || body.brandColor === '') data.brandColor = null;
      else {
        const c = String(body.brandColor);
        if (!/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(c)) {
          throw new ApiError(400, 'bad_request', 'brandColor must be a hex color like #7c5cff');
        }
        data.brandColor = c;
      }
    }

    const workspace = await prisma.workspace.update({ where: { id: ws.id }, data });
    return ok({ workspace });
  } catch (e) {
    return err(e);
  }
}

// DELETE /api/mkt/v1/workspaces/[id] -> OWNER only. Workers survive (they belong to their
// account, the workspace is just a grouping) — detach, then delete; members cascade.
export async function DELETE(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');
    await requireWorkspaceRole(params.id, ctx.accountId, 'OWNER');
    await prisma.$transaction([
      prisma.worker.updateMany({ where: { workspaceId: params.id }, data: { workspaceId: null } }),
      prisma.workspace.delete({ where: { id: params.id } }),
    ]);
    return ok({ deleted: true });
  } catch (e) {
    return err(e);
  }
}
