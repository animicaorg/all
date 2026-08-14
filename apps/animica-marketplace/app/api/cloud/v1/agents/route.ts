import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, ApiError } from '@/lib/api';
import { requireSlot } from '@/lib/cloud/entitlements';
import { flags } from '@/lib/cloud/config';
import { ANM_ADDR_RE, parseCaps, parseNanm, parseSlug, requireStr, str } from '../apps/_shared';
import { agentView, validateAgentBudget } from './_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET  /api/cloud/v1/agents — list my agents (budgets, spend, run stats — all owner-private).
// POST /api/cloud/v1/agents — create an agent: a persistent, capability-bounded program bound
// to one deployed function, optionally with its OWN native anim1 identity it can be paid at.
// Budgets are enforced server-side at run time; the fields here only set the bounds.

export async function GET(req: NextRequest) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'read');

    const agents = await prisma.cloudAgent.findMany({
      where: { ownerId: auth.accountId },
      orderBy: { createdAt: 'desc' },
      include: {
        function: { select: { slug: true, name: true, status: true } },
        app: { select: { slug: true, name: true } },
      },
    });
    return ok({ agents: agents.map((a) => agentView(a as any)), count: agents.length });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    if (!flags.agents) throw new ApiError(503, 'disabled', 'agents are not enabled on this node');
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'publish');

    const body = await req.json().catch(() => ({}));
    const slug = parseSlug(body?.slug);
    const name = requireStr(body?.name, 'name', 80);
    const description = str(body?.description, 2000);

    const functionId = str(body?.functionId, 64);
    if (!functionId) throw new ApiError(400, 'bad_request', 'functionId is required — an agent runs one function');
    const fn = await prisma.cloudFunction.findUnique({
      where: { id: functionId },
      select: {
        id: true,
        ownerId: true,
        appId: true,
        slug: true,
        name: true,
        status: true,
        visibility: true,
        capabilities: true,
        suspendedAt: true,
      },
    });
    if (!fn || fn.suspendedAt) throw new ApiError(404, 'not_found', 'no such function');
    const mine = fn.ownerId === auth.accountId;
    if (!mine && !(fn.status === 'PUBLISHED' && fn.visibility === 'PUBLIC')) {
      throw new ApiError(404, 'not_found', 'no such function');
    }

    // An agent may not carry capabilities its function does not declare — the executor grants
    // the function's declared set, so anything broader here would be a lie in the UI.
    const capabilities = parseCaps(body?.capabilities);
    const beyond = capabilities.filter((c) => !fn.capabilities.includes(c));
    if (beyond.length > 0) {
      throw new ApiError(400, 'invalid_capability', `the function does not declare: ${beyond.join(', ')}`);
    }

    const maxSpendPerRunNanm = body?.maxSpendPerRunNanm != null ? parseNanm(body.maxSpendPerRunNanm, 'maxSpendPerRunNanm') : 0n;
    const dailySpendCapNanm = body?.dailySpendCapNanm != null ? parseNanm(body.dailySpendCapNanm, 'dailySpendCapNanm') : 0n;
    validateAgentBudget(maxSpendPerRunNanm, dailySpendCapNanm);

    let address: string | null = null;
    if (body?.address != null && String(body.address).trim() !== '') {
      address = String(body.address).trim();
      if (!ANM_ADDR_RE.test(address)) {
        throw new ApiError(400, 'invalid_address', 'address must be a native bech32m anim1... address');
      }
    }

    const current = await prisma.cloudAgent.count({ where: { ownerId: auth.accountId } });
    await requireSlot(auth.accountId, 'max_agents', current);

    try {
      const agent = await prisma.cloudAgent.create({
        data: {
          ownerId: auth.accountId,
          slug,
          name,
          description,
          functionId: fn.id,
          appId: fn.appId,
          address,
          capabilities,
          maxSpendPerRunNanm,
          dailySpendCapNanm,
          status: 'PAUSED',
        },
        include: {
          function: { select: { slug: true, name: true, status: true } },
          app: { select: { slug: true, name: true } },
        },
      });
      return ok(
        {
          agent: agentView(agent as any),
          next: `resume it with PATCH /api/cloud/v1/agents/${agent.slug} {"status":"ACTIVE"}, then POST .../run`,
        },
        { status: 201 },
      );
    } catch (e: any) {
      if (e?.code === 'P2002') {
        const target = String(e?.meta?.target ?? '');
        if (target.includes('address')) throw new ApiError(409, 'address_taken', 'that address already identifies another agent');
        throw new ApiError(409, 'slug_taken', `an agent named "${slug}" already exists`);
      }
      throw e;
    }
  } catch (e) {
    return err(e);
  }
}
