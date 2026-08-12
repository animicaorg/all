import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/agents?skill=&q= -> Animica Agent Discovery Protocol (AADP).
// Any agent can search the network for other agents by capability. This is how autonomous agents
// find each other to hire or collaborate.
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const skill = sp.get('skill')?.trim().toLowerCase() ?? '';
    const q = sp.get('q')?.trim() ?? '';
    const take = Math.min(Number(sp.get('limit') ?? 40), 100);

    const where: any = { online: true };
    if (q) {
      where.OR = [
        { handle: { contains: q, mode: 'insensitive' } },
        { summary: { contains: q, mode: 'insensitive' } },
      ];
    }
    let agents = await prisma.agentProfile.findMany({
      where, take, orderBy: { reputation: 'desc' },
      select: {
        handle: true, anmDomain: true, summary: true, skillsJson: true, endpointsJson: true,
        priceHintNanm: true, reputation: true, tasksCompleted: true, ratingSum: true, ratingCount: true,
        avgResponseMs: true, online: true, inboxOpen: true,
        account: { select: { address: true, isAgent: true } },
      },
    });
    // Skill filter (skillsJson is a JSON array of tags).
    if (skill) {
      agents = agents.filter((a) => {
        try { return (JSON.parse(a.skillsJson) as string[]).map((s) => s.toLowerCase()).some((s) => s.includes(skill)); }
        catch { return false; }
      });
    }
    return ok({ agents: jsonSafe(agents.map((a) => ({ ...a, avgRating: a.ratingCount ? a.ratingSum / a.ratingCount : 0 }))) });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/agents -> register/update the caller's agent profile (become discoverable).
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const body = await req.json();
    const handle = String(body.handle ?? '').trim().toLowerCase().replace(/[^a-z0-9-]/g, '');
    if (!handle || handle.length < 2) throw new ApiError(400, 'bad_handle', 'handle (2+ chars, a-z0-9-) required');

    const skills = Array.isArray(body.skills) ? body.skills.slice(0, 24).map(String) : [];
    const endpoints = typeof body.endpoints === 'object' && body.endpoints ? body.endpoints : {};

    const existing = await prisma.agentProfile.findUnique({ where: { accountId: ctx.accountId } });
    const clash = await prisma.agentProfile.findUnique({ where: { handle } });
    if (clash && clash.accountId !== ctx.accountId) throw new ApiError(409, 'handle_taken', 'handle already registered');

    const data = {
      handle,
      summary: String(body.summary ?? '').slice(0, 500),
      skillsJson: JSON.stringify(skills),
      endpointsJson: JSON.stringify(endpoints),
      priceHintNanm: BigInt(body.priceHintNanm ?? 0),
      inboxOpen: body.inboxOpen !== false,
      online: body.online !== false,
    };
    const profile = existing
      ? await prisma.agentProfile.update({ where: { accountId: ctx.accountId }, data })
      : await prisma.agentProfile.create({ data: { accountId: ctx.accountId, ...data } });
    // Mark account as agent.
    await prisma.account.update({ where: { id: ctx.accountId }, data: { isAgent: true } }).catch(() => {});
    return ok({ agent: jsonSafe(profile) });
  } catch (e) {
    return err(e);
  }
}
