import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, ApiError } from '@/lib/api';
import { dayKeyUtc, parseCaps, parseExpiry, parseNanm, parsePayees, str, validateSpendGrant } from '../apps/_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// The user's permission dashboard (§19, §20).
//
//   GET    /api/cloud/v1/grants          list my authorizations, with the subject resolved
//   POST   /api/cloud/v1/grants          create/update a grant for an app, agent or function
//   DELETE /api/cloud/v1/grants?id=...   revoke — takes effect on the very next host call,
//                                        because the executor re-reads the row at every spend

const SUBJECT_KINDS = ['app', 'agent', 'function'] as const;
type SubjectKind = (typeof SUBJECT_KINDS)[number];

async function resolveSubject(kind: SubjectKind, ref: string) {
  if (kind === 'app') {
    const app = await prisma.cloudApp.findFirst({
      where: { OR: [{ id: ref }, { slug: ref.toLowerCase() }], status: { not: 'ARCHIVED' } },
      select: { id: true, slug: true, name: true, capabilities: true, suspendedAt: true },
    });
    if (!app) throw new ApiError(404, 'not_found', 'no such app');
    if (app.suspendedAt) throw new ApiError(403, 'suspended', 'this app is suspended');
    return { id: app.id, slug: app.slug, name: app.name, capabilities: app.capabilities };
  }
  if (kind === 'agent') {
    const agent = await prisma.cloudAgent.findFirst({
      where: { OR: [{ id: ref }, { slug: ref.toLowerCase() }] },
      select: { id: true, slug: true, name: true, capabilities: true, status: true },
    });
    if (!agent) throw new ApiError(404, 'not_found', 'no such agent');
    if (agent.status === 'SUSPENDED') throw new ApiError(403, 'suspended', 'this agent is suspended');
    return { id: agent.id, slug: agent.slug, name: agent.name, capabilities: agent.capabilities };
  }
  const fn = await prisma.cloudFunction.findFirst({
    where: { id: ref, suspendedAt: null },
    select: { id: true, slug: true, name: true, capabilities: true },
  });
  if (!fn) throw new ApiError(404, 'not_found', 'no such function (pass the function id)');
  return { id: fn.id, slug: fn.slug, name: fn.name, capabilities: fn.capabilities };
}

function grantView(g: any, subject?: { slug: string; name: string } | null) {
  return {
    id: g.id,
    subjectKind: g.subjectKind,
    subjectId: g.subjectId,
    subject: subject ?? null,
    capabilities: g.capabilities,
    maxPerCallNanm: g.maxPerCallNanm,
    maxPerExecNanm: g.maxPerExecNanm,
    dailyCapNanm: g.dailyCapNanm,
    spentTodayNanm: g.spendDayKey === dayKeyUtc() ? g.spentTodayNanm : 0n,
    allowedPayees: g.allowedPayees,
    expiresAt: g.expiresAt,
    revoked: Boolean(g.revokedAt),
    revokedAt: g.revokedAt,
    active: !g.revokedAt && (!g.expiresAt || g.expiresAt > new Date()),
    createdAt: g.createdAt,
    updatedAt: g.updatedAt,
  };
}

