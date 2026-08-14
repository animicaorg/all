import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, ApiError } from '@/lib/api';
import { ANM_ADDR_RE, parseCaps, parseNanm, str } from '../../apps/_shared';
import { agentView, validateAgentBudget } from '../_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET    /api/cloud/v1/agents/[slug] — owner detail: budget, spend, recent executions.
// PATCH  /api/cloud/v1/agents/[slug] — pause/resume, budget edit, metadata, identity address.
// DELETE /api/cloud/v1/agents/[slug] — remove the agent. If it has execution history the row
// is DISABLED instead of deleted, so the economics of past runs stay attributable forever.

async function ownAgent(slug: string, accountId: string) {
  const agent = await prisma.cloudAgent.findUnique({
    where: { slug: slug.toLowerCase() },
    include: {
      function: { select: { id: true, slug: true, name: true, status: true, capabilities: true } },
      app: { select: { slug: true, name: true } },
    },
  });
  if (!agent || agent.ownerId !== accountId) throw new ApiError(404, 'not_found', 'no such agent');
  return agent;
}

export async function GET(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'read');
    const agent = await ownAgent(decodeURIComponent(ctx.params.slug).trim(), auth.accountId);

    const [recent, spendAgg] = await Promise.all([
      prisma.cloudExecution.findMany({
        where: { agentId: agent.id },
        orderBy: { createdAt: 'desc' },
        take: 10,
        select: {
          requestId: true,
          status: true,
          priceNanm: true,
          durationMs: true,
          errorCode: true,
          createdAt: true,
        },
      }),
      prisma.cloudExecution.aggregate({
        where: { agentId: agent.id, billed: true },
        _sum: { priceNanm: true },
        _count: { _all: true },
      }),
    ]);

    return ok({
      agent: agentView(agent as any),
      recentExecutions: recent,
      totals: { executions: spendAgg._count._all, spentNanm: spendAgg._sum.priceNanm ?? 0n },
    });
  } catch (e) {
    return err(e);
  }
}

export async function PATCH(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'publish');
    const agent = await ownAgent(decodeURIComponent(ctx.params.slug).trim(), auth.accountId);
    if (agent.status === 'SUSPENDED') {
      throw new ApiError(403, 'suspended', 'this agent was suspended by the platform — contact support');
    }

    const body = await req.json().catch(() => ({}));
    const data: any = {};

    if (body?.name != null) data.name = str(body.name, 80) || agent.name;
    if (body?.description != null) data.description = str(body.description, 2000);

    if (body?.status != null) {
      const s = str(body.status, 12).toUpperCase();
      if (!['ACTIVE', 'PAUSED'].includes(s)) {
        throw new ApiError(400, 'bad_request', 'status must be ACTIVE or PAUSED (DELETE removes the agent)');
      }
      if (s === 'ACTIVE' && agent.function.status !== 'PUBLISHED') {
        throw new ApiError(409, 'function_not_live', 'the agent cannot go ACTIVE while its function is not PUBLISHED');
      }
      data.status = s;
    }

    if (body?.capabilities != null) {
      const capabilities = parseCaps(body.capabilities);
      const beyond = capabilities.filter((c) => !agent.function.capabilities.includes(c));
      if (beyond.length > 0) {
        throw new ApiError(400, 'invalid_capability', `the function does not declare: ${beyond.join(', ')}`);
      }
      data.capabilities = capabilities;
    }

    if (body?.maxSpendPerRunNanm != null || body?.dailySpendCapNanm != null) {
      const perRun =
        body?.maxSpendPerRunNanm != null ? parseNanm(body.maxSpendPerRunNanm, 'maxSpendPerRunNanm') : agent.maxSpendPerRunNanm;
      const daily =
        body?.dailySpendCapNanm != null ? parseNanm(body.dailySpendCapNanm, 'dailySpendCapNanm') : agent.dailySpendCapNanm;
      validateAgentBudget(perRun, daily);
      data.maxSpendPerRunNanm = perRun;
      data.dailySpendCapNanm = daily;
    }

    if (body?.address !== undefined) {
      if (body.address === null || String(body.address).trim() === '') {
        data.address = null;
      } else {
        const address = String(body.address).trim();
        if (!ANM_ADDR_RE.test(address)) {
          throw new ApiError(400, 'invalid_address', 'address must be a native bech32m anim1... address');
        }
        data.address = address;
      }
    }

    if (Object.keys(data).length === 0) throw new ApiError(400, 'bad_request', 'nothing to update');

    try {
      const updated = await prisma.cloudAgent.update({
        where: { id: agent.id },
        data,
        include: {
          function: { select: { slug: true, name: true, status: true } },
          app: { select: { slug: true, name: true } },
        },
      });
      return ok({ agent: agentView(updated as any) });
    } catch (e: any) {
      if (e?.code === 'P2002') throw new ApiError(409, 'address_taken', 'that address already identifies another agent');
      throw e;
    }
  } catch (e) {
    return err(e);
  }
}

export async function DELETE(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'publish');
    const agent = await ownAgent(decodeURIComponent(ctx.params.slug).trim(), auth.accountId);

    const executions = await prisma.cloudExecution.count({ where: { agentId: agent.id } });
    if (executions > 0) {
      const disabled = await prisma.cloudAgent.update({ where: { id: agent.id }, data: { status: 'DISABLED' } });
      return ok({
        deleted: false,
        disabled: true,
        slug: disabled.slug,
        reason: `the agent has ${executions} recorded executions — it is disabled, not erased, so their accounting stays attributable`,
      });
    }
    await prisma.cloudAgent.delete({ where: { id: agent.id } });
    return ok({ deleted: true, disabled: false, slug: agent.slug });
  } catch (e) {
    return err(e);
  }
}