export async function GET(req: NextRequest) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'read');

    const includeRevoked = req.nextUrl.searchParams.get('all') === '1';
    const grants = await prisma.cloudGrant.findMany({
      where: { accountId: auth.accountId, ...(includeRevoked ? {} : { revokedAt: null }) },
      orderBy: { updatedAt: 'desc' },
    });

    // Resolve subjects in a batch per kind so the dashboard can show names, not ids.
    const byKind = (k: string) => grants.filter((g) => g.subjectKind === k).map((g) => g.subjectId);
    const [apps, agents, fns] = await Promise.all([
      prisma.cloudApp.findMany({ where: { id: { in: byKind('app') } }, select: { id: true, slug: true, name: true } }),
      prisma.cloudAgent.findMany({ where: { id: { in: byKind('agent') } }, select: { id: true, slug: true, name: true } }),
      prisma.cloudFunction.findMany({ where: { id: { in: byKind('function') } }, select: { id: true, slug: true, name: true } }),
    ]);
    const lookup = new Map<string, { slug: string; name: string }>();
    for (const r of [...apps, ...agents, ...fns]) lookup.set(r.id, { slug: r.slug, name: r.name });

    return ok({
      grants: grants.map((g) => grantView(g, lookup.get(g.subjectId) ?? null)),
      count: grants.length,
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');

    const body = await req.json().catch(() => ({}));
    const kind = str(body?.subjectKind, 12).toLowerCase() as SubjectKind;
    if (!SUBJECT_KINDS.includes(kind)) {
      throw new ApiError(400, 'bad_request', `subjectKind must be ${SUBJECT_KINDS.join(' | ')}`);
    }
    const ref = str(body?.subjectId, 64);
    if (!ref) throw new ApiError(400, 'bad_request', 'subjectId is required (id, or slug for apps/agents)');
    const subject = await resolveSubject(kind, ref);

    const capabilities = parseCaps(body?.capabilities);
    if (capabilities.length === 0) throw new ApiError(400, 'bad_request', 'capabilities is required');
    const undeclared = capabilities.filter((c) => !subject.capabilities.includes(c));
    if (undeclared.length > 0) {
      throw new ApiError(
        400,
        'undeclared_capability',
        `this ${kind} does not declare: ${undeclared.join(', ')} — you can only authorize what it declares`,
      );
    }

    requireScope(auth, capabilities.includes('SPEND_ANM') ? 'buy' : 'use');

    const maxPerCallNanm = body?.maxPerCallNanm != null ? parseNanm(body.maxPerCallNanm, 'maxPerCallNanm') : 0n;
    const maxPerExecNanm = body?.maxPerExecNanm != null ? parseNanm(body.maxPerExecNanm, 'maxPerExecNanm') : 0n;
    const dailyCapNanm = body?.dailyCapNanm != null ? parseNanm(body.dailyCapNanm, 'dailyCapNanm') : 0n;
    const allowedPayees = parsePayees(body?.allowedPayees);
    const expiresAt = parseExpiry(body?.expiresAt);
    if (capabilities.includes('SPEND_ANM')) {
      validateSpendGrant({ maxPerCallNanm, maxPerExecNanm, dailyCapNanm, allowedPayees, expiresAt });
    }

    const grant = await prisma.cloudGrant.upsert({
      where: {
        accountId_subjectKind_subjectId: { accountId: auth.accountId, subjectKind: kind, subjectId: subject.id },
      },
      create: {
        accountId: auth.accountId,
        subjectKind: kind,
        subjectId: subject.id,
        capabilities,
        maxPerCallNanm,
        maxPerExecNanm,
        dailyCapNanm,
        allowedPayees,
        expiresAt,
      },
      update: {
        capabilities,
        maxPerCallNanm,
        maxPerExecNanm,
        dailyCapNanm,
        allowedPayees,
        expiresAt,
        revokedAt: null,
      },
    });

    return ok({ grant: grantView(grant, { slug: subject.slug, name: subject.name }) }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'use');

    let id = str(req.nextUrl.searchParams.get('id'), 64);
    if (!id) {
      const body = await req.json().catch(() => ({}));
      id = str(body?.id, 64);
    }
    if (!id) throw new ApiError(400, 'bad_request', 'pass the grant id (?id=... or {"id": ...})');

    const grant = await prisma.cloudGrant.findUnique({ where: { id } });
    if (!grant || grant.accountId !== auth.accountId) throw new ApiError(404, 'not_found', 'no such grant');

    const revoked = grant.revokedAt
      ? grant
      : await prisma.cloudGrant.update({ where: { id: grant.id }, data: { revokedAt: new Date() } });

    return ok({
      revoked: true,
      grant: grantView(revoked, null),
      note: 'revocation is immediate — the executor re-checks the grant on every sensitive host call',
    });
  } catch (e) {
    return err(e);
  }
}
